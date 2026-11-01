"""Deterministic regression detection between evaluation records.

Two records are comparable only if their ``identity`` matches (evaluation type, dataset and corpus
fingerprints, model, embedder). Otherwise the comparison is ERROR with an explanation; coverage
(case count) is still checked so a shrinking dataset is always flagged. ``parameters`` such as
``top_k`` or ``simulated_failure`` may differ: they are reported as changes, and their effect shows
up in the metrics (this is how a configuration or seeded-defect regression is detected).

Outcome: PASS (no regression), BLOCKED (a release-blocking regression), ERROR (the comparison
could not be performed reliably). ERROR wins over BLOCKED. Advisory metrics never block.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from ai_quality_lab.observability.records import MetricKind, validate_record

DEFAULT_RULES = Path("ci") / "regression_rules.json"
ACTIONS = ("block", "investigate")
DIRECTIONS = ("higher_is_better", "lower_is_better")
EPSILON = 1e-9


class RulesError(ValueError):
    """The regression rules file is missing or invalid."""


@dataclass(frozen=True)
class Rule:
    metric: str
    direction: str
    tolerance: float
    action: str
    live_tolerance: float | None = None
    why: str = ""

    def tolerance_for(self, deterministic: bool) -> float:
        return self.tolerance if deterministic or self.live_tolerance is None \
            else self.live_tolerance


def load_rules(path: str | Path = DEFAULT_RULES) -> dict[str, Rule]:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        raw_rules = data["rules"]
        if data.get("schema_version") != 1 or not isinstance(raw_rules, dict):
            raise ValueError("expected schema_version 1 and a 'rules' object")
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise RulesError(f"regression rules {path} are unreadable: {exc}") from None
    return parse_rules(raw_rules)


def parse_rules(raw_rules: Mapping[str, Any]) -> dict[str, Rule]:
    rules = {}
    for name, raw in raw_rules.items():
        try:
            rule = Rule(metric=name, direction=raw["direction"], tolerance=raw["tolerance"],
                        action=raw["action"], live_tolerance=raw.get("live_tolerance"),
                        why=raw.get("why", ""))
        except (KeyError, TypeError) as exc:
            raise RulesError(f"rule {name!r} is incomplete: {exc}") from None
        tolerances = [rule.tolerance] + ([rule.live_tolerance] if rule.live_tolerance is not None
                                         else [])
        if (rule.direction not in DIRECTIONS or rule.action not in ACTIONS
                or any(isinstance(t, bool) or not isinstance(t, (int, float)) or t < 0
                       for t in tolerances)):
            raise RulesError(f"rule {name!r} is invalid: direction must be one of {DIRECTIONS}, "
                             f"action one of {ACTIONS}, tolerances non-negative numbers")
        rules[name] = rule
    return rules


@dataclass(frozen=True)
class Finding:
    """One observation. ``action``: error | block | investigate | advisory."""

    action: str
    reason: str
    metric: str | None = None
    baseline: float | int | None = None
    current: float | int | None = None
    baseline_denominator: int | None = None
    current_denominator: int | None = None
    tolerance: float | None = None
    change: float | None = None
    rule: str | None = None


@dataclass
class Comparison:
    evaluation_type: str
    baseline_run_id: str | None
    current_run_id: str | None
    comparable: bool
    findings: list[Finding] = field(default_factory=list)
    parameter_changes: dict[str, list[Any]] = field(default_factory=dict)

    @property
    def status(self) -> str:
        actions = {f.action for f in self.findings}
        if "error" in actions:
            return "ERROR"
        return "BLOCKED" if "block" in actions else "PASS"

    def to_dict(self) -> dict[str, Any]:
        return {"evaluation_type": self.evaluation_type, "status": self.status,
                "baseline_run_id": self.baseline_run_id, "current_run_id": self.current_run_id,
                "comparable": self.comparable, "parameter_changes": self.parameter_changes,
                "findings": [asdict(f) for f in self.findings]}


def compare_records(baseline: Mapping[str, Any], current: Mapping[str, Any],
                    rules: Mapping[str, Rule]) -> Comparison:
    """Compare a current record with its baseline (or the previous comparable run)."""
    comparison = Comparison(
        evaluation_type=str(current.get("evaluation_type") if isinstance(current, Mapping)
                            else "?"),
        baseline_run_id=baseline.get("run_id") if isinstance(baseline, Mapping) else None,
        current_run_id=current.get("run_id") if isinstance(current, Mapping) else None,
        comparable=False)
    add = comparison.findings.append
    for label, record in (("baseline", baseline), ("current", current)):
        errors = validate_record(record)
        if errors:
            add(Finding("error", f"{label} record is invalid: " + "; ".join(errors)))
    if comparison.findings:
        return comparison
    if baseline["evaluation_type"] != current["evaluation_type"]:
        add(Finding("error", f"cannot compare {current['evaluation_type']} with a "
                             f"{baseline['evaluation_type']} baseline"))
        return comparison
    if current["status"] == "ERROR":
        add(Finding("error", "the current run's own status is ERROR: its results are incomplete"))

    _check_coverage(baseline, current, add)
    differing = sorted(k for k in set(baseline["identity"]) | set(current["identity"])
                       if baseline["identity"].get(k) != current["identity"].get(k))
    for key in differing:
        add(Finding("error", f"not comparable: {key} changed from "
                             f"{baseline['identity'].get(key)!r} to "
                             f"{current['identity'].get(key)!r}. Metrics from different datasets "
                             "or model configurations are not equivalent; approve a new baseline "
                             "explicitly if the change is intended."))
    if differing:
        return comparison
    comparison.comparable = True
    comparison.parameter_changes = {
        k: [baseline["parameters"].get(k), current["parameters"].get(k)]
        for k in sorted(set(baseline["parameters"]) | set(current["parameters"]))
        if baseline["parameters"].get(k) != current["parameters"].get(k)}
    deterministic = baseline["deterministic"] and current["deterministic"]
    for name, rule in rules.items():
        finding = _compare_metric(name, rule, baseline, current, deterministic)
        if finding:
            add(finding)
    _new_critical_failures(baseline, current, add)
    return comparison


def _check_coverage(baseline: Mapping[str, Any], current: Mapping[str, Any], add) -> None:
    """Shrinking coverage is flagged even when the datasets differ (where metrics are skipped)."""
    before, after = baseline["dataset"].get("case_count"), current["dataset"].get("case_count")
    if isinstance(before, int) and isinstance(after, int) and after < before:
        add(Finding("block", f"dataset shrank from {before} to {after} cases: rates over fewer "
                             "cases are not comparable and may look artificially better",
                    metric="case_count", baseline=before, current=after, change=after - before,
                    rule="coverage"))


def _compare_metric(name: str, rule: Rule, baseline: Mapping[str, Any],
                    current: Mapping[str, Any], deterministic: bool) -> Finding | None:
    base, cur = baseline["metrics"].get(name), current["metrics"].get(name)
    if base is None and cur is None:
        return None
    if base is None:
        return None  # newly measured: nothing to compare against yet (shown in trends)
    if cur is None:
        reason = current["unmeasured"].get(name, "not reported")
        return Finding("error", f"{name} was measured in the baseline but not in the current run "
                                f"({reason}); missing data is never treated as unchanged or zero",
                       metric=name, baseline=base["value"],
                       baseline_denominator=base["denominator"])
    if base["kind"] != cur["kind"]:
        return Finding("error", f"{name} changed kind ({base['kind']} -> {cur['kind']})",
                       metric=name)
    change = round(cur["value"] - base["value"], 6)
    worse = change < 0 if rule.direction == "higher_is_better" else change > 0
    tolerance = rule.tolerance_for(deterministic)
    if not worse or abs(change) <= tolerance + EPSILON:
        return None
    advisory = cur["kind"] == MetricKind.ADVISORY.value
    action = "advisory" if advisory else rule.action  # judge scores never block
    reason = (f"{name} got worse: {base['value']} -> {cur['value']} (change {change:+g}, "
              f"tolerance {tolerance:g}, {rule.direction.replace('_', ' ')})")
    if advisory:
        reason += "; advisory LLM-judge metric, never release-blocking"
    return Finding(action, reason, metric=name, baseline=base["value"], current=cur["value"],
                   baseline_denominator=base["denominator"],
                   current_denominator=cur["denominator"], tolerance=tolerance, change=change,
                   rule=name)


def _new_critical_failures(baseline: Mapping[str, Any], current: Mapping[str, Any], add) -> None:
    before = {c["id"]: c["outcome"] for c in baseline["cases"]}
    for case in current["cases"]:
        if (case["severity"] == "critical" and case["outcome"] != "PASS"
                and before.get(case["id"]) == "PASS"):
            add(Finding("block", f"new critical failure: {case['id']} was PASS in the baseline, "
                                 f"now {case['outcome']}", metric="critical_case",
                        rule="new_critical_failure"))


# --- Whole run against approved baselines -------------------------------------------------------


@dataclass
class RunComparison:
    comparisons: list[Comparison] = field(default_factory=list)
    problems: list[str] = field(default_factory=list)  # missing records or baselines: ERROR
    selection: dict[str, str] = field(default_factory=dict)  # type -> which run and why

    @property
    def status(self) -> str:
        statuses = {c.status for c in self.comparisons}
        if self.problems or "ERROR" in statuses or not self.comparisons:
            return "ERROR"
        return "BLOCKED" if "BLOCKED" in statuses else "PASS"

    def to_dict(self) -> dict[str, Any]:
        status = self.status
        # total / errored / release_readiness let `ci_check --report` confirm the exit code.
        return {"status": status,
                "release_readiness": "READY" if status == "PASS" else "BLOCKED",
                "total": len(self.comparisons),
                "errored": sum(1 for c in self.comparisons if c.status == "ERROR")
                + len(self.problems),
                "problems": self.problems, "selection": self.selection,
                "comparisons": [c.to_dict() for c in self.comparisons]}


def select_records(history: Sequence[Mapping[str, Any]], types: Sequence[str],
                   run_ids: Sequence[str] = (), latest: bool = False
                   ) -> tuple[list[Mapping[str, Any]], list[str], dict[str, str]]:
    """Choose which records of a (possibly multi-run) history to compare.

    * default: the history must hold exactly one record per type (CI: one fresh history per run);
      several records of a type are left for ``compare_run`` to report as ambiguous (ERROR).
    * ``run_ids``: exactly these runs. Every ID must exist, at most one per type, and every
      requested type must be covered; otherwise ERROR.
    * ``latest``: per type, the record with the latest ``recorded_at``; a tie is ambiguous (ERROR),
      never resolved by file order.
    Returns (records, problems, selection notes). Comparability is still checked afterwards.
    """
    problems: list[str] = []
    notes: dict[str, str] = {}
    if run_ids and latest:
        return [], ["choose either --run-id or --latest, not both"], notes
    if run_ids:
        by_id = {r["run_id"]: r for r in history}
        unknown = [i for i in run_ids if i not in by_id]
        if unknown:
            problems.append(f"run id(s) not in the history: {unknown}")
        if len(set(run_ids)) != len(run_ids):
            problems.append("the same run id was given more than once")
        chosen = [by_id[i] for i in dict.fromkeys(run_ids) if i in by_id]
        for etype in types:
            picked = [r["run_id"] for r in chosen if r["evaluation_type"] == etype]
            if len(picked) > 1:
                problems.append(f"{etype}: several runs selected {picked}; select one per type")
            elif picked:
                notes[etype] = f"{picked[0]} (selected with --run-id)"
        extra = sorted({r["evaluation_type"] for r in chosen} - set(types))
        if extra:
            problems.append(f"selected run(s) of type(s) {extra} that were not requested in "
                            "--types")
        return chosen, problems, notes
    if latest:
        chosen = []
        for etype in types:
            records = [r for r in history if r["evaluation_type"] == etype]
            if not records:
                continue  # compare_run reports the missing record
            newest = max(r["recorded_at"] for r in records)
            tied = [r for r in records if r["recorded_at"] == newest]
            if len(tied) > 1:
                problems.append(f"{etype}: {len(tied)} runs share the latest time {newest} "
                                f"({[r['run_id'] for r in tied]}); select one with --run-id")
                continue
            chosen.append(tied[0])
            notes[etype] = (f"{tied[0]['run_id']} (latest of {len(records)} {etype} run(s), "
                            f"recorded {newest})")
        return chosen, problems, notes
    return [r for r in history if r["evaluation_type"] in types], problems, notes


def compare_run(current: Sequence[Mapping[str, Any]], baselines: Mapping[str, Any],
                required_types: Sequence[str], rules: Mapping[str, Rule]) -> RunComparison:
    """Compare one run's records (one per type) with the approved baselines.

    ``baselines`` maps type -> record, or type -> an exception explaining why it is unavailable.
    """
    run = RunComparison()
    by_type: dict[str, list[Mapping[str, Any]]] = {}
    for record in current:
        by_type.setdefault(str(record.get("evaluation_type")), []).append(record)
    if not required_types:
        run.problems.append("no evaluation types were requested")
    for etype in required_types:
        records = by_type.get(etype, [])
        baseline = baselines.get(etype)
        if not records:
            run.problems.append(f"{etype}: no evaluation record for this run (missing report or "
                                "record step failed)")
            continue
        if len(records) > 1:
            run.problems.append(f"{etype}: {len(records)} records in this run; expected exactly "
                                "one, so the comparison would be ambiguous")
            continue
        if baseline is None or isinstance(baseline, Exception):
            reason = baseline if isinstance(baseline, Exception) else "no approved baseline"
            run.problems.append(f"{etype}: baseline unavailable: {reason}")
            continue
        run.comparisons.append(compare_records(baseline, records[0], rules))
    return run
