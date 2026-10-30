"""Executes evaluation cases against an agent and produces an :class:`EvaluationSummary`.

The runner builds a fresh agent for every case from that case's own configuration, sends the input
through the agent's public ``respond`` method, and hands an agent-agnostic snapshot of the reply to
the evaluator. A failing or erroring case never stops the rest of the suite.

Command line::

    python -m ai_quality_lab.runner [--dataset PATH] [--simulate-failure MODE] [--json]

Exit codes: 0 = READY, 1 = BLOCKED, 2 = dataset could not be loaded or is invalid.
"""

from __future__ import annotations

import argparse
import copy
import sys
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Protocol

from ai_quality_lab.dataset import DatasetValidationError, EvalCase, load_dataset
from ai_quality_lab.evaluator import (
    CaseOutcome,
    CaseResult,
    ErrorDetails,
    EvaluationSummary,
    ObservedReply,
    ReleaseReadiness,
    decide_outcome,
    evaluate_expectations,
    summarize,
)
from ai_quality_lab.mock_agent import MockChatbot, SimulatedFailure

DEFAULT_DATASET_PATH = Path("evals") / "guardrail_cases.json"


class AgentReply(Protocol):
    """The minimum an agent's reply must expose to be evaluated."""

    @property
    def response(self) -> str: ...

    @property
    def blocked(self) -> bool: ...

    @property
    def detected_topics(self) -> Sequence[str]: ...


class Agent(Protocol):
    def respond(self, message: str) -> AgentReply: ...


AgentFactory = Callable[[Mapping[str, Any]], Agent]
"""Builds an agent from one case's ``configuration``."""


class AgentContractError(TypeError):
    """The agent returned a reply that does not satisfy :class:`AgentReply`."""


def mock_chatbot_factory(
    simulated_failure: SimulatedFailure = SimulatedFailure.NONE,
) -> AgentFactory:
    """Factory for :class:`MockChatbot`, optionally with a test-only simulated failure."""

    def build(configuration: Mapping[str, Any]) -> Agent:
        return MockChatbot(configuration, simulated_failure=simulated_failure)

    return build


def observe(reply: Any) -> ObservedReply:
    """Validate an agent reply against :class:`AgentReply` and snapshot it."""
    response = getattr(reply, "response", None)
    blocked = getattr(reply, "blocked", None)
    topics = getattr(reply, "detected_topics", None)
    if not isinstance(response, str):
        raise AgentContractError(f"reply.response must be a str, got {type(response).__name__}.")
    if type(blocked) is not bool:
        raise AgentContractError(f"reply.blocked must be a bool, got {type(blocked).__name__}.")
    if isinstance(topics, str) or not isinstance(topics, Sequence) or not all(
        isinstance(t, str) for t in topics
    ):
        raise AgentContractError("reply.detected_topics must be a sequence of str.")
    return ObservedReply(response=response, blocked=blocked, detected_topics=tuple(topics))


def run_case(case: EvalCase, agent_factory: AgentFactory) -> CaseResult:
    """Run one case. Exceptions become an ERROR result; they are never raised or treated as PASS."""
    stage = "setup"
    observed: ObservedReply | None = None
    start = time.perf_counter()
    try:
        # Deep copy so an agent cannot mutate the dataset and affect later cases.
        agent = agent_factory(copy.deepcopy(dict(case.configuration)))
        stage = "execution"
        observed = observe(agent.respond(case.input))
        stage = "evaluation"
        assertions = evaluate_expectations(case.expected, observed)
        outcome = decide_outcome(assertions)
    except Exception as exc:  # noqa: BLE001 - any failure to complete is reported as ERROR
        return CaseResult(
            case=case,
            outcome=CaseOutcome.ERROR,
            observed=observed,
            error=ErrorDetails(stage=stage, type=type(exc).__name__, message=str(exc)),
            duration_ms=_elapsed_ms(start),
        )
    return CaseResult(
        case=case,
        outcome=outcome,
        assertions=assertions,
        observed=observed,
        duration_ms=_elapsed_ms(start),
    )


def run_evaluation_suite(
    cases: Sequence[EvalCase], agent_factory: AgentFactory | None = None
) -> EvaluationSummary:
    """Run every case (in order) and summarize. Defaults to a healthy :class:`MockChatbot`.

    Raises:
        DatasetValidationError: if case IDs are not unique.
    """
    duplicates = sorted(case_id for case_id, n in Counter(c.id for c in cases).items() if n > 1)
    if duplicates:
        raise DatasetValidationError([f"Duplicate case id(s): {duplicates}."])
    factory = agent_factory or mock_chatbot_factory()
    return summarize([run_case(case, factory) for case in cases])


def run_dataset(
    path: str | Path = DEFAULT_DATASET_PATH, agent_factory: AgentFactory | None = None
) -> EvaluationSummary:
    """Load and validate a dataset file, then run it."""
    return run_evaluation_suite(load_dataset(path), agent_factory)


# --- Command line -------------------------------------------------------------------------------


def format_summary(summary: EvaluationSummary) -> str:
    """Plain-text (ASCII) console rendering of a summary."""
    lines = ["Results", "-------"]
    for result in summary.results:
        case = result.case
        marker = "  << CRITICAL FAILURE" if result.critical_failure else ""
        lines.append(
            f"[{result.outcome.value:<5}] {case.id:<8} {case.severity.value:<8} "
            f"{case.category.value:<11} {case.name}{marker}"
        )
        for assertion in result.failed_assertions:
            lines.append(
                f"          - {assertion.name}: expected {_clip(assertion.expected)}, "
                f"actual {_clip(assertion.actual)}"
            )
            lines.append(f"            {assertion.explanation}")
        if result.error:
            lines.append(
                f"          - {result.error.stage} error: "
                f"{result.error.type}: {result.error.message}"
            )

    rate = "n/a (no cases executed)" if summary.pass_rate is None else f"{summary.pass_rate:.2%}"
    lines += [
        "",
        "Summary",
        "-------",
        f"Total: {summary.total}  Passed: {summary.passed}  Failed: {summary.failed}  "
        f"Errored: {summary.errored}",
        f"Pass rate: {rate}  (passed / all executed cases, errors included)",
        "Failures by severity: " + _format_counts(summary.failures_by_severity),
        "Errors by severity:   " + _format_counts(summary.errors_by_severity),
        "By category:",
    ]
    for category, counts in summary.by_category.items():
        lines.append(f"  {category:<11} " + _format_counts(counts))
    critical = ", ".join(summary.critical_failures) or "none"
    lines += [
        f"Critical failures: {critical}",
        f"Release readiness: {summary.release_readiness.value}",
    ]
    lines += [f"  - {reason}" for reason in summary.readiness_reasons]
    return "\n".join(lines)


def _clip(value: Any, limit: int = 70) -> str:
    # Console view only; JSON output keeps the full evidence.
    text = repr(value)
    return text if len(text) <= limit else text[: limit - 3] + "..."


def _format_counts(counts: Mapping[str, int]) -> str:
    return "  ".join(f"{key}={value}" for key, value in counts.items())


def _elapsed_ms(start: float) -> float:
    return round((time.perf_counter() - start) * 1000, 3)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ai_quality_lab.runner",
        description="Run the evaluation dataset against the mock chatbot.",
    )
    parser.add_argument(
        "--dataset",
        type=Path,
        default=DEFAULT_DATASET_PATH,
        help=f"dataset JSON file (default: {DEFAULT_DATASET_PATH})",
    )
    parser.add_argument(
        "--simulate-failure",
        choices=[mode.value for mode in SimulatedFailure],
        default=SimulatedFailure.NONE.value,
        help="TEST-ONLY: run the mock chatbot with a simulated defect",
    )
    parser.add_argument("--json", action="store_true", help="print the summary as JSON")
    args = parser.parse_args(argv)

    try:
        cases = load_dataset(args.dataset)
    except (DatasetValidationError, OSError) as exc:
        print(f"Dataset error: {exc}", file=sys.stderr)
        return 2

    simulated_failure = SimulatedFailure(args.simulate_failure)
    summary = run_evaluation_suite(cases, mock_chatbot_factory(simulated_failure))
    if args.json:
        print(summary.to_json())
    else:
        print(f"Dataset: {args.dataset}  ({len(cases)} cases)")
        if simulated_failure is not SimulatedFailure.NONE:
            print(f"WARNING: simulated failure '{simulated_failure.value}' is ENABLED (test-only).")
        print()
        print(format_summary(summary))
    return 0 if summary.release_readiness is ReleaseReadiness.READY else 1


if __name__ == "__main__":
    raise SystemExit(main())
