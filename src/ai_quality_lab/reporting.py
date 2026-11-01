"""Markdown quality reports for quality observability (Phase 8).

Plain Markdown tables only, so the same report works in a GitHub Actions job summary, in VS Code,
and as a downloaded artifact. Every rate is shown with its numerator/denominator (or ``n=`` for
averages); unmeasured metrics are shown as "not measured" with the reason, never as 0. Advisory
(LLM-judge) metrics are labelled as advisory everywhere they appear.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ai_quality_lab.observability.regression import Rule, compare_records

TREND_COLUMNS = {
    "custom_engine": ("case_count", "pass_rate", "critical_failures", "high_failures",
                      "safety_failures", "error_count"),
    "promptfoo": ("case_count", "pass_rate", "critical_failures", "high_failures",
                  "safety_failures", "error_count"),
    "rag_retrieval": ("case_count", "pass_rate", "doc_hit_rate", "doc_recall", "doc_mrr",
                      "doc_ndcg", "restricted_retrievals"),
    "rag_mock": ("case_count", "pass_rate", "critical_failures", "safety_failures", "doc_recall",
                 "over_abstention_rate", "answerable_abstention_rate"),
    "rag_live": ("case_count", "pass_rate", "critical_failures", "safety_failures", "doc_recall",
                 "over_abstention_rate", "answerable_abstention_rate"),
    "pytest": ("test_count", "test_pass_rate", "tests_failed", "expected_skips",
               "unexpected_skips"),
    "ragas_advisory": ("ragas_faithfulness", "ragas_answer_relevancy", "ragas_context_precision",
                       "ragas_context_recall"),
}
ICONS = {"PASS": "✅", "BLOCKED": "❌", "ERROR": "⚠️"}
LIMITATIONS = (
    "Deterministic CI records use stand-ins (mock chatbot, hashing embeddings, mock RAG models). "
    "They show regressions in the tested scenarios, not live-model quality.",
    "Live-model records (rag_live) exist only when a live evaluation is run locally and recorded; "
    "they are not produced in CI.",
    "Advisory LLM-judge metrics (Ragas) are reported for context only. The local judge is "
    "unreliable on real answers (D-6.2), so they never block a release.",
    "Abstention is detected with the project's fixed 'not found' phrases; a paraphrased "
    "abstention would be missed.",
    "Runs are compared only when dataset, knowledge base, model and embedder match; other runs "
    "are listed separately, never combined.",
)


def fmt_metric(m: Mapping[str, Any] | None, unmeasured_reason: str | None = None) -> str:
    if m is None:
        return f"not measured ({unmeasured_reason})" if unmeasured_reason else "—"
    value, num, den = m["value"], m["numerator"], m["denominator"]
    text = f"{value:g}" if isinstance(value, float) else str(value)
    if num is not None and den is not None:
        text += f" ({num}/{den})"
    elif den is not None:
        text += f" (n={den})"
    if m["kind"] == "advisory":
        text += " *advisory*"
    return text


def _short(sha: Any) -> str:
    return str(sha)[:7] if sha else "unknown"


def _cell(record: Mapping[str, Any], name: str) -> str:
    m = record["metrics"].get(name)
    if m is None:
        return "not measured" if name in record["unmeasured"] else "—"
    return fmt_metric(m)


def render_comparison(comparison: Mapping[str, Any]) -> list[str]:
    """Lines for one Comparison.to_dict()."""
    lines = [f"### {comparison['evaluation_type']}: {ICONS[comparison['status']]} "
             f"{comparison['status']}", "",
             f"Baseline `{comparison['baseline_run_id']}` vs current "
             f"`{comparison['current_run_id']}`."]
    if comparison["parameter_changes"]:
        changes = ", ".join(f"`{k}`: {v[0]!r} → {v[1]!r}"
                            for k, v in comparison["parameter_changes"].items())
        lines.append(f"Parameter changes: {changes}.")
    findings = comparison["findings"]
    if not findings:
        lines += ["", "No regression beyond tolerance."]
        return lines + [""]
    lines += ["", "| Action | Metric | Baseline | Current | Denominators (base → current) | "
                  "Tolerance | Reason |", "|---|---|---|---|---|---|---|"]
    for f in findings:
        dens = (f"{f['baseline_denominator']} → {f['current_denominator']}"
                if f["baseline_denominator"] is not None or f["current_denominator"] is not None
                else "")
        lines.append(f"| {f['action'].upper()} | {f['metric'] or ''} | "
                     f"{'' if f['baseline'] is None else f['baseline']} | "
                     f"{'' if f['current'] is None else f['current']} | {dens} | "
                     f"{'' if f['tolerance'] is None else f['tolerance']} | {f['reason']} |")
    return lines + [""]


def render_run_comparison(run: Mapping[str, Any], title: str = "Regression vs approved baseline"
                          ) -> str:
    """Markdown for RunComparison.to_dict() (CI job summary and artifact)."""
    lines = [f"## {title}: {ICONS[run['status']]} {run['status']}", ""]
    if run.get("selection"):
        lines += ["**Runs selected:**", *[f"- {t}: {note}" for t, note in
                                          sorted(run["selection"].items())], ""]
    if run["problems"]:
        lines += ["**Could not compare (ERROR, never a pass):**", *[f"- {p}" for p in
                                                                   run["problems"]], ""]
    lines += ["| Evaluation | Status | Comparable | Findings |", "|---|---|---|---|"]
    for c in run["comparisons"]:
        counts = {}
        for f in c["findings"]:
            counts[f["action"]] = counts.get(f["action"], 0) + 1
        summary = ", ".join(f"{n} {a}" for a, n in sorted(counts.items())) or "none"
        lines.append(f"| {c['evaluation_type']} | {ICONS[c['status']]} {c['status']} | "
                     f"{'yes' if c['comparable'] else 'no'} | {summary} |")
    lines.append("")
    for c in run["comparisons"]:
        lines += render_comparison(c)
    return "\n".join(lines) + "\n"


def _latest_by_type(history: Sequence[Mapping[str, Any]]) -> dict[str, Mapping[str, Any]]:
    latest: dict[str, Mapping[str, Any]] = {}
    for record in sorted(history, key=lambda r: r["recorded_at"]):  # stable: file order on ties
        latest[record["evaluation_type"]] = record
    return latest


def render_quality_report(history: Sequence[Mapping[str, Any]],
                          baselines: Mapping[str, Any], rules: Mapping[str, Rule],
                          known_defects_path: Path | None = None, last: int = 10) -> str:
    """The full local / CI quality report from a validated history and loaded baselines."""
    latest = _latest_by_type(history)
    comparisons = {}
    for etype, record in latest.items():
        baseline = baselines.get(etype)
        comparisons[etype] = (compare_records(baseline, record, rules).to_dict()
                              if isinstance(baseline, Mapping) else None)
    statuses = [c["status"] for c in comparisons.values() if c]
    # Nothing compared is never a pass: there is no evidence either way.
    overall = ("ERROR" if not statuses or "ERROR" in statuses else
               "BLOCKED" if "BLOCKED" in statuses else "PASS")
    uncompared = sorted(t for t, c in comparisons.items() if c is None)
    lines = [f"# AI Quality Observability Report: {ICONS[overall]} {overall}", "",
             f"{len(history)} record(s) in history; latest run per evaluation type compared with "
             "its approved baseline. PASS means no regression beyond the documented tolerances, "
             "not that the AI system is safe."]
    if uncompared:
        lines.append(f"**Not compared (no usable baseline): {', '.join(uncompared)}.** The "
                     "status above covers only the compared evaluation types.")
    lines.append("")

    lines += ["## Latest results", "",
              "| Evaluation | Recorded (UTC) | Commit | Run status | vs baseline | Key metrics |",
              "|---|---|---|---|---|---|"]
    for etype, record in sorted(latest.items()):
        comparison = comparisons[etype]
        versus = (f"{ICONS[comparison['status']]} {comparison['status']}" if comparison else
                  f"no baseline ({baselines.get(etype) or 'not approved'})")
        key = "; ".join(f"{n} {_cell(record, n)}" for n in TREND_COLUMNS.get(etype, ())[:4])
        lines.append(f"| {etype}{' (advisory)' if etype == 'ragas_advisory' else ''} | "
                     f"{record['recorded_at']} | {_short(record['source'].get('commit'))} | "
                     f"{record['status']} | {versus} | {key} |")
    lines.append("")

    lines += ["## Baseline comparison", ""]
    for etype in sorted(comparisons):
        if comparisons[etype]:
            lines += render_comparison(comparisons[etype])
        else:
            lines += [f"### {etype}: no comparison", "",
                      f"No usable baseline: {baselines.get(etype) or 'none approved'}.", ""]

    lines += ["## Trends (comparable runs only)", ""]
    for etype in sorted(latest):
        lines += _trend_section(etype, [r for r in history if r["evaluation_type"] == etype],
                                rules, last)

    lines += _defects_section(known_defects_path, latest)
    lines += ["## Data limitations", "", *[f"- {text}" for text in LIMITATIONS], ""]
    return "\n".join(lines) + "\n"


def _trend_section(etype: str, records: Sequence[Mapping[str, Any]], rules: Mapping[str, Rule],
                   last: int) -> list[str]:
    ordered = sorted(records, key=lambda r: r["recorded_at"])
    key = ordered[-1]["comparability_key"]
    group = [r for r in ordered if r["comparability_key"] == key]
    other = len(ordered) - len(group)
    columns = TREND_COLUMNS.get(etype, ())
    title = f"### {etype}" + (" (advisory LLM-judge scores, never blocking)"
                              if etype == "ragas_advisory" else "")
    lines = [title, "", f"{len(group)} comparable run(s) (comparability key `{key}`)"
             + (f"; {other} run(s) with a different dataset/model configuration are not combined "
                "with these." if other else "."), ""]
    lines += ["| Recorded (UTC) | Commit | Status | Seeded failure | " + " | ".join(columns) + " |",
              "|---" * (4 + len(columns)) + "|"]
    for r in group[-last:]:
        seeded = r["parameters"].get("simulated_failure", "n/a")
        lines.append(f"| {r['recorded_at']} | {_short(r['source'].get('commit'))} | {r['status']} "
                     f"| {seeded} | " + " | ".join(_cell(r, c) for c in columns) + " |")
    if len(group) >= 2:
        change = compare_records(group[-2], group[-1], rules).to_dict()
        moved = [f for f in change["findings"] if f["action"] != "error"]
        lines += ["", f"Latest vs previous comparable run: {ICONS[change['status']]} "
                      f"{change['status']}" + (f" ({len(moved)} finding(s))" if moved else "")]
        lines += [f"- {f['action'].upper()}: {f['reason']}" for f in change["findings"]]
    else:
        lines += ["", "Only one comparable run: no trend yet."]
    return lines + [""]


def _defects_section(path: Path | None, latest: Mapping[str, Mapping[str, Any]]) -> list[str]:
    lines = ["## Known unresolved defects", ""]
    try:
        defects = json.loads(Path(path).read_text(encoding="utf-8"))["defects"] if path else None
    except (OSError, ValueError, KeyError, TypeError):
        defects = None
    if not isinstance(defects, list):
        return lines + ["- known defects file unavailable or unreadable", ""]
    for d in defects:
        if not isinstance(d, dict) or d.get("status") != "open":
            continue
        tracked = d.get("tracked_metric") or {}
        record = latest.get(tracked.get("evaluation_type", ""))
        measurement = ""
        if tracked:
            value = (_cell(record, tracked["metric"]) if record else
                     "no record of this evaluation type in the history")
            measurement = f" Latest {tracked.get('evaluation_type')} {tracked['metric']}: {value}."
        lines.append(f"- **{d.get('id')} ({d.get('severity')}):** {d.get('title')}.{measurement}")
    return lines + [""]
