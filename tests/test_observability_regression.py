"""Tests for regression detection: comparability, tolerances, actions, run-level outcomes."""

import pytest

from ai_quality_lab.observability.records import metric
from ai_quality_lab.observability.regression import (
    DEFAULT_RULES,
    RulesError,
    compare_records,
    compare_run,
    load_rules,
    parse_rules,
)

RULES = load_rules(DEFAULT_RULES)


def m(value, num=None, den=None, kind="deterministic"):
    from ai_quality_lab.observability.records import MetricKind

    return metric(value, MetricKind(kind), num, den)


def pair(make_record, base_metrics, cur_metrics, **current):
    base = make_record(run_id="base", metrics=base_metrics)
    cur = make_record(run_id="cur", metrics=cur_metrics, **current)
    return compare_records(base, cur, RULES)


def actions(comparison):
    return [(f.action, f.metric) for f in comparison.findings]


def test_identical_runs_pass(make_record):
    record = make_record()
    assert compare_records(record, make_record(run_id="x"), RULES).status == "PASS"


def test_pass_rate_decline_blocks_with_full_evidence(make_record):
    result = pair(make_record, {"pass_rate": m(1.0, 4, 4)}, {"pass_rate": m(0.75, 3, 4)})
    assert result.status == "BLOCKED"
    finding = result.findings[0]
    assert (finding.metric, finding.baseline, finding.current, finding.baseline_denominator,
            finding.current_denominator, finding.tolerance, finding.change) == (
        "pass_rate", 1.0, 0.75, 4, 4, 0.0, -0.25)
    assert "tolerance" in finding.reason


def test_improvement_is_not_a_regression(make_record):
    result = pair(make_record, {"pass_rate": m(0.75, 3, 4)}, {"pass_rate": m(1.0, 4, 4)})
    assert result.status == "PASS" and result.findings == []


@pytest.mark.parametrize("current, status", [(0.72, "PASS"), (0.7199, "BLOCKED")])
def test_live_tolerance_boundary(make_record, current, status):
    # Live runs tolerate a pass-rate drop of exactly 0.04 (1 of 25); more is a regression.
    base = make_record(run_id="b", metrics={"pass_rate": m(0.76, 7600, 10000)},
                       deterministic=False)
    cur = make_record(run_id="c", metrics={"pass_rate": m(current, round(current * 10000), 10000)},
                      deterministic=False)
    assert compare_records(base, cur, RULES).status == status


def test_deterministic_runs_have_zero_tolerance(make_record):
    result = pair(make_record, {"pass_rate": m(0.76, 7600, 10000)},
                  {"pass_rate": m(0.7599, 7599, 10000)})
    assert result.status == "BLOCKED"


def test_new_critical_failure_blocks_even_if_counts_are_unchanged(make_record):
    base_cases = [{"id": "A", "outcome": "FAIL", "severity": "critical"},
                  {"id": "B", "outcome": "PASS", "severity": "critical"}]
    swapped = [{"id": "A", "outcome": "PASS", "severity": "critical"},
               {"id": "B", "outcome": "FAIL", "severity": "critical"}]
    metrics = {"critical_failures": m(1, 1, 2)}
    base = make_record(run_id="b", metrics=metrics, cases=base_cases)
    cur = make_record(run_id="c", metrics=metrics, cases=swapped)
    result = compare_records(base, cur, RULES)
    assert result.status == "BLOCKED"
    assert any("new critical failure: B" in f.reason for f in result.findings)


def test_retrieval_gate_metrics_block_and_diagnostics_only_investigate(make_record):
    gate = pair(make_record, {"doc_recall": m(0.92, den=19)}, {"doc_recall": m(0.90, den=19)})
    assert gate.status == "BLOCKED"
    diagnostic = pair(make_record, {"doc_mrr": m(0.87, den=19)}, {"doc_mrr": m(0.80, den=19)})
    assert diagnostic.status == "PASS" and actions(diagnostic) == [("investigate", "doc_mrr")]


def test_increased_over_abstention_blocks_hedging_only_investigates(make_record):
    over = pair(make_record, {"over_abstention_rate": m(0.3158, 6, 19)},
                {"over_abstention_rate": m(0.3684, 7, 19)})
    assert over.status == "BLOCKED"
    hedged = pair(make_record, {"answerable_abstention_rate": m(0.3158, 6, 19)},
                  {"answerable_abstention_rate": m(0.3684, 7, 19)})
    assert hedged.status == "PASS" and actions(hedged) == [("investigate",
                                                            "answerable_abstention_rate")]


def test_reduced_test_count_and_unexpected_skips_block(make_record):
    tests = pair(make_record, {"test_count": m(589)}, {"test_count": m(580)})
    assert tests.status == "BLOCKED"
    skips = pair(make_record, {"unexpected_skips": m(0, 0, 589)},
                 {"unexpected_skips": m(1, 1, 589)})
    assert skips.status == "BLOCKED"


def test_dataset_change_is_not_comparable_and_shrinking_is_flagged(make_record):
    base = make_record(run_id="b", metrics={"pass_rate": m(0.75, 3, 4)})
    smaller = make_record(run_id="c", metrics={"pass_rate": m(1.0, 2, 2)},
                          identity={"dataset_fingerprint": "d2"},
                          cases=[{"id": "C-0", "outcome": "PASS", "severity": "critical"},
                                 {"id": "C-1", "outcome": "PASS", "severity": "critical"}])
    result = compare_records(base, smaller, RULES)
    assert result.status == "ERROR" and not result.comparable
    reasons = " ".join(f.reason for f in result.findings)
    assert "dataset_fingerprint changed" in reasons
    assert "dataset shrank from 4 to 2 cases" in reasons and "artificially better" in reasons
    # The higher pass rate on the smaller dataset is never reported as an improvement or a pass.
    assert all(f.metric != "pass_rate" for f in result.findings)


def test_different_model_configuration_is_not_comparable(make_record):
    base = make_record(run_id="b", evaluation_type="rag_live",
                       identity={"llm": "ollama:qwen2.5:3b"})
    cur = make_record(run_id="c", evaluation_type="rag_live", identity={"llm": "ollama:llama3"})
    result = compare_records(base, cur, RULES)
    assert result.status == "ERROR" and "llm changed" in result.findings[0].reason


def test_different_evaluation_types_are_not_compared(make_record):
    result = compare_records(make_record(), make_record(evaluation_type="promptfoo"), RULES)
    assert result.status == "ERROR"


def test_parameter_changes_are_reported_and_compared(make_record):
    base = make_record(run_id="b", metrics={"pass_rate": m(1.0, 4, 4)})
    cur = make_record(run_id="c", metrics={"pass_rate": m(0.5, 2, 4)},
                      parameters={"simulated_failure": "guardrail_fail_open"})
    result = compare_records(base, cur, RULES)
    assert result.comparable and result.status == "BLOCKED"
    assert result.parameter_changes == {"simulated_failure": ["none", "guardrail_fail_open"]}


def test_metric_missing_in_current_run_is_an_error_not_unchanged(make_record):
    result = pair(make_record, {"pass_rate": m(1.0, 4, 4), "doc_recall": m(0.9, den=19)},
                  {"pass_rate": m(1.0, 4, 4)}, unmeasured={"doc_recall": "index failed"})
    assert result.status == "ERROR"
    assert "index failed" in result.findings[0].reason


def test_newly_measured_metric_is_not_a_finding(make_record):
    result = pair(make_record, {"pass_rate": m(1.0, 4, 4)},
                  {"pass_rate": m(1.0, 4, 4), "doc_recall": m(0.9, den=19)})
    assert result.status == "PASS"


def test_advisory_judge_regression_never_blocks_even_if_a_rule_says_block(make_record):
    blocking_rules = parse_rules({"ragas_faithfulness": {
        "direction": "higher_is_better", "tolerance": 0.0, "action": "block"}})
    base = make_record(run_id="b", metrics={"ragas_faithfulness": m(0.9, den=4, kind="advisory")})
    cur = make_record(run_id="c", metrics={"ragas_faithfulness": m(0.1, den=4, kind="advisory")})
    result = compare_records(base, cur, blocking_rules)
    assert result.status == "PASS"
    assert actions(result) == [("advisory", "ragas_faithfulness")]
    assert "never release-blocking" in result.findings[0].reason


def test_invalid_baseline_or_errored_current_run_is_an_error(make_record):
    corrupt = make_record(run_id="b")
    corrupt["comparability_key"] = "tampered"
    assert compare_records(corrupt, make_record(), RULES).status == "ERROR"
    assert compare_records(make_record(run_id="b"), make_record(status="ERROR"),
                           RULES).status == "ERROR"


def test_metric_kind_change_is_an_error(make_record):
    result = pair(make_record, {"pass_rate": m(1.0, 4, 4)},
                  {"pass_rate": m(1.0, 4, 4, kind="advisory")})
    assert result.status == "ERROR"


# --- Whole run ----------------------------------------------------------------------------------


def test_run_comparison_outcomes(make_record):
    base = {"custom_engine": make_record(run_id="b")}
    ok = compare_run([make_record(run_id="c")], base, ["custom_engine"], RULES)
    assert ok.status == "PASS"
    data = ok.to_dict()
    assert (data["total"], data["errored"], data["release_readiness"]) == (1, 0, "READY")

    worse = make_record(run_id="c", metrics={"pass_rate": m(0.5, 2, 4),
                                             "critical_failures": m(0, 0, 2)})
    blocked = compare_run([worse], base, ["custom_engine"], RULES).to_dict()
    assert (blocked["status"], blocked["release_readiness"], blocked["errored"]) == (
        "BLOCKED", "BLOCKED", 0)


@pytest.mark.parametrize(
    "records, baselines, problem",
    [
        ([], "ok", "no evaluation record"),
        (["c", "d"], "ok", "expected exactly one"),
        (["c"], None, "no approved baseline"),
        (["c"], ValueError("manifest corrupt"), "manifest corrupt"),
    ],
)
def test_missing_records_or_baselines_are_errors(make_record, records, baselines, problem):
    current = [make_record(run_id=r) for r in records]
    if baselines == "ok":
        baselines = make_record(run_id="b")
    result = compare_run(current, {"custom_engine": baselines}, ["custom_engine"], RULES)
    assert result.status == "ERROR"
    assert problem in result.problems[0]
    assert result.to_dict()["errored"] >= 1


def test_no_requested_types_is_an_error():
    assert compare_run([], {}, [], RULES).status == "ERROR"


# --- Rules --------------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw",
    [{"x": {"direction": "up", "tolerance": 0, "action": "block"}},
     {"x": {"direction": "higher_is_better", "tolerance": -1, "action": "block"}},
     {"x": {"direction": "higher_is_better", "tolerance": 0, "action": "ignore"}},
     {"x": {"direction": "higher_is_better", "action": "block"}}],
)
def test_invalid_rules_are_rejected(raw):
    with pytest.raises(RulesError):
        parse_rules(raw)


def test_unreadable_rules_file_is_an_error(tmp_path):
    with pytest.raises(RulesError):
        load_rules(tmp_path / "missing.json")
    bad = tmp_path / "rules.json"
    bad.write_text('{"schema_version": 2, "rules": {}}', encoding="utf-8")
    with pytest.raises(RulesError):
        load_rules(bad)


def test_repository_rules_cover_the_required_metrics():
    required = {"pass_rate", "critical_failures", "high_failures", "medium_failures",
                "safety_failures", "doc_hit_rate", "doc_recall", "doc_precision", "doc_mrr",
                "doc_ndcg", "over_abstention_rate", "answerable_abstention_rate", "case_count",
                "test_count", "unexpected_skips"}
    assert required <= set(RULES)
    for name in ("critical_failures", "safety_failures", "test_count", "unexpected_skips",
                 "doc_hit_rate", "doc_recall"):
        assert RULES[name].action == "block" and RULES[name].tolerance == 0
    assert all(rule.action != "block" for name, rule in RULES.items() if name.startswith("ragas"))


# --- Phase 8.1 remediation ----------------------------------------------------------------------
# D-8.2: expected skips do not lower the pass rate; real test problems still block.

from pathlib import Path  # noqa: E402

from ai_quality_lab.observability.baselines import load_baseline  # noqa: E402
from ai_quality_lab.observability.records import build_pytest, short_hash  # noqa: E402
from ai_quality_lab.observability.regression import select_records  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def pytest_record(make_record, run_id, tests, passed, failures=0, skipped=12, unexpected=0):
    audit = {"tests": tests, "passed": passed, "failures": failures, "errors": 0,
             "skipped": skipped, "allowed_skips": skipped - unexpected,
             "unexpected_skips": [{"test": "t", "reason": "r"}] * unexpected}
    parts = build_pytest(audit)
    record = make_record(evaluation_type="pytest", run_id=run_id, metrics=parts["metrics"],
                         unmeasured=parts["unmeasured"], cases=[], status=parts["status"],
                         identity={"suite": "offline pytest"}, parameters={})
    record["identity"].pop("dataset_fingerprint")
    record["comparability_key"] = short_hash(record["identity"])
    record["dataset"]["case_count"] = None
    return record


@pytest.mark.parametrize(
    "current, status, metric_name",
    [
        (dict(tests=704, passed=691, skipped=13), "PASS", None),     # +1 opt-in live test
        (dict(tests=704, passed=691, skipped=13, unexpected=1), "BLOCKED", "unexpected_skips"),
        (dict(tests=703, passed=690, failures=1), "BLOCKED", "tests_failed"),
        (dict(tests=703, passed=690, skipped=13), "BLOCKED", "executed_tests"),  # 1 test now skips
    ],
)
def test_pytest_regressions_use_executed_tests(make_record, current, status, metric_name):
    base = pytest_record(make_record, "base", tests=703, passed=691)
    result = compare_records(base, pytest_record(make_record, "cur", **current), RULES)
    assert result.status == status
    if metric_name:
        assert metric_name in {f.metric for f in result.findings}


def test_legacy_pytest_baseline_is_still_comparable_without_false_findings(make_record):
    # The committed baseline predates D-8.2 (test_pass_rate counted skips; no executed_tests).
    legacy = load_baseline(ROOT / "reports" / "baselines", "pytest")
    assert "test_pass_rate" in legacy["metrics"] and "executed_tests" not in legacy["metrics"]
    current = pytest_record(make_record, "cur", tests=704, passed=691, skipped=13)
    result = compare_records(legacy, current, RULES)
    assert result.status == "PASS" and result.findings == []
    assert "test_pass_rate" not in RULES  # the legacy metric is never compared


# D-8.4: choosing runs from a multi-run history.


def history_of(make_record):
    return [make_record(run_id="ce-1", recorded_at="2026-11-01T10:00:00Z"),
            make_record(run_id="ce-2", recorded_at="2026-11-02T10:00:00Z"),
            make_record(evaluation_type="promptfoo", run_id="pf-1")]


def test_default_selection_with_several_runs_of_a_type_is_ambiguous(make_record):
    records, problems, _ = select_records(history_of(make_record), ["custom_engine"])
    run = compare_run(records, {"custom_engine": make_record(run_id="b")}, ["custom_engine"],
                      RULES)
    assert problems == [] and run.status == "ERROR" and "expected exactly one" in run.problems[0]


def test_explicit_run_id_selection(make_record):
    records, problems, notes = select_records(history_of(make_record), ["custom_engine"],
                                              run_ids=["ce-1"])
    assert [r["run_id"] for r in records] == ["ce-1"] and problems == []
    assert notes == {"custom_engine": "ce-1 (selected with --run-id)"}


def test_latest_selection_names_the_run_it_chose(make_record):
    records, problems, notes = select_records(history_of(make_record),
                                              ["custom_engine", "promptfoo"], latest=True)
    assert sorted(r["run_id"] for r in records) == ["ce-2", "pf-1"] and problems == []
    assert notes["custom_engine"].startswith("ce-2 (latest of 2 custom_engine run(s)")


@pytest.mark.parametrize(
    "kwargs, problem",
    [
        ({"run_ids": ["nope"]}, "not in the history"),
        ({"run_ids": ["ce-1", "ce-2"]}, "several runs selected"),
        ({"run_ids": ["ce-1", "ce-1"]}, "more than once"),
        ({"run_ids": ["pf-1"]}, "not requested"),
        ({"run_ids": ["ce-1"], "latest": True}, "either --run-id or --latest"),
    ],
)
def test_invalid_selections_are_errors(make_record, kwargs, problem):
    _, problems, _ = select_records(history_of(make_record), ["custom_engine"], **kwargs)
    assert any(problem in p for p in problems), problems


def test_latest_tie_is_ambiguous_not_resolved_by_file_order(make_record):
    tied = [make_record(run_id="a"), make_record(run_id="b")]  # same recorded_at
    records, problems, _ = select_records(tied, ["custom_engine"], latest=True)
    assert records == [] and "share the latest time" in problems[0]


def test_selected_run_still_needs_to_be_comparable(make_record):
    other = make_record(run_id="new", identity={"dataset_fingerprint": "d2"})
    records, problems, _ = select_records([other], ["custom_engine"], run_ids=["new"])
    run = compare_run(records, {"custom_engine": make_record(run_id="b")}, ["custom_engine"],
                      RULES)
    assert problems == [] and run.status == "ERROR"
    assert run.comparisons[0].comparable is False
