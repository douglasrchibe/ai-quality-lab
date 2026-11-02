"""Repeated-run calibration for nondeterministic (live) evaluations. Phase 9.

Answers, for an explicitly selected set of comparable runs: how much does each metric vary from
run to run, which cases are unstable, did any run contain a critical or safety failure, and what
regression tolerance does the observed variation support?

Statistical choices (see docs/live-calibration.md):
* Repeated runs ask the SAME questions, so they are not independent trials: case results are never
  pooled into one binomial sample. Run-level statistics use n = number of runs; case-level
  statistics use n = runs of that case.
* Run-level spread: mean, median, min, max, range, sample standard deviation, and a t-interval for
  the mean (df = n - 1; Student t, because n is small and the population SD is unknown). It assumes
  runs are exchangeable and is approximate: with n = 5 it is wide on purpose.
* Case-level: a Wilson score interval for each case's failure probability (good coverage for small
  n and for 0/n or n/n, unlike the normal approximation).
* Missing observations are never imputed: a metric missing from any run, or measured over a
  different denominator in some run, is not aggregated and is reported as such.
* Critical and safety failures are listed per run and per case and make the analysis BLOCKING,
  whatever the averages are. They never receive a tolerance.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Mapping, Sequence
from typing import Any

from ai_quality_lab.observability.records import metric_spec, utc_now, validate_record
from ai_quality_lab.observability.regression import Rule

MIN_RUNS_FOR_STATISTICS = 2
MIN_RUNS_FOR_TOLERANCE = 5
NEVER_TOLERATED = ("critical_failures", "error_count", "safety_failures", "restricted_retrievals")
PURPOSES = ("official", "exploratory")
VARIABLE_IN_EXPLORATORY = ("seed",)  # the only identity part an exploratory batch may vary
Z_95 = 1.959964
T_975 = {1: 12.706, 2: 4.303, 3: 3.182, 4: 2.776, 5: 2.571, 6: 2.447, 7: 2.365, 8: 2.306,
         9: 2.262, 10: 2.228, 11: 2.201, 12: 2.179, 13: 2.160, 14: 2.145, 15: 2.131, 16: 2.120,
         17: 2.110, 18: 2.101, 19: 2.093, 20: 2.086, 25: 2.060, 30: 2.042}
"""Two-sided 95% Student t critical values (standard tables). Between listed df, the next lower df
is used, which is conservative (a wider interval)."""


class CalibrationError(ValueError):
    """The selected runs cannot be analysed together reliably."""


def t_critical(df: int) -> float:
    if df < 1:
        raise ValueError("t-interval needs at least 2 runs")
    return T_975[max(k for k in T_975 if k <= df)] if df <= 30 else Z_95


def wilson_interval(successes: int, trials: int, z: float = Z_95) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion (95% by default)."""
    if trials <= 0 or not 0 <= successes <= trials:
        raise ValueError(f"invalid proportion {successes}/{trials}")
    p = successes / trials
    denominator = 1 + z * z / trials
    centre = (p + z * z / (2 * trials)) / denominator
    half = z * math.sqrt(p * (1 - p) / trials + z * z / (4 * trials * trials)) / denominator
    return max(0.0, centre - half), min(1.0, centre + half)


# --- Selecting and checking runs ----------------------------------------------------------------


def select_runs(history: Sequence[Mapping[str, Any]], run_ids: Sequence[str] = (),
                batch: str | None = None) -> list[Mapping[str, Any]]:
    """The runs to analyse: exactly the given run IDs, or every run of one batch (in file order)."""
    if bool(run_ids) == bool(batch):
        raise CalibrationError("select runs with either explicit run IDs or one batch label")
    if run_ids:
        if len(set(run_ids)) != len(run_ids):
            raise CalibrationError("the same run ID was selected more than once")
        by_id = {r["run_id"]: r for r in history}
        missing = [i for i in run_ids if i not in by_id]
        if missing:
            raise CalibrationError(f"run ID(s) not in the history: {missing}")
        return [by_id[i] for i in run_ids]
    runs = [r for r in history if r["source"].get("batch") == batch]
    if not runs:
        raise CalibrationError(f"no runs recorded with batch {batch!r}")
    return runs


def _identity_without(identity: Mapping[str, Any], varied: Sequence[str]) -> dict[str, Any]:
    reduced = dict(identity)
    if "seed" in varied and isinstance(reduced.get("generation"), dict):
        reduced["generation"] = {k: v for k, v in reduced["generation"].items() if k != "seed"}
    return reduced


def check_runs(runs: Sequence[Mapping[str, Any]], purpose: str) -> list[str]:
    """Every reason the runs cannot be analysed together (empty = they can)."""
    if purpose not in PURPOSES:
        return [f"purpose must be one of {PURPOSES}"]
    problems = []
    for run in runs:
        errors = validate_record(run)
        if errors:
            problems.append(f"{run.get('run_id')}: invalid record: {'; '.join(errors)}")
    if problems or not runs:
        return problems or ["no runs selected"]
    ids = [r["run_id"] for r in runs]
    if len(set(ids)) != len(ids):
        problems.append("duplicate run IDs in the selection")
    hashes = [r["source"].get("report_sha256") for r in runs]
    duplicated = sorted({h for h in hashes if h and hashes.count(h) > 1})
    if duplicated:
        problems.append(f"the same source report was recorded more than once "
                        f"(report_sha256 {duplicated}); a duplicate would count one run twice")
    types = {r["evaluation_type"] for r in runs}
    if len(types) != 1:
        problems.append(f"runs of different evaluation types {sorted(types)}")
    errored = [r["run_id"] for r in runs if r["status"] == "ERROR"]
    if errored:
        problems.append(f"run(s) with status ERROR (incomplete): {errored}")
    origins = {r["source"].get("origin") == "synthetic" for r in runs}
    if len(origins) > 1:
        problems.append("synthetic and real runs are mixed")
    purposes = {r["source"].get("purpose") for r in runs}
    if purpose == "official" and purposes != {"official"}:
        problems.append(f"an official calibration needs runs recorded with purpose 'official'; "
                        f"found {sorted(map(str, purposes))}")
    if purpose == "exploratory" and "official" in purposes:
        problems.append("an exploratory analysis must not include official runs")
    varied = VARIABLE_IN_EXPLORATORY if purpose == "exploratory" else ()
    keys = {repr(sorted(_identity_without(r["identity"], varied).items())) for r in runs}
    if len(keys) > 1:
        fields = sorted({k for r in runs for k in r["identity"]
                         if len({repr(x["identity"].get(k)) for x in runs}) > 1})
        problems.append(f"runs are not comparable: identity differs in {fields}"
                        + ("" if varied else " (only an exploratory batch may vary the seed)"))
    return problems


# --- Analysis -----------------------------------------------------------------------------------


def _metric_summary(name: str, runs: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    spec = metric_spec(name)
    unit = spec[0].value if spec else "unknown"
    present = [r["metrics"][name] for r in runs if name in r["metrics"]]
    summary: dict[str, Any] = {"metric": name, "unit": unit,
                               "kind": present[0]["kind"] if present else None,
                               "runs_observed": len(present), "runs_total": len(runs)}
    if len(present) < len(runs):
        absent = [r["run_id"] for r in runs if name not in r["metrics"]]
        reasons = sorted({r["unmeasured"].get(name, "not reported") for r in runs
                          if name not in r["metrics"]})
        summary.update(status="incomplete", missing_runs=absent, missing_reasons=reasons,
                       note="not aggregated: missing observations are never imputed")
        return summary
    denominators = sorted({m["denominator"] for m in present}, key=str)
    summary["denominator"] = denominators[0] if len(denominators) == 1 else denominators
    if len(denominators) > 1:
        summary.update(status="incomparable",
                       note="not aggregated: the denominator differs between runs")
        return summary
    values = [m["value"] for m in present]
    summary.update(status="ok", values=values,
                   numerators=[m["numerator"] for m in present],
                   mean=round(statistics.fmean(values), 4),
                   median=round(statistics.median(values), 4),
                   min=min(values), max=max(values), range=round(max(values) - min(values), 4))
    if len(values) >= MIN_RUNS_FOR_STATISTICS and statistics.stdev(values) == 0:
        # A zero-width interval would claim certainty that n identical runs cannot give.
        summary.update(sd=0.0, mean_ci95=None,
                       ci_method=f"not informative: no variation observed in {len(values)} runs")
    elif len(values) >= MIN_RUNS_FOR_STATISTICS:
        sd = statistics.stdev(values)
        half = t_critical(len(values) - 1) * sd / math.sqrt(len(values))
        low, high = summary["mean"] - half, summary["mean"] + half
        if unit in ("rate", "score"):
            low, high = max(0.0, low), min(1.0, high)
        if unit == "count":
            low = max(0.0, low)
        summary.update(sd=round(sd, 4), mean_ci95=[round(low, 4), round(high, 4)],
                       ci_method=f"Student t, df={len(values) - 1} (approximate; runs assumed "
                                 "exchangeable)")
    return summary


def _case_consistency(runs: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    by_case: dict[str, list[Mapping[str, Any]]] = {}
    for run in runs:
        for case in run["cases"]:
            by_case.setdefault(case["id"], []).append({**case, "run_id": run["run_id"]})
    rows = []
    for case_id in sorted(by_case):
        seen = by_case[case_id]
        n = len(seen)
        failed = sum(1 for c in seen if c["outcome"] != "PASS")
        low, high = wilson_interval(failed, n)
        row: dict[str, Any] = {
            "id": case_id, "severity": seen[0]["severity"], "runs": n,
            "fail": failed, "errors": sum(1 for c in seen if c["outcome"] == "ERROR"),
            "outcome_pattern": ("consistent_pass" if failed == 0 else
                                "consistent_fail" if failed == n else "intermittent"),
            "fail_probability_ci95": [round(low, 4), round(high, 4)],
            "missing_runs": len(runs) - n,
        }
        if all("abstained" in c for c in seen) and seen[0].get("answerable"):
            abstained = sum(1 for c in seen if c["abstained"])
            row["abstained"] = abstained
            row["abstention_pattern"] = ("never" if abstained == 0 else
                                         "always" if abstained == n else "intermittent")
        if all("safety_failure" in c for c in seen):
            row["safety_failures"] = sum(1 for c in seen if c["safety_failure"])
        rows.append(row)
    return rows


def _blocking(runs: Sequence[Mapping[str, Any]]) -> list[str]:
    """Critical or safety failures in ANY run: individually listed, never averaged away."""
    items = []
    for run in runs:
        for case in run["cases"]:
            if case["severity"] == "critical" and case["outcome"] != "PASS":
                items.append(f"{run['run_id']}: critical case {case['id']} {case['outcome']}")
            if case.get("safety_failure"):
                items.append(f"{run['run_id']}: safety failure in case {case['id']}")
        for name in ("critical_failures", "safety_failures", "restricted_retrievals"):
            m = run["metrics"].get(name)
            if m and m["value"]:
                items.append(f"{run['run_id']}: {name} = {m['value']}")
    return list(dict.fromkeys(items))


def _tolerance(summary: Mapping[str, Any], rule: Rule | None, n_runs: int) -> dict[str, Any]:
    name = summary["metric"]
    proposal: dict[str, Any] = {"metric": name,
                                "current_live_tolerance": rule.live_tolerance if rule else None,
                                "current_tolerance": rule.tolerance if rule else None}
    if name in NEVER_TOLERATED:
        return {**proposal, "proposed": 0,
                "basis": "never tolerated: critical, error, safety and restricted-content "
                         "failures block at any count"}
    if summary.get("status") != "ok":
        return {**proposal, "proposed": None, "basis": f"no proposal: {summary.get('note')}"}
    if n_runs < MIN_RUNS_FOR_TOLERANCE:
        return {**proposal, "proposed": None,
                "basis": f"insufficient sample: {n_runs} run(s); at least "
                         f"{MIN_RUNS_FOR_TOLERANCE} are needed for a proposal"}
    one_case = (round(1 / summary["denominator"], 4)
                if summary["unit"] == "rate" and summary["denominator"] else
                1 if summary["unit"] == "count" else None)
    observed = summary["range"]
    basis = (f"observed run-to-run range over {n_runs} runs (largest difference between any two "
             "comparable runs)")
    if observed == 0:
        basis += ("; no variation was observed, so a tolerance of 0 is proposed. Unseen "
                  "nondeterminism would then cause a BLOCK, which is the safe direction; "
                  "collect more runs before relaxing it")
    return {**proposal, "proposed": observed, "one_case_unit": one_case, "basis": basis}


def analyze_runs(runs: Sequence[Mapping[str, Any]], purpose: str,
                 rules: Mapping[str, Rule]) -> dict[str, Any]:
    """The full calibration analysis. Raises ``CalibrationError`` when the runs can't be combined."""
    problems = check_runs(runs, purpose)
    if problems:
        raise CalibrationError("; ".join(problems))
    n = len(runs)
    synthetic = runs[0]["source"].get("origin") == "synthetic"
    label = ("SYNTHETIC: not live evidence" if synthetic else
             "OFFICIAL calibration" if purpose == "official" else
             "EXPLORATORY: excluded from baselines and official tolerances")
    names = sorted({name for r in runs for name in r["metrics"]})
    metrics = [_metric_summary(name, runs) for name in names]
    cases = _case_consistency(runs)
    blocking = _blocking(runs)
    tolerances = [_tolerance(s, rules.get(s["metric"]), n) for s in metrics
                  if s["kind"] == "deterministic" and s["metric"] in rules]
    pass_rate = next((s for s in metrics if s["metric"] == "pass_rate"
                      and s.get("status") == "ok"), None)
    expected_sd = None
    if pass_rate and pass_rate["denominator"]:
        # If cases varied independently with the observed per-case failure frequencies, the
        # run-level pass-rate SD would be about sqrt(sum p(1-p)) / N. A cross-check on the observed
        # SD, not a separate estimate.
        p = [c["fail"] / c["runs"] for c in cases if c["runs"]]
        expected_sd = round(math.sqrt(sum(q * (1 - q) for q in p)) / pass_rate["denominator"], 4)
    limitations = [
        f"Sample size n = {n} run(s). Results are indicative, not statistically conclusive; "
        "confidence intervals at this n are wide.",
        "Runs repeat the same cases, so they are not independent trials; no pooled binomial "
        "interval is reported.",
        "A tolerance proposal is evidence for the owner's review. It does not change "
        "ci/regression_rules.json.",
    ]
    if n < MIN_RUNS_FOR_TOLERANCE:
        limitations.append(f"Fewer than {MIN_RUNS_FOR_TOLERANCE} runs: no tolerance is proposed.")
    if not any(c["outcome_pattern"] == "intermittent" for c in cases):
        upper = round(wilson_interval(0, n)[1], 4)
        limitations.append(
            f"No case changed outcome across the {n} runs. That bounds each case's chance of "
            f"changing in a run at no more than about {upper} (95% Wilson upper bound for 0/{n}); "
            "it does not show the system is deterministic.")
    return {
        "schema_version": 1, "generated_at": utc_now(), "label": label, "purpose": purpose,
        "synthetic": synthetic, "evaluation_type": runs[0]["evaluation_type"], "n_runs": n,
        "run_ids": [r["run_id"] for r in runs],
        "batches": sorted({str(r["source"].get("batch")) for r in runs}),
        "identity": runs[0]["identity"] if purpose == "official" else
        _identity_without(runs[0]["identity"], VARIABLE_IN_EXPLORATORY),
        "varied": [] if purpose == "official" else
        sorted({str((r["identity"].get("generation") or {}).get("seed")) for r in runs}),
        "commits": sorted({str(r["source"].get("commit")) for r in runs}),
        "clean_provenance": all(r["source"].get("commit") and
                                r["source"].get("uncommitted_changes") is False for r in runs),
        "statuses": {r["run_id"]: r["status"] for r in runs},
        "blocking": blocking, "verdict": "BLOCKING" if blocking else "NO CRITICAL OR SAFETY FAILURE",
        "metrics": metrics, "cases": cases,
        "unstable_cases": [c["id"] for c in cases if c["outcome_pattern"] == "intermittent"
                           or c.get("abstention_pattern") == "intermittent"],
        "expected_pass_rate_sd_from_cases": expected_sd,
        "tolerance_proposals": tolerances, "limitations": limitations,
    }
