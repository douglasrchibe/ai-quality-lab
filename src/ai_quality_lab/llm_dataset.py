"""Schema, loading, and validation for real-LLM evaluation cases (``evals/llm_cases.json``).

Unlike the guardrail dataset, expected results here describe *behavior*, because a real model's
wording varies between runs. Each case combines:

* ``metrics``: DeepEval (LLM-as-a-judge) metrics to run; see ``METRICS``.
* ``criteria``: evaluation steps for the ``behavior`` metric (DeepEval G-Eval).
* ``checks``: optional deterministic checks, used only where they are robust (blocked status,
  word limits, phrases that must not leak).

Example::

    {
      "id": "LLM-001", "name": "...", "category": "informational", "severity": "medium",
      "input": "What causes the seasons on Earth?",
      "configuration": {"guardrails": {"cooking": {"enabled": true}}},
      "expected_behavior": "Explains axial tilt; does not attribute seasons to distance.",
      "criteria": ["Check the response attributes seasons to the tilt of Earth's axis.", "..."],
      "context": ["optional reference text given to the model"],
      "metrics": ["answer_relevancy", "behavior"],
      "checks": {"blocked": false, "max_words": 150, "response_not_contains": ["..."]},
      "constraints": ["human-readable constraints shown in reports"],
      "tags": ["smoke"]
    }
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any

from ai_quality_lab.dataset import DatasetValidationError, Severity, normalize_for_match
from ai_quality_lab.guardrails import GuardrailConfig

SCHEMA_VERSION = 1
DEFAULT_LLM_DATASET_PATH = Path("evals") / "llm_cases.json"

METRICS = (
    "answer_relevancy",
    "faithfulness",
    "hallucination",
    "contextual_relevancy",
    "behavior",
    "toxicity",
    "bias",
)
CONTEXT_METRICS = frozenset({"faithfulness", "hallucination", "contextual_relevancy"})
_CHECK_KEYS = ("blocked", "response_nonempty", "response_contains", "response_not_contains",
               "max_words")
_REQUIRED = ("id", "name", "category", "severity", "input", "configuration", "expected_behavior")
_OPTIONAL = ("criteria", "context", "metrics", "checks", "constraints", "tags")
_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


class LLMCategory(str, Enum):
    INFORMATIONAL = "informational"
    CONCISE = "concise"
    MULTI_PART = "multi_part"
    AMBIGUOUS = "ambiguous"
    INSUFFICIENT_INFORMATION = "insufficient_information"
    HALLUCINATION_RISK = "hallucination_risk"
    CONTRADICTORY_INSTRUCTIONS = "contradictory_instructions"
    GUARDRAIL_TRIGGER = "guardrail_trigger"
    GUARDRAIL_BYPASS = "guardrail_bypass"
    NORMAL = "normal"
    UNCERTAINTY = "uncertainty"
    SCOPE = "scope"
    SAFETY = "safety"


@dataclass(frozen=True)
class LLMChecks:
    blocked: bool | None = None
    response_nonempty: bool | None = None
    response_contains: tuple[str, ...] = ()
    response_not_contains: tuple[str, ...] = ()
    max_words: int | None = None

    def is_empty(self) -> bool:
        return self == LLMChecks()

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        for key in _CHECK_KEYS:
            value = getattr(self, key)
            if value is not None and value != ():
                data[key] = list(value) if isinstance(value, tuple) else value
        return data


@dataclass(frozen=True)
class LLMCase:
    id: str
    name: str
    category: LLMCategory
    severity: Severity
    input: str
    configuration: Mapping[str, Any]
    expected_behavior: str
    criteria: tuple[str, ...] = ()
    context: tuple[str, ...] = ()
    metrics: tuple[str, ...] = ()
    checks: LLMChecks = field(default_factory=LLMChecks)
    constraints: tuple[str, ...] = ()
    tags: tuple[str, ...] = ()


def load_llm_dataset(path: str | Path = DEFAULT_LLM_DATASET_PATH) -> tuple[LLMCase, ...]:
    """Read and validate an LLM dataset; every problem is reported in one error."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DatasetValidationError(
            [f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}."]
        ) from exc
    return parse_llm_dataset(raw)


def parse_llm_dataset(raw: Any) -> tuple[LLMCase, ...]:
    if not isinstance(raw, Mapping):
        raise DatasetValidationError(["Dataset must be a JSON object."])
    problems: list[str] = []
    if raw.get("schema_version") != SCHEMA_VERSION or type(raw.get("schema_version")) is not int:
        problems.append(f"'schema_version' must be {SCHEMA_VERSION}.")
    unknown = sorted(set(raw) - {"schema_version", "description", "cases"})
    if unknown:
        problems.append(f"Unsupported top-level key(s): {unknown}.")
    cases_raw = raw.get("cases")
    if not isinstance(cases_raw, list) or not cases_raw:
        problems.append("'cases' must be a non-empty list.")
        raise DatasetValidationError(problems)

    cases: list[LLMCase] = []
    seen: dict[str, int] = {}
    for index, item in enumerate(cases_raw):
        where = f"cases[{index}]"
        if isinstance(item, Mapping) and isinstance(item.get("id"), str):
            if item["id"] in seen:
                problems.append(f"{where}: duplicate id '{item['id']}' (first used by "
                                f"cases[{seen[item['id']]}]).")
            seen.setdefault(item["id"], index)
            where = f"{where} (id '{item['id']}')"
        errors: list[str] = []
        case = _parse_case(item, errors)
        problems.extend(f"{where}: {e}" for e in errors)
        if case is not None and not errors:
            cases.append(case)
    if problems:
        raise DatasetValidationError(problems)
    return tuple(cases)


def _parse_case(raw: Any, errors: list[str]) -> LLMCase | None:
    if not isinstance(raw, Mapping):
        errors.append("case must be an object.")
        return None
    missing = [k for k in _REQUIRED if k not in raw]
    if missing:
        errors.append(f"missing required field(s): {missing}.")
    unknown = sorted(set(raw) - set(_REQUIRED) - set(_OPTIONAL))
    if unknown:
        errors.append(f"unsupported field(s): {unknown}.")
    if missing:
        return None

    case_id = _text(raw, "id", errors)
    name = _text(raw, "name", errors)
    message = _text(raw, "input", errors)
    expected_behavior = _text(raw, "expected_behavior", errors)
    constraints = _text_list(raw, "constraints", errors)
    tags = _text_list(raw, "tags", errors)
    if case_id and not _ID_PATTERN.fullmatch(case_id):
        errors.append(f"'id' {case_id!r} may only contain letters, digits, '_', '.', '-'.")
    category = _enum(raw, "category", LLMCategory, errors)
    severity = _enum(raw, "severity", Severity, errors)
    configuration = raw["configuration"]
    try:
        GuardrailConfig.from_dict(configuration)
    except ValueError as exc:
        errors.append(f"invalid 'configuration': {exc}")
    criteria = _text_list(raw, "criteria", errors)
    context = _text_list(raw, "context", errors)
    metrics = _text_list(raw, "metrics", errors)
    unknown_metrics = sorted(set(metrics) - set(METRICS))
    if unknown_metrics:
        errors.append(f"unsupported metric(s) {unknown_metrics}; supported: {list(METRICS)}.")
    if len(set(metrics)) != len(metrics):
        errors.append("'metrics' contains duplicates.")
    checks = _checks(raw.get("checks", {}), errors)

    needs_context = sorted(set(metrics) & CONTEXT_METRICS)
    if needs_context and not context:
        errors.append(f"metric(s) {needs_context} require a non-empty 'context'.")
    if "behavior" in metrics and not criteria:
        errors.append("metric 'behavior' requires 'criteria' (a list of evaluation steps).")
    if checks is not None and checks.blocked is True and metrics:
        errors.append("a case expected to be blocked must not list metrics: the model is never "
                      "called, so there is nothing for a judge to evaluate.")
    if not metrics and (checks is None or checks.is_empty()):
        errors.append("define at least one metric or deterministic check.")

    if errors:
        return None
    return LLMCase(
        id=case_id,
        name=name,
        category=category,
        severity=severity,
        input=message,
        configuration=configuration,
        expected_behavior=expected_behavior,
        criteria=criteria,
        context=context,
        metrics=metrics,
        checks=checks,
        constraints=constraints,
        tags=tags,
    )


def _text(raw: Mapping[str, Any], key: str, errors: list[str]) -> str:
    value = raw.get(key)
    if not isinstance(value, str) or not value.strip():
        errors.append(f"'{key}' must be a non-blank string.")
        return ""
    return value


def _enum(raw: Mapping[str, Any], key: str, enum_type: type[Enum], errors: list[str]) -> Any:
    try:
        return enum_type(raw.get(key))
    except ValueError:
        errors.append(f"'{key}' must be one of {[m.value for m in enum_type]}, "
                      f"got {raw.get(key)!r}.")
        return None


def _text_list(raw: Mapping[str, Any], key: str, errors: list[str]) -> tuple[str, ...]:
    value = raw.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        errors.append(f"'{key}' must be a list of non-blank strings.")
        return ()
    return tuple(value)


def _checks(raw: Any, errors: list[str]) -> LLMChecks | None:
    if not isinstance(raw, Mapping):
        errors.append("'checks' must be an object.")
        return None
    unknown = sorted(set(raw) - set(_CHECK_KEYS))
    if unknown:
        errors.append(f"unsupported check(s) {unknown}; supported: {list(_CHECK_KEYS)}.")
    for key in ("blocked", "response_nonempty"):
        if key in raw and type(raw[key]) is not bool:
            errors.append(f"check '{key}' must be true or false.")
    max_words = raw.get("max_words")
    if max_words is not None and (type(max_words) is not int or max_words < 1):
        errors.append("check 'max_words' must be a positive integer.")
    contains = _text_list(raw, "response_contains", errors)
    not_contains = _text_list(raw, "response_not_contains", errors)
    overlap = {normalize_for_match(p) for p in contains} & {
        normalize_for_match(p) for p in not_contains
    }
    if overlap:
        errors.append(f"phrase(s) {sorted(overlap)} are both required and prohibited.")
    return LLMChecks(
        blocked=raw.get("blocked"),
        response_nonempty=raw.get("response_nonempty"),
        response_contains=contains,
        response_not_contains=not_contains,
        max_words=max_words,
    )
