"""Tests for repeated-run calibration (Phase 9): statistics, comparability, instability, safety.

All records here are SYNTHETIC (``observability.synthetic``); no live model is involved.
"""

import copy
import math
import statistics

import pytest

from ai_quality_lab.observability.calibration import (
    CalibrationError,
    analyze_runs,
    check_runs,
    select_runs,
    t_critical,
    wilson_interval,
)
from ai_quality_lab.observability.records import metric, short_hash, validate_record
from ai_quality_lab.observability.regression import DEFAULT_RULES, load_rules
from ai_quality_lab.observability.synthetic import example_batch, synthetic_run

RULES = load_rules(DEFAULT_RULES)


def analyze(runs, purpose="official"):
    return analyze_runs(runs, purpose, RULES)


def summary(analysis, name):
    return next(s for s in analysis["metrics"] if s["metric"] == name)


def case(analysis, case_id):
    return next(c for c in analysis["cases"] if c["id"] == case_id)


# --- Known-answer statistics --------------------------------------------------------------------


@pytest.mark.parametrize("k, n, low, high", [(0, 5, 0.0, 0.4345), (5, 5, 0.5655, 1.0),
                                             (2, 5, 0.1176, 0.7693), (1, 19, 0.0094, 0.2464)])
def test_wilson_interval_known_values(k, n, low, high):
    assert [round(x, 4) for x in wilson_interval(k, n)] == [low, high]


def test_wilson_interval_rejects_invalid_proportions():
    for k, n in ((1, 0), (6, 5), (-1, 5)):
        with pytest.raises(ValueError):
            wilson_interval(k, n)


@pytest.mark.parametrize("df, t", [(1, 12.706), (4, 2.776), (22, 2.086), (40, 1.959964)])
def test_t_critical_values(df, t):
    assert t_critical(df) == pytest.approx(t)  # df 22 uses df 20 (wider, conservative)


def test_run_level_statistics_match_hand_calculation():
    analysis = analyze(example_batch())
    s = summary(analysis, "pass_rate")
    values = [21 / 25, 20 / 25, 21 / 25, 20 / 25, 20 / 25]
    assert s["values"] == [round(v, 4) for v in values]
    assert s["numerators"] == [21, 20, 21, 20, 20] and s["denominator"] == 25
    assert s["mean"] == round(statistics.fmean(values), 4) == 0.816
    assert s["median"] == 0.8 and (s["min"], s["max"], s["range"]) == (0.8, 0.84, 0.04)
    sd = statistics.stdev(values)
    assert s["sd"] == round(sd, 4)
    half = 2.776 * sd / math.sqrt(5)
    assert s["mean_ci95"] == [round(0.816 - half, 4), round(0.816 + half, 4)]
    assert "df=4" in s["ci_method"]


def test_no_variation_gives_no_confidence_interval():
    runs = [synthetic_run(f"r{i}") for i in range(5)]
    s = summary(analyze(runs), "pass_rate")
    assert s["sd"] == 0.0 and s["mean_ci95"] is None and "not informative" in s["ci_method"]


def test_single_run_has_no_spread_statistics_and_no_tolerance():
    analysis = analyze([synthetic_run("only")])
    assert "sd" not in summary(analysis, "pass_rate")
    proposal = next(t for t in analysis["tolerance_proposals"] if t["metric"] == "pass_rate")
    assert proposal["proposed"] is None and "insufficient sample" in proposal["basis"]


def test_fewer_than_five_runs_never_get_a_tolerance_proposal():
    analysis = analyze(example_batch()[:4])
    proposals = {t["metric"]: t for t in analysis["tolerance_proposals"]}
    assert proposals["pass_rate"]["proposed"] is None
    assert any("Fewer than 5 runs" in text for text in analysis["limitations"])


# --- Denominators and missing observations ------------------------------------------------------


def test_unequal_denominators_are_never_averaged():
    runs = example_batch()
    all_but_last = [c["id"] for c in runs[0]["cases"]][:-1]  # same config, one case missing
    shorter = synthetic_run("short", case_ids=all_but_last)
    s = summary(analyze([*runs[:4], shorter]), "pass_rate")
    assert s["status"] == "incomparable" and "denominator differs" in s["note"]
    assert "mean" not in s


def test_missing_observations_are_not_imputed():
    runs = example_batch()
    del runs[2]["metrics"]["doc_mrr"]
    runs[2]["unmeasured"]["doc_mrr"] = "index unavailable"
    s = summary(analyze(runs), "doc_mrr")
    assert s["status"] == "incomplete" and s["missing_runs"] == ["synthetic-run-3"]
    assert s["missing_reasons"] == ["index unavailable"] and "mean" not in s


# --- Comparability and selection ----------------------------------------------------------------


def test_incomparable_configuration_is_rejected():
    runs = example_batch()
    other = synthetic_run("other-model", identity={**runs[0]["identity"],
                                                   "model_digests": {"m": "1" * 64}})
    with pytest.raises(CalibrationError, match="identity differs in \\['model_digests'\\]"):
        analyze([*runs[:4], other])


def test_ollama_version_change_is_not_comparable():
    runs = example_batch()
    upgraded = synthetic_run("upgraded", identity={**runs[0]["identity"],
                                                   "ollama_version": "0.0.1-synthetic"})
    assert any("ollama_version" in p for p in check_runs([*runs[:4], upgraded], "official"))


def test_official_batch_may_not_vary_the_seed_but_exploratory_may():
    official = [synthetic_run(f"o{i}", seed=42 + i) for i in range(5)]
    assert any("only an exploratory batch may vary the seed" in p
               for p in check_runs(official, "official"))
    exploratory = [synthetic_run(f"e{i}", seed=i, purpose="exploratory", batch="explore")
                   for i in range(5)]
    analysis = analyze(exploratory, "exploratory")
    assert analysis["label"].startswith("SYNTHETIC")  # synthetic wins over exploratory
    assert analysis["varied"] == ["0", "1", "2", "3", "4"]
    assert "seed" not in analysis["identity"]["generation"]


def test_official_and_exploratory_runs_are_never_mixed():
    runs = example_batch()
    runs[0]["source"]["purpose"] = "exploratory"
    assert any("purpose 'official'" in p for p in check_runs(runs, "official"))
    assert any("must not include official runs" in p for p in check_runs(runs, "exploratory"))


def test_duplicate_selection_and_duplicate_reports_are_rejected():
    runs = example_batch()
    with pytest.raises(CalibrationError, match="more than once"):
        select_runs(runs, ["synthetic-run-1", "synthetic-run-1"])
    with pytest.raises(CalibrationError, match="not in the history"):
        select_runs(runs, ["nope"])
    with pytest.raises(CalibrationError, match="either explicit run IDs or one batch"):
        select_runs(runs, ["synthetic-run-1"], "synthetic")
    copy_of_run = copy.deepcopy(runs[0])
    copy_of_run["run_id"] = "same-report-again"
    assert any("recorded more than once" in p for p in check_runs([*runs, copy_of_run],
                                                                   "official"))


def test_batch_selection_returns_only_that_batch():
    runs = [*example_batch(), synthetic_run("elsewhere", batch="other")]
    assert [r["run_id"] for r in select_runs(runs, batch="other")] == ["elsewhere"]
    with pytest.raises(CalibrationError, match="no runs recorded with batch"):
        select_runs(runs, batch="missing")


def test_errored_or_invalid_runs_and_mixed_origins_are_rejected():
    runs = example_batch()
    runs[1]["status"] = "ERROR"
    assert any("status ERROR" in p for p in check_runs(runs, "official"))
    real = example_batch()
    real[0]["source"]["origin"] = "local"
    assert any("synthetic and real runs are mixed" in p for p in check_runs(real, "official"))
    broken = example_batch()
    broken[0]["metrics"]["pass_rate"] = metric(0.5, numerator=1, denominator=4)
    assert any("invalid record" in p for p in check_runs(broken, "official"))


# --- Per-case consistency and safety ------------------------------------------------------------


def test_per_case_patterns_and_intervals():
    analysis = analyze(example_batch())
    assert analysis["unstable_cases"] == ["RAG-012", "RAG-016"]
    assert case(analysis, "RAG-012")["outcome_pattern"] == "intermittent"
    assert case(analysis, "RAG-012")["fail_probability_ci95"] == [0.1176, 0.7693]
    assert case(analysis, "RAG-007")["outcome_pattern"] == "consistent_fail"
    assert case(analysis, "RAG-007")["abstention_pattern"] == "always"
    assert case(analysis, "RAG-016")["abstention_pattern"] == "intermittent"
    assert case(analysis, "RAG-001")["outcome_pattern"] == "consistent_pass"


def test_intermittent_critical_safety_failure_blocks_despite_good_averages():
    runs = [synthetic_run(f"r{i}") for i in range(9)]
    runs.append(synthetic_run("bad", safety=["RAG-002"]))  # critical case, 1 of 10 runs
    analysis = analyze(runs)
    assert analysis["verdict"] == "BLOCKING"
    assert "bad: critical case RAG-002 FAIL" in analysis["blocking"]
    assert "bad: safety failure in case RAG-002" in analysis["blocking"]
    assert summary(analysis, "safety_failures")["mean"] == 0.1  # the average looks harmless
    assert case(analysis, "RAG-002")["outcome_pattern"] == "intermittent"


def test_critical_metrics_are_never_given_a_tolerance():
    analysis = analyze([*example_batch(), synthetic_run("r6", safety=["RAG-003"])])
    proposals = {t["metric"]: t for t in analysis["tolerance_proposals"]}
    for name in ("critical_failures", "safety_failures", "error_count", "restricted_retrievals"):
        assert proposals[name]["proposed"] == 0 and "never tolerated" in proposals[name]["basis"]


def test_tolerance_proposal_is_the_observed_range():
    proposals = {t["metric"]: t for t in analyze(example_batch())["tolerance_proposals"]}
    assert proposals["pass_rate"]["proposed"] == 0.04
    assert proposals["pass_rate"]["one_case_unit"] == 0.04
    assert proposals["pass_rate"]["current_live_tolerance"] == 0.04
    assert proposals["high_failures"]["proposed"] == 1
    assert proposals["over_abstention_rate"]["proposed"] == 0.0527


def test_zero_variation_proposes_zero_and_states_the_bound():
    analysis = analyze([synthetic_run(f"r{i}") for i in range(5)])
    proposal = next(t for t in analysis["tolerance_proposals"] if t["metric"] == "pass_rate")
    assert proposal["proposed"] == 0 and "no variation was observed" in proposal["basis"]
    assert any("0.4345" in text and "does not show the system is deterministic" in text
               for text in analysis["limitations"])


def test_analysis_never_changes_the_rules_file():
    before = DEFAULT_RULES.read_bytes()
    analyze(example_batch())
    assert DEFAULT_RULES.read_bytes() == before


def test_synthetic_records_are_valid_and_labelled():
    for run in example_batch():
        assert validate_record(run) == [] and run["source"]["origin"] == "synthetic"
    assert analyze(example_batch())["label"] == "SYNTHETIC: not live evidence"
    assert short_hash(example_batch()[0]["identity"]) == example_batch()[0]["comparability_key"]
