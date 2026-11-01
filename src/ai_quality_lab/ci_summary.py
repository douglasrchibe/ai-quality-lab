"""CI reporting: pytest skip audit, GitHub job summary, and the final quality gate.

    python -m ai_quality_lab.ci_summary skips --junit ci-artifacts/python-tests/junit.xml \\
        --out ci-artifacts/python-tests
    python -m ai_quality_lab.ci_summary summary --artifacts downloaded --output summary.md \\
        --needs-json '<toJSON(needs)>'
    python -m ai_quality_lab.ci_summary gate --artifacts downloaded --needs-json '<toJSON(needs)>'

Design rules:
* Release decisions come from exit codes (``ci_check`` status files and job results), never from
  parsing human-readable output. The summary is informational and must never crash: anything it
  cannot read is shown as "unavailable", not guessed.
* Known defects (``ci/known_defects.json``) are always listed, so a green run cannot hide them.
* Outcomes: PASS (exit 0), BLOCKED (exit 1, a quality criterion failed), ERROR (exit 2, something
  could not run).
"""

from __future__ import annotations

import argparse
import json
import sys
import xml.etree.ElementTree as ET
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

ALLOWED_SKIP_MODULES = ("tests.test_llm_live", "tests.test_rag_live")
"""Only opt-in live-model tests may be skipped in CI; any other skip means lost coverage."""
KNOWN_DEFECTS = Path("ci") / "known_defects.json"
LIMITATIONS = (
    "CI uses deterministic stand-ins (hashing embeddings, mock models). A pass is evidence for the "
    "tested scenarios and configurations only, not proof that the AI system is safe or reliable.",
    "Live-model behaviour (Ollama qwen2.5:3b / nomic-embed-text) is not evaluated in CI; run the "
    "live commands locally (README, Phase 6).",
    "Ragas and DeepEval judge scores are advisory and are not computed in CI.",
)


# --- pytest skip audit --------------------------------------------------------------------------


def audit_skips(junit_path: Path, allowed: Sequence[str] = ALLOWED_SKIP_MODULES) -> dict[str, Any]:
    """Counts from a JUnit file, and any skipped test outside the allowed live-test modules."""
    root = ET.parse(junit_path).getroot()
    suites = [root] if root.tag == "testsuite" else list(root.iter("testsuite"))
    totals = {key: sum(int(s.get(key, 0)) for s in suites)
              for key in ("tests", "failures", "errors", "skipped")}
    unexpected, allowed_skips = [], 0
    for case in root.iter("testcase"):
        skipped = case.find("skipped")
        if skipped is None:
            continue
        module = case.get("classname", "")
        if any(module == a or module.startswith(a + ".") for a in allowed):
            allowed_skips += 1
        else:
            unexpected.append({"test": f"{module}::{case.get('name')}",
                               "reason": skipped.get("message", "")})
    return {**totals, "passed": totals["tests"] - totals["failures"] - totals["errors"]
            - totals["skipped"], "allowed_skips": allowed_skips, "unexpected_skips": unexpected}


# --- Reading artifacts (tolerant) ---------------------------------------------------------------


def _find(root: Path, name: str) -> Path | None:
    matches = sorted(root.rglob(name))
    return matches[0] if matches else None


def _load_json(root: Path, name: str) -> Any:
    path = _find(root, name)
    if path is None:
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def load_statuses(root: Path) -> list[dict[str, Any]]:
    """All ``*.status.json`` files. ``job`` = the artifact folder (``ci-<job>``) they came from."""
    statuses = []
    for path in sorted(root.rglob("*.status.json")):
        relative = path.relative_to(root).parts
        job = relative[0].removeprefix("ci-") if len(relative) > 1 else ""
        try:
            status = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            status = None
        if not isinstance(status, dict):  # unreadable, or valid JSON that is not an object
            status = {"name": path.name, "outcome": "ERROR", "expectation_met": False,
                      "expected": "?", "exit_code": None, "unreadable": True}
        statuses.append({**status, "job": job})
    return statuses


class NeedsError(ValueError):
    """Job results (GitHub ``needs`` context) are missing or unreadable."""


def parse_needs(needs_json: str | None) -> dict[str, str]:
    """Job name -> result. Raises ``NeedsError`` instead of guessing: without job results the gate
    cannot see a job that failed before writing any check status (e.g. a failed install)."""
    if not needs_json or not needs_json.strip():
        raise NeedsError("no job results were provided (--needs-json / --needs-file)")
    try:
        needs = json.loads(needs_json)
        results = {job: str(info["result"]) for job, info in needs.items()}
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise NeedsError(f"job results are not valid needs JSON: {exc}") from None
    if not results:
        raise NeedsError("job results are empty")
    return results


# --- Gate ---------------------------------------------------------------------------------------


def overall_outcome(job_results: Mapping[str, str], statuses: Sequence[Mapping[str, Any]]
                    ) -> tuple[str, list[str]]:
    """PASS only if every required job succeeded and every check met its expectation.

    ERROR wins over BLOCKED: a check that could not run, a cancelled/skipped job, or a job that
    failed without any unmet check status (e.g. dependency installation failed) means the
    evaluation is incomplete, so its quality verdict is unknown.
    """
    if not job_results and not statuses:
        return "ERROR", ["no job results or check statuses were available"]
    unmet = [s for s in statuses if not s.get("expectation_met")]
    reasons = [f"{s.get('name')}: expected {s.get('expected')}, got {s.get('outcome')} "
               f"(exit {s.get('exit_code')})"
               + (f"; {s['report_problem']}" if s.get("report_problem") else "") for s in unmet]
    error = any(s.get("outcome") == "ERROR" for s in unmet)
    for job, result in sorted(job_results.items()):
        if result == "success":
            continue
        explained = any(s.get("job") == job for s in unmet)
        if result != "failure" or not explained:
            error = True
            reasons.append(f"job '{job}' finished with result '{result}'"
                           + ("" if explained else " before any check reported a quality outcome"))
    if not reasons:
        return "PASS", []
    return ("ERROR" if error else "BLOCKED"), reasons


EXIT_CODES = {"PASS": 0, "BLOCKED": 1, "ERROR": 2}


# --- Summary markdown ---------------------------------------------------------------------------


def _fmt(value: Any) -> str:
    return "unavailable" if value is None else str(value)


SECTION_ERRORS = (KeyError, TypeError, AttributeError, ValueError)
"""A malformed report shows as unreadable; it must not crash the summary (and skip the gate)."""


def _result_line(label: str, render: Callable[[], str]) -> str:
    try:
        return f"- **{label}:** {render()}"
    except SECTION_ERRORS as exc:
        return f"- **{label}:** report unreadable ({type(exc).__name__})"


def _pytest_line(skips: Any) -> str:
    if not skips:
        return "report unavailable"
    return (f"{skips['tests']} tests, {skips['passed']} passed, {skips['failures']} failed, "
            f"{skips['errors']} errors, {skips['skipped']} skipped ({skips['allowed_skips']} "
            f"opt-in live tests; {len(skips['unexpected_skips'])} unexpected skips)")


def _engine_line(engine: Any) -> str:
    if not engine:
        return "report unavailable"
    return (f"{engine['passed']}/{engine['total']} passed, release readiness "
            f"{engine['release_readiness']}")


def _promptfoo_line(promptfoo: Any, comparison: Any) -> str:
    stats = (promptfoo or {}).get("results", {}).get("stats")
    run = (f"{stats['successes']} passed, {stats['failures']} failed, {stats['errors']} errors"
           if stats else "report unavailable")
    agreement = (f"{comparison['agreements']}/{comparison['cases_compared']}" if comparison
                 else "unavailable")
    return f"{run}; agreement with custom engine: {agreement}"


def _rag_line(rag: Any) -> str:
    if not rag:
        return "report unavailable"
    doc = rag.get("retrieval_metrics", {}).get("document", {})
    gates = rag.get("release_gates", {})
    failed_gates = [g for g, v in gates.items() if not v.get("passed")]
    classes = {k: v for k, v in rag.get("failure_classifications", {}).items() if v}
    return (f"{rag.get('passed')}/{rag.get('total')} passed, critical failures "
            f"{rag.get('critical_failures') or 'none'}, readiness {rag.get('release_readiness')}; "
            f"document Hit Rate@{rag.get('configuration', {}).get('top_k')} {doc.get('hit_rate')}, "
            f"Recall {doc.get('recall')}; failed gates: {failed_gates or 'none'}"
            + (f"; failure classes: {classes}" if classes else ""))


def _regression_line(regression: Any) -> str:
    if not regression:
        return "report unavailable"
    per_type = ", ".join(f"{c['evaluation_type']} {c['status']}" for c in regression["comparisons"])
    problems = f"; problems: {'; '.join(regression['problems'])}" if regression["problems"] else ""
    return (f"{regression['status']} against the approved baselines "
            f"({per_type or 'nothing compared'}){problems}")


def _defect_lines(known_defects_path: Path) -> list[str]:
    try:
        defects = json.loads(known_defects_path.read_text(encoding="utf-8"))["defects"]
        return [f"- **{d['id']} ({d['severity']}):** {d['title']}"
                + (f" ({', '.join(d['cases'])})" if d["cases"] else "") + ". "
                f"Last observed {d['last_observed']}. {d['ci_visibility']}"
                for d in defects if d.get("status") == "open"]
    except (OSError, *SECTION_ERRORS):
        return ["- known defects file unavailable or unreadable"]


def build_summary(root: Path, job_results: Mapping[str, str],
                  known_defects_path: Path = KNOWN_DEFECTS, needs_problem: str | None = None
                  ) -> str:
    statuses = load_statuses(root)
    outcome, reasons = overall_outcome(job_results, statuses)
    if needs_problem:
        outcome, reasons = "ERROR", [f"job results unavailable: {needs_problem}", *reasons]
    icon = {"PASS": "✅", "BLOCKED": "❌", "ERROR": "⚠️"}[outcome]
    lines = [f"# AI Quality Lab CI: {icon} {outcome}", ""]
    if reasons:
        lines += ["**Why:**", *[f"- {r}" for r in reasons], ""]

    if job_results:
        lines += ["## Jobs", "", "| Job | Result |", "|---|---|"]
        lines += [f"| {job} | {result} |" for job, result in sorted(job_results.items())]
        lines.append("")

    lines += ["## Checks (from exit codes)", "",
              "| Check | Expected | Outcome | Exit | Met |", "|---|---|---|---|---|"]
    for s in sorted(statuses, key=lambda s: str(s.get("name"))):
        met = "yes" if s.get("expectation_met") else "**NO**"
        lines.append(f"| {s.get('name')} | {s.get('expected')} | {s.get('outcome')} | "
                     f"{_fmt(s.get('exit_code'))} | {met} |")
    if not statuses:
        lines.append("| (no check status files found) | | | | |")
    lines.append("")

    lines += ["## Results", ""]
    lines.append(_result_line("pytest", lambda: _pytest_line(_load_json(root, "skip-audit.json"))))
    lines.append(_result_line("Custom engine", lambda: _engine_line(
        _load_json(root, "custom-engine-report.json"))))
    lines.append(_result_line("Promptfoo", lambda: _promptfoo_line(
        _load_json(root, "promptfoo-results.json"), _load_json(root, "comparison-normal.json"))))
    for label, name in (("RAG retrieval (hashing)", "rag-retrieval.json"),
                        ("RAG end-to-end (mock model)", "rag-mock.json")):
        lines.append(_result_line(label, lambda name=name: _rag_line(_load_json(root, name))))
    lines.append(_result_line("Regression vs baseline", lambda: _regression_line(
        _load_json(root, "regression.json"))))
    lines.append("")

    lines += ["## Known defects (not detectable in CI, still open)", ""]
    lines += _defect_lines(known_defects_path)
    lines += ["", "## Not run in this workflow", "",
              "- Live-model tests (`-m live_llm`) and live RAG/DeepEval evaluations (need Ollama).",
              "- Ragas advisory scoring (needs a judge model); Ragas *environment* validation runs "
              "in the separate `Ragas environment` workflow when dependency files change.",
              "", "## Limitations", ""]
    lines += [f"- {text}" for text in LIMITATIONS]
    lines += ["", "Artifacts: download the `ci-*` artifacts from this run for JUnit XML, logs, "
              "and JSON reports."]
    return "\n".join(lines) + "\n"


# --- CLI ----------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_quality_lab.ci_summary")
    sub = parser.add_subparsers(dest="command", required=True)
    skips = sub.add_parser("skips", help="fail if pytest skipped anything except live tests")
    skips.add_argument("--junit", type=Path, required=True)
    skips.add_argument("--out", type=Path, required=True)
    for name in ("summary", "gate"):
        p = sub.add_parser(name)
        p.add_argument("--artifacts", type=Path, required=True)
        needs = p.add_mutually_exclusive_group(required=True)
        needs.add_argument("--needs-json", help="toJSON(needs) from the workflow")
        needs.add_argument("--needs-file", type=Path, help="the same JSON, read from a file")
    sub.choices["summary"].add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)

    if args.command == "skips":
        try:
            audit = audit_skips(args.junit)
        except (OSError, ET.ParseError) as exc:
            print(f"Skip audit error: cannot read {args.junit}: {exc}", file=sys.stderr)
            return 2
        args.out.mkdir(parents=True, exist_ok=True)
        (args.out / "skip-audit.json").write_text(json.dumps(audit, indent=2), encoding="utf-8")
        for item in audit["unexpected_skips"]:
            print(f"UNEXPECTED SKIP: {item['test']}: {item['reason']}")
        print(f"[skip audit] {audit['skipped']} skipped, {audit['allowed_skips']} allowed live "
              f"tests, {len(audit['unexpected_skips'])} unexpected")
        return 1 if audit["unexpected_skips"] else 0

    try:
        raw = (args.needs_file.read_text(encoding="utf-8-sig") if args.needs_file
               else args.needs_json)
        job_results = parse_needs(raw)
        needs_problem = None
    except (OSError, NeedsError) as exc:
        job_results, needs_problem = {}, str(exc)
    if args.command == "summary":
        text = build_summary(args.artifacts, job_results, needs_problem=needs_problem)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(text, encoding="utf-8")
        print(text)
        return 0
    if needs_problem:
        print(f"Overall CI outcome: ERROR\n  - {needs_problem}")
        return EXIT_CODES["ERROR"]
    outcome, reasons = overall_outcome(job_results, load_statuses(args.artifacts))
    print(f"Overall CI outcome: {outcome}")
    for reason in reasons:
        print(f"  - {reason}")
    return EXIT_CODES[outcome]


if __name__ == "__main__":
    raise SystemExit(main())
