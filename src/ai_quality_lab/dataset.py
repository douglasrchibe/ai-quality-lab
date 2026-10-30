"""Evaluation dataset schema, loading, and validation.

A dataset is a JSON object (schema_version 1)::

    {
      "schema_version": 1,
      "description": "optional free text",
      "cases": [
        {
          "id": "GR-001",                      # unique; letters, digits, _ . -
          "name": "Cooking blocked when guardrail enabled",
          "category": "positive",              # positive|negative|boundary|adversarial|regression
          "severity": "critical",              # critical | high | medium | low
          "input": "Give me a pasta recipe.",  # any string, including "" (empty-input cases)
          "configuration": {"guardrails": {"cooking": {"enabled": true}}},
          "expected": {                        # at least one of:
            "blocked": true,
            "detected_topics": ["cooking"],    # exact set, order-insensitive; [] means "no topics"
            "response_contains": ["can't help"],
            "response_not_contains": ["spaghetti"],
            "response_nonempty": true
          },
          "description": "What the scenario verifies and why.",
          "tags": ["guardrail", "cooking"]     # optional
        }
      ]
    }

The whole dataset is validated before anything runs. Every problem found is reported together in one
:class:`DatasetValidationError`, so the file can be fixed in a single pass.
"""

from __future__ import annotations

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ai_quality_lab.guardrails import GuardrailConfig

SCHEMA_VERSION = 1

_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")
_DATASET_KEYS = frozenset({"schema_version", "description", "cases"})
_REQUIRED_CASE_FIELDS = (
    "id",
    "name",
    "category",
    "severity",
    "input",
    "configuration",
    "expected",
    "description",
)
_OPTIONAL_CASE_FIELDS = ("tags",)
_EXPECTATION_KEYS = (
    "blocked",
    "detected_topics",
    "response_contains",
    "response_not_contains",
    "response_nonempty",
)

ConfigurationValidator = Callable[[Any], object]
"""Callable that raises ``ValueError`` if a case's agent configuration is invalid."""


class Category(str, Enum):
    POSITIVE = "positive"
    NEGATIVE = "negative"
    BOUNDARY = "boundary"
    ADVERSARIAL = "adversarial"
    REGRESSION = "regression"


class Severity(str, Enum):
    """Impact if the case fails. Set in the dataset; never inferred from the outcome."""

    CRITICAL = "critical"
    HIGH = "high"
    MEDIUM = "medium"
    LOW = "low"


class DatasetValidationError(ValueError):
    """The dataset is unusable as a whole. Raised before any case executes.

    ``problems`` lists every issue found, each prefixed with its location.
    """

    def __init__(self, problems: Sequence[str]):
        self.problems = tuple(problems)
        details = "\n".join(f"  - {problem}" for problem in self.problems)
        super().__init__(
            f"Evaluation dataset is invalid ({len(self.problems)} problem(s)):\n{details}"
        )


@dataclass(frozen=True)
class Expectations:
    """Expected behavior for one case. ``None`` or empty means "not asserted"."""

    blocked: bool | None = None
    detected_topics: tuple[str, ...] | None = None
    response_contains: tuple[str, ...] = ()
    response_not_contains: tuple[str, ...] = ()
    response_nonempty: bool | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {}
        if self.blocked is not None:
            data["blocked"] = self.blocked
        if self.detected_topics is not None:
            data["detected_topics"] = list(self.detected_topics)
        if self.response_contains:
            data["response_contains"] = list(self.response_contains)
        if self.response_not_contains:
            data["response_not_contains"] = list(self.response_not_contains)
        if self.response_nonempty is not None:
            data["response_nonempty"] = self.response_nonempty
        return data


@dataclass(frozen=True)
class EvalCase:
    """One validated evaluation scenario."""

    id: str
    name: str
    category: Category
    severity: Severity
    input: str
    configuration: Mapping[str, Any]
    expected: Expectations
    description: str
    tags: tuple[str, ...] = ()


def load_dataset(
    path: str | Path,
    *,
    validate_configuration: ConfigurationValidator | None = GuardrailConfig.from_dict,
) -> tuple[EvalCase, ...]:
    """Read and validate a dataset file.

    Args:
        path: JSON file to load (UTF-8).
        validate_configuration: Checks each case's ``configuration``; defaults to the mock
            chatbot's guardrail schema. Pass ``None`` to skip, e.g. for a different agent.

    Raises:
        FileNotFoundError / OSError: if the file cannot be read.
        DatasetValidationError: if the JSON is invalid or any case is malformed.
    """
    text = Path(path).read_text(encoding="utf-8")
    try:
        raw = json.loads(text, object_pairs_hook=_reject_duplicate_keys)
    except json.JSONDecodeError as exc:
        raise DatasetValidationError(
            [f"Invalid JSON at line {exc.lineno}, column {exc.colno}: {exc.msg}."]
        ) from exc
    except _DuplicateKeyError as exc:
        raise DatasetValidationError([str(exc)]) from exc
    return parse_dataset(raw, validate_configuration=validate_configuration)


def parse_dataset(
    raw: Any,
    *,
    validate_configuration: ConfigurationValidator | None = GuardrailConfig.from_dict,
) -> tuple[EvalCase, ...]:
    """Validate an already-decoded dataset object. See :func:`load_dataset`."""
    if not isinstance(raw, Mapping):
        raise DatasetValidationError(
            [f"Dataset must be a JSON object, got {_json_type(raw)}."]
        )

    problems: list[str] = []
    unknown = sorted(set(raw) - _DATASET_KEYS)
    if unknown:
        problems.append(f"Unsupported top-level key(s): {unknown}.")
    version = raw.get("schema_version")
    if type(version) is not int or version != SCHEMA_VERSION:
        problems.append(f"'schema_version' must be {SCHEMA_VERSION}, got {version!r}.")
    if "description" in raw and not isinstance(raw["description"], str):
        problems.append("'description' must be a string.")

    raw_cases = raw.get("cases")
    if not isinstance(raw_cases, list):
        problems.append(f"'cases' must be a list, got {_json_type(raw_cases)}.")
        raise DatasetValidationError(problems)
    if not raw_cases:
        problems.append("'cases' is empty; a dataset must contain at least one case.")

    cases: list[EvalCase] = []
    first_index_by_id: dict[str, int] = {}
    for index, raw_case in enumerate(raw_cases):
        location = f"cases[{index}]"
        case_id = raw_case.get("id") if isinstance(raw_case, Mapping) else None
        if isinstance(case_id, str):
            if case_id in first_index_by_id:
                problems.append(
                    f"{location}: duplicate id '{case_id}' "
                    f"(first used by cases[{first_index_by_id[case_id]}])."
                )
            else:
                first_index_by_id[case_id] = index
        case = _parse_case(raw_case, location, problems, validate_configuration)
        if case is not None:
            cases.append(case)

    if problems:
        raise DatasetValidationError(problems)
    return tuple(cases)


def _parse_case(
    raw: Any,
    location: str,
    problems: list[str],
    validate_configuration: ConfigurationValidator | None,
) -> EvalCase | None:
    if not isinstance(raw, Mapping):
        problems.append(f"{location}: case must be an object, got {_json_type(raw)}.")
        return None
    if isinstance(raw.get("id"), str):
        location = f"{location} (id '{raw['id']}')"

    errors: list[str] = []
    missing = [key for key in _REQUIRED_CASE_FIELDS if key not in raw]
    if missing:
        errors.append(f"missing required field(s): {missing}.")
    unknown = sorted(set(raw) - set(_REQUIRED_CASE_FIELDS) - set(_OPTIONAL_CASE_FIELDS))
    if unknown:
        errors.append(f"unsupported field(s): {unknown}.")

    case_id = _string_field(raw, "id", errors)
    if case_id is not None and not _ID_PATTERN.fullmatch(case_id):
        errors.append(f"'id' {case_id!r} may only contain letters, digits, '_', '.', '-'.")
    name = _string_field(raw, "name", errors)
    description = _string_field(raw, "description", errors)
    message = _string_field(raw, "input", errors, allow_blank=True)
    category = _enum_field(raw, "category", Category, errors)
    severity = _enum_field(raw, "severity", Severity, errors)
    tags = _tags(raw.get("tags", []), errors)
    configuration = _configuration(raw, errors, validate_configuration)
    expected = _expectations(raw["expected"], errors) if "expected" in raw else None

    if errors:
        problems.extend(f"{location}: {error}" for error in errors)
        return None
    return EvalCase(
        id=case_id,
        name=name,
        category=category,
        severity=severity,
        input=message,
        configuration=configuration,
        expected=expected,
        description=description,
        tags=tags,
    )


def _string_field(
    raw: Mapping[str, Any], key: str, errors: list[str], *, allow_blank: bool = False
) -> str | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, str):
        errors.append(f"'{key}' must be a string, got {_json_type(value)}.")
        return None
    if not allow_blank and not value.strip():
        errors.append(f"'{key}' must not be blank.")
        return None
    return value


def _enum_field(raw: Mapping[str, Any], key: str, enum_type: type[Enum], errors: list[str]) -> Any:
    if key not in raw:
        return None
    allowed = [member.value for member in enum_type]
    try:
        return enum_type(raw[key])
    except ValueError:
        errors.append(f"'{key}' must be one of {allowed}, got {raw[key]!r}.")
        return None


def _tags(value: Any, errors: list[str]) -> tuple[str, ...]:
    if not isinstance(value, list) or not all(isinstance(t, str) and t.strip() for t in value):
        errors.append("'tags' must be a list of non-blank strings.")
        return ()
    return tuple(value)


def _configuration(
    raw: Mapping[str, Any], errors: list[str], validate: ConfigurationValidator | None
) -> Mapping[str, Any] | None:
    if "configuration" not in raw:
        return None
    value = raw["configuration"]
    if not isinstance(value, Mapping):
        errors.append(f"'configuration' must be an object, got {_json_type(value)}.")
        return None
    if validate is not None:
        try:
            validate(value)
        except ValueError as exc:
            errors.append(f"invalid 'configuration': {exc}")
            return None
    return value


def _expectations(raw: Any, errors: list[str]) -> Expectations | None:
    if not isinstance(raw, Mapping):
        errors.append(f"'expected' must be an object, got {_json_type(raw)}.")
        return None
    count_before = len(errors)
    unknown = sorted(set(raw) - set(_EXPECTATION_KEYS))
    if unknown:
        errors.append(
            f"unsupported expectation(s) {unknown}; supported: {list(_EXPECTATION_KEYS)}."
        )
    if not set(raw) & set(_EXPECTATION_KEYS):
        errors.append("'expected' must define at least one supported expectation.")

    blocked = _optional_bool(raw, "blocked", errors)
    nonempty = _optional_bool(raw, "response_nonempty", errors)
    topics = _string_list(raw, "detected_topics", errors, allow_empty_list=True)
    contains = _string_list(raw, "response_contains", errors)
    not_contains = _string_list(raw, "response_not_contains", errors)

    required = {normalize_for_match(p) for p in contains or ()}
    prohibited = {normalize_for_match(p) for p in not_contains or ()}
    overlap = sorted(required & prohibited)
    if overlap:
        errors.append(f"phrase(s) {overlap} are both required and prohibited.")
    if nonempty is False and contains:
        errors.append("'response_nonempty' is false but 'response_contains' requires text.")

    if len(errors) > count_before:
        return None
    return Expectations(
        blocked=blocked,
        detected_topics=topics,
        response_contains=contains or (),
        response_not_contains=not_contains or (),
        response_nonempty=nonempty,
    )


def _optional_bool(raw: Mapping[str, Any], key: str, errors: list[str]) -> bool | None:
    if key not in raw:
        return None
    value = raw[key]
    if type(value) is not bool:
        errors.append(f"expectation '{key}' must be true or false, got {value!r}.")
        return None
    return value


def _string_list(
    raw: Mapping[str, Any], key: str, errors: list[str], *, allow_empty_list: bool = False
) -> tuple[str, ...] | None:
    if key not in raw:
        return None
    value = raw[key]
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        errors.append(f"expectation '{key}' must be a list of non-blank strings.")
        return None
    if not value and not allow_empty_list:
        errors.append(f"expectation '{key}' must list at least one entry.")
        return None
    folded = [normalize_for_match(v) for v in value]
    if len(set(folded)) != len(folded):
        errors.append(f"expectation '{key}' contains duplicate entries.")
        return None
    return tuple(value)


def normalize_for_match(text: str) -> str:
    """Normalization for phrase assertions: case-fold and collapse whitespace, nothing else.

    Punctuation, apostrophes, digits, and accents are preserved, so "can't" and "cant" differ.
    Shared by dataset validation and the evaluator so both agree on what counts as the same phrase.
    """
    return " ".join(text.casefold().split())


def _json_type(value: Any) -> str:
    names = {dict: "object", list: "array", str: "string", bool: "boolean", type(None): "null"}
    for py_type, name in names.items():
        if isinstance(value, py_type):
            return name
    if isinstance(value, (int, float)):
        return "number"
    return type(value).__name__


class _DuplicateKeyError(ValueError):
    pass


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    # The json module silently keeps the last duplicate key, which would hide a conflicting value.
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise _DuplicateKeyError(f"Duplicate JSON key {key!r} in the same object.")
        result[key] = value
    return result
