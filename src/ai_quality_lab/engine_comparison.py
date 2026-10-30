"""Compare the custom evaluation engine with a Promptfoo run on the shared dataset.

Usage (after a Promptfoo run has written its JSON results)::

    python -m ai_quality_lab.engine_comparison reports/promptfoo/results.json

The custom engine is run in-process with the same simulated-failure mode the Promptfoo run used
(read from the results file), then outcomes are matched case by case using the ``case_id``
metadata that :mod:`ai_quality_lab.promptfoo_adapter` attaches to every Promptfoo test.

Exit codes: 0 = both engines agree on every case, 1 = any disagreement or case missing on one
side, 2 = an input file could not be read or is not a Promptfoo results file for this dataset.
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_quality_lab.dataset import DatasetValidationError, Severity, load_dataset
from ai_quality_lab.evaluator import CaseOutcome, EvaluationSummary
from ai_quality_lab.mock_agent import SimulatedFailure
from ai_quality_lab.runner import DEFAULT_DATASET_PATH, mock_chatbot_factory, run_evaluation_suite

_PROMPTFOO_ERROR_REASON = 2
"""Promptfoo's ``failureReason`` code for an error (0 = none, 1 = assertion failure)."""


class PromptfooResultsError(ValueError):
    """The file is not a usable Promptfoo results file for the shared dataset."""


@dataclass(frozen=True)
class PromptfooCaseResult:
    case_id: str
    outcome: CaseOutcome
    failed_checks: tuple[str, ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class PromptfooRun:
    simulated_failure: SimulatedFailure
    results: Mapping[str, PromptfooCaseResult]


@dataclass(frozen=True)
class ComparisonRow:
    case_id: str
    severity: str
    custom: CaseOutcome | None
    promptfoo: CaseOutcome | None
    custom_failed_checks: tuple[str, ...]
    promptfoo_failed_checks: tuple[str, ...]

    @property
    def agree(self) -> bool:
        return self.custom is not None and self.custom is self.promptfoo


@dataclass(frozen=True)
class EngineComparison:
    simulated_failure: SimulatedFailure
    rows: tuple[ComparisonRow, ...]
    custom_summary: EvaluationSummary

    @property
    def disagreements(self) -> tuple[ComparisonRow, ...]:
        return tuple(row for row in self.rows if not row.agree)

    def outcome_counts(self, engine: str) -> dict[str, int]:
        outcomes = [getattr(row, engine) for row in self.rows]
        return {o.value: sum(1 for x in outcomes if x is o) for o in CaseOutcome}

    def promptfoo_failed_by_severity(self) -> dict[str, int]:
        return {
            s.value: sum(
                1 for r in self.rows if r.severity == s.value and r.promptfoo is CaseOutcome.FAIL
            )
            for s in Severity
        }


def load_promptfoo_results(path: str | Path) -> PromptfooRun:
    """Parse a Promptfoo ``eval -o results.json`` file into per-case outcomes."""
    try:
        document = json.loads(Path(path).read_text(encoding="utf-8"))
        rows = document["results"]["results"]
    except (json.JSONDecodeError, KeyError, TypeError) as exc:
        raise PromptfooResultsError(f"{path} is not a Promptfoo JSON results file: {exc}") from exc

    results: dict[str, PromptfooCaseResult] = {}
    for row in rows:
        parsed = _parse_row(row)
        if parsed.case_id in results:
            raise PromptfooResultsError(
                f"case_id {parsed.case_id!r} appears more than once; compare one provider and one "
                "prompt per run."
            )
        results[parsed.case_id] = parsed
    return PromptfooRun(simulated_failure=_simulated_failure(document), results=results)


def _parse_row(row: Mapping[str, Any]) -> PromptfooCaseResult:
    metadata = (row.get("testCase") or {}).get("metadata") or {}
    case_id = metadata.get("case_id")
    if not isinstance(case_id, str):
        raise PromptfooResultsError(
            "A Promptfoo result has no 'case_id' metadata; it was not generated from the shared "
            "dataset by promptfoo/tests.py."
        )
    # Note: Promptfoo also fills the top-level "error" field for ordinary assertion failures
    # (failureReason 1), so only failureReason 2 or a provider-level error means ERROR.
    provider_error = (row.get("response") or {}).get("error")
    if row.get("failureReason") == _PROMPTFOO_ERROR_REASON or provider_error:
        message = provider_error or row.get("error")
        return PromptfooCaseResult(case_id, CaseOutcome.ERROR, error=str(message))
    components = (row.get("gradingResult") or {}).get("componentResults") or []
    failed = tuple(
        c.get("assertion", {}).get("metric") or c.get("assertion", {}).get("type", "?")
        for c in components
        if not c.get("pass")
    )
    outcome = CaseOutcome.PASS if row.get("success") else CaseOutcome.FAIL
    return PromptfooCaseResult(case_id, outcome, failed_checks=failed)


def _simulated_failure(document: Mapping[str, Any]) -> SimulatedFailure:
    providers = (document.get("config") or {}).get("providers") or []
    modes = {
        (p.get("config") or {}).get("simulated_failure", SimulatedFailure.NONE.value)
        for p in providers
        if isinstance(p, Mapping)
    } or {SimulatedFailure.NONE.value}
    if len(modes) != 1:
        raise PromptfooResultsError(f"Results mix simulated-failure modes: {sorted(modes)}.")
    return SimulatedFailure(modes.pop())


def compare_engines(
    custom: EvaluationSummary, promptfoo: PromptfooRun
) -> EngineComparison:
    """Match outcomes by case ID. Cases present on only one side are reported, not dropped."""
    custom_by_id = {r.case.id: r for r in custom.results}
    rows = []
    for case_id in sorted(custom_by_id.keys() | promptfoo.results.keys()):
        custom_result = custom_by_id.get(case_id)
        promptfoo_result = promptfoo.results.get(case_id)
        rows.append(
            ComparisonRow(
                case_id=case_id,
                severity=custom_result.case.severity.value if custom_result else "?",
                custom=custom_result.outcome if custom_result else None,
                promptfoo=promptfoo_result.outcome if promptfoo_result else None,
                custom_failed_checks=tuple(a.name for a in custom_result.failed_assertions)
                if custom_result
                else (),
                promptfoo_failed_checks=promptfoo_result.failed_checks if promptfoo_result else (),
            )
        )
    return EngineComparison(promptfoo.simulated_failure, tuple(rows), custom)


def format_comparison(comparison: EngineComparison) -> str:
    lines = [
        f"Simulated failure mode: {comparison.simulated_failure.value}",
        "",
        f"{'Case':<8} {'Severity':<9} {'Custom':<7} {'Promptfoo':<10} Agree",
        f"{'-' * 8} {'-' * 9} {'-' * 7} {'-' * 10} -----",
    ]
    for row in comparison.rows:
        custom = row.custom.value if row.custom else "MISSING"
        promptfoo = row.promptfoo.value if row.promptfoo else "MISSING"
        lines.append(
            f"{row.case_id:<8} {row.severity:<9} {custom:<7} {promptfoo:<10} "
            f"{'yes' if row.agree else 'NO'}"
        )
        if row.custom_failed_checks or row.promptfoo_failed_checks:
            custom_checks = ", ".join(row.custom_failed_checks) or "-"
            promptfoo_checks = ", ".join(row.promptfoo_failed_checks) or "-"
            lines.append(f"{'':<8}   custom failed:    {custom_checks}")
            lines.append(f"{'':<8}   promptfoo failed: {promptfoo_checks}")

    custom_counts = comparison.outcome_counts("custom")
    promptfoo_counts = comparison.outcome_counts("promptfoo")
    promptfoo_all_passed = promptfoo_counts[CaseOutcome.PASS.value] == len(comparison.rows)
    readiness = comparison.custom_summary.release_readiness.value
    agreements = len(comparison.rows) - len(comparison.disagreements)
    lines += [
        "",
        f"Custom engine: {_format_counts(custom_counts)}  -> release readiness {readiness}",
        f"Promptfoo:     {_format_counts(promptfoo_counts)}  -> eval exit code "
        f"{'0' if promptfoo_all_passed else '100 (any failure or error)'}",
        "Promptfoo failures by dataset severity (metadata only; Promptfoo does not act on it): "
        + _format_counts(comparison.promptfoo_failed_by_severity()),
        f"Cases compared: {len(comparison.rows)}  Agreements: {agreements}  "
        f"Disagreements: {len(comparison.disagreements)}",
    ]
    return "\n".join(lines)


def _format_counts(counts: Mapping[str, int]) -> str:
    return "  ".join(f"{key}={value}" for key, value in counts.items())


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ai_quality_lab.engine_comparison",
        description="Compare a Promptfoo results file with the custom engine on the same dataset.",
    )
    parser.add_argument("promptfoo_results", type=Path, help="Promptfoo `eval -o` JSON file")
    parser.add_argument("--dataset", type=Path, default=DEFAULT_DATASET_PATH)
    parser.add_argument("--output", type=Path, help="also write a JSON summary here (for CI)")
    args = parser.parse_args(argv)

    try:
        promptfoo = load_promptfoo_results(args.promptfoo_results)
        cases = load_dataset(args.dataset)
    except (PromptfooResultsError, DatasetValidationError, OSError) as exc:
        print(f"Input error: {exc}", file=sys.stderr)
        return 2

    custom = run_evaluation_suite(cases, mock_chatbot_factory(promptfoo.simulated_failure))
    comparison = compare_engines(custom, promptfoo)
    print(format_comparison(comparison))
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(comparison_to_dict(comparison), indent=2),
                               encoding="utf-8")
    return 0 if not comparison.disagreements else 1


def comparison_to_dict(comparison: EngineComparison) -> dict:
    return {
        "simulated_failure": comparison.simulated_failure.value,
        "cases_compared": len(comparison.rows),
        "agreements": len(comparison.rows) - len(comparison.disagreements),
        "disagreements": [row.case_id for row in comparison.disagreements],
        "custom_outcomes": comparison.outcome_counts("custom"),
        "promptfoo_outcomes": comparison.outcome_counts("promptfoo"),
        "custom_release_readiness": comparison.custom_summary.release_readiness.value,
    }


if __name__ == "__main__":
    raise SystemExit(main())
