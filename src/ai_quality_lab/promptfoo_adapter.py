"""Bridge between Promptfoo and the mock chatbot.

Promptfoo is a Node.js evaluation tool. It talks to this project through two Python entry points,
both loaded from thin shims in ``promptfoo/``:

* :func:`call_api` is a Promptfoo *Python provider*. It builds a real :class:`MockChatbot` from the
  test's ``configuration`` var, sends the ``message`` var, and returns the reply as a JSON string
  ``{"response": ..., "blocked": ..., "detected_topics": [...]}``.
* :func:`build_promptfoo_tests` is a Promptfoo *test generator*. It converts the shared dataset
  (``evals/guardrail_cases.json``, the same file the custom engine uses) into Promptfoo test cases
  with equivalent assertions, so the two engines cannot drift apart.

No chatbot logic is duplicated here; the adapter only translates formats.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ai_quality_lab.dataset import EvalCase, Expectations, load_dataset
from ai_quality_lab.mock_agent import ChatResponse, MockChatbot, SimulatedFailure

DEFAULT_DATASET_PATH = Path(__file__).resolve().parents[2] / "evals" / "guardrail_cases.json"
ASSERTIONS_FILE = "file://assertions.js"
"""JavaScript assertion helpers, resolved relative to the Promptfoo config directory."""

RESPONSE_FIELD = "JSON.parse(output).response"
"""Promptfoo ``transform`` that extracts the reply text before phrase assertions run."""

OUTPUT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "required": ["response", "blocked", "detected_topics"],
    "properties": {
        "response": {"type": "string"},
        "blocked": {"type": "boolean"},
        "detected_topics": {"type": "array", "items": {"type": "string"}},
    },
    "additionalProperties": False,
}


# --- Provider -----------------------------------------------------------------------------------


def call_api(prompt: str, options: Mapping[str, Any] | None, context: Mapping[str, Any] | None):
    """Promptfoo Python provider entry point.

    Reads ``context["vars"]["message"]`` (falling back to the rendered ``prompt``) and
    ``context["vars"]["configuration"]``. The optional provider config key ``simulated_failure``
    enables a test-only defect (see :class:`SimulatedFailure`).

    Returns ``{"output": <json string>}`` on success or ``{"error": <message>}`` if the test or
    provider configuration is invalid, which Promptfoo reports as an error, not a pass.
    """
    variables = dict((context or {}).get("vars") or {})
    provider_config = dict((options or {}).get("config") or {})
    try:
        message = variables.get("message", prompt)
        configuration = _configuration_var(variables)
        failure = SimulatedFailure(provider_config.get("simulated_failure", SimulatedFailure.NONE))
        reply = MockChatbot(configuration, simulated_failure=failure).respond(message)
    except (ValueError, TypeError) as exc:
        return {"error": f"{type(exc).__name__}: {exc}"}
    return {"output": json.dumps(reply_to_output(reply), ensure_ascii=False)}


def reply_to_output(reply: ChatResponse) -> dict[str, Any]:
    """The agent-contract fields the evaluator may judge; mock-only fields are left out."""
    return {
        "response": reply.response,
        "blocked": reply.blocked,
        "detected_topics": list(reply.detected_topics),
    }


def _configuration_var(variables: Mapping[str, Any]) -> Mapping[str, Any]:
    if "configuration" not in variables:
        raise ValueError("Test is missing the 'configuration' var (the guardrail configuration).")
    configuration = variables["configuration"]
    # Defensive: accept a JSON string in case a var is stringified (e.g. when loaded from CSV).
    if isinstance(configuration, str):
        configuration = json.loads(configuration)
    return configuration


# --- Test generator -----------------------------------------------------------------------------


def build_promptfoo_tests(dataset_path: str | Path | None = None) -> list[dict[str, Any]]:
    """Convert the shared evaluation dataset into Promptfoo test cases.

    Every test gets two contract assertions (structured output and guardrail consistency) plus
    one assertion per expectation defined in the dataset. The dataset is validated first, so an
    invalid file fails loudly instead of producing an empty or partial Promptfoo suite.
    """
    cases = load_dataset(dataset_path or DEFAULT_DATASET_PATH)
    return [case_to_promptfoo_test(case) for case in cases]


def case_to_promptfoo_test(case: EvalCase) -> dict[str, Any]:
    return {
        "description": f"{case.id}: {case.name}",
        "vars": {"message": case.input, "configuration": dict(case.configuration)},
        "assert": contract_assertions() + expectation_assertions(case.expected),
        "metadata": {
            "case_id": case.id,
            "category": case.category.value,
            "severity": case.severity.value,
            "tags": ",".join(case.tags),
        },
    }


def contract_assertions() -> list[dict[str, Any]]:
    """Checks applied to every test, independent of the case's expectations."""
    return [
        {"type": "is-json", "value": OUTPUT_SCHEMA, "metric": "structured_output"},
        {
            "type": "javascript",
            "value": f"{ASSERTIONS_FILE}:guardrailConsistency",
            "metric": "guardrail_consistency",
        },
    ]


def expectation_assertions(expected: Expectations) -> list[dict[str, Any]]:
    """Promptfoo equivalents of the custom engine's assertions, in the same order."""
    assertions: list[dict[str, Any]] = []
    if expected.blocked is not None:
        assertions.append(_js_check("blockedStatus", "blocked_status", expected.blocked))
    if expected.detected_topics is not None:
        topics = sorted(expected.detected_topics)
        assertions.append(_js_check("detectedTopics", "detected_topics", topics))
    if expected.response_nonempty is not None:
        assertions.append(
            _js_check("responseNonempty", "nonempty_response", expected.response_nonempty)
        )
    assertions += [_phrase("icontains", p, "required_phrase") for p in expected.response_contains]
    assertions += [
        _phrase("not-icontains", p, "prohibited_phrase") for p in expected.response_not_contains
    ]
    return assertions


def _js_check(function: str, metric: str, expected: bool | Sequence[str]) -> dict[str, Any]:
    return {
        "type": "javascript",
        "value": f"{ASSERTIONS_FILE}:{function}",
        "config": {"expected": expected},
        "metric": metric,
    }


def _phrase(assertion_type: str, phrase: str, metric: str) -> dict[str, Any]:
    return {"type": assertion_type, "value": phrase, "transform": RESPONSE_FIELD, "metric": metric}
