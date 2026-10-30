"""Unit tests for ai_quality_lab.dataset: loading and validating evaluation datasets."""

import json

import pytest

from ai_quality_lab.dataset import (
    Category,
    DatasetValidationError,
    EvalCase,
    Expectations,
    Severity,
    load_dataset,
    parse_dataset,
)


def dataset(*cases, **top_level):
    return {"schema_version": 1, "cases": list(cases), **top_level}


def validation_problems(raw) -> tuple[str, ...]:
    with pytest.raises(DatasetValidationError) as excinfo:
        parse_dataset(raw)
    return excinfo.value.problems


# --- Valid datasets -----------------------------------------------------------------------------


def test_repository_dataset_is_valid_and_complete(repo_dataset_path):
    cases = load_dataset(repo_dataset_path)
    assert len(cases) >= 20
    assert len({c.id for c in cases}) == len(cases)
    assert {c.category for c in cases} == set(Category)
    assert {c.severity for c in cases} == set(Severity)


def test_valid_dataset_file_loads(tmp_path, raw_case):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(dataset(raw_case())), encoding="utf-8")
    (case,) = load_dataset(path)
    assert isinstance(case, EvalCase)
    assert case.id == "T-001"
    assert case.category is Category.POSITIVE
    assert case.severity is Severity.HIGH
    assert case.expected == Expectations(blocked=True)
    assert case.tags == ("test",)


def test_tags_are_optional(raw_case):
    raw = raw_case()
    del raw["tags"]
    assert parse_dataset(dataset(raw))[0].tags == ()


def test_empty_input_string_is_allowed(raw_case):
    (case,) = parse_dataset(dataset(raw_case(input="", expected={"blocked": False})))
    assert case.input == ""


def test_all_expectation_types_parse(raw_case):
    expected = {
        "blocked": False,
        "detected_topics": [],
        "response_contains": ["Paris"],
        "response_not_contains": ["recipe"],
        "response_nonempty": True,
    }
    (case,) = parse_dataset(dataset(raw_case(expected=expected)))
    assert case.expected.detected_topics == ()
    assert case.expected.to_dict() == expected


# --- File-level errors --------------------------------------------------------------------------


@pytest.mark.parametrize("text", ["{not json", "", '{"schema_version": 1, "cases": [}'])
def test_invalid_json_rejected(tmp_path, text):
    path = tmp_path / "bad.json"
    path.write_text(text, encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="Invalid JSON at line"):
        load_dataset(path)


def test_duplicate_json_keys_rejected(tmp_path):
    path = tmp_path / "dup.json"
    path.write_text('{"schema_version": 1, "schema_version": 1, "cases": []}', encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="Duplicate JSON key 'schema_version'"):
        load_dataset(path)


def test_missing_file_raises_file_not_found(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_dataset(tmp_path / "missing.json")


# --- Top-level structure ------------------------------------------------------------------------


@pytest.mark.parametrize(
    "raw, fragment",
    [
        ([], "must be a JSON object"),
        ({"cases": []}, "'schema_version' must be 1"),
        ({"schema_version": True, "cases": [{}]}, "'schema_version' must be 1"),
        ({"schema_version": 2, "cases": [{}]}, "'schema_version' must be 1"),
        ({"schema_version": 1, "cases": {}}, "'cases' must be a list"),
        ({"schema_version": 1}, "'cases' must be a list"),
        ({"schema_version": 1, "cases": []}, "'cases' is empty"),
        ({"schema_version": 1, "cases": [], "extra": 1}, "Unsupported top-level key"),
    ],
)
def test_malformed_top_level_rejected(raw, fragment):
    problems = validation_problems(raw)
    assert any(fragment in p for p in problems), problems


# --- Case-level errors --------------------------------------------------------------------------


def test_missing_required_fields_are_all_reported(raw_case):
    raw = raw_case()
    for key in ("name", "severity", "expected"):
        del raw[key]
    (problem,) = validation_problems(dataset(raw))
    assert "cases[0] (id 'T-001')" in problem
    assert "missing required field(s): ['name', 'severity', 'expected']" in problem


def test_duplicate_ids_rejected(raw_case):
    problems = validation_problems(dataset(raw_case(), raw_case()))
    assert problems == ("cases[1]: duplicate id 'T-001' (first used by cases[0]).",)


@pytest.mark.parametrize("severity", ["CRITICAL", "blocker", "", None, 1])
def test_invalid_severity_rejected(raw_case, severity):
    (problem,) = validation_problems(dataset(raw_case(severity=severity)))
    assert "'severity' must be one of ['critical', 'high', 'medium', 'low']" in problem


@pytest.mark.parametrize("category", ["smoke", "Positive", None])
def test_invalid_category_rejected(raw_case, category):
    (problem,) = validation_problems(dataset(raw_case(category=category)))
    assert "'category' must be one of" in problem


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"id": "bad id!"}, "'id' 'bad id!' may only contain"),
        ({"id": 7}, "'id' must be a string, got number"),
        ({"name": "  "}, "'name' must not be blank"),
        ({"input": None}, "'input' must be a string, got null"),
        ({"description": ["x"]}, "'description' must be a string, got array"),
        ({"tags": "cooking"}, "'tags' must be a list of non-blank strings"),
        ({"tags": ["ok", ""]}, "'tags' must be a list of non-blank strings"),
        ({"configuration": "cooking=on"}, "'configuration' must be an object"),
        ({"unexpected": 1}, "unsupported field(s): ['unexpected']"),
    ],
)
def test_incorrect_field_types_rejected(raw_case, overrides, fragment):
    (problem,) = validation_problems(dataset(raw_case(**overrides)))
    assert fragment in problem


@pytest.mark.parametrize(
    "expected, fragment",
    [
        ({"blocked": "yes"}, "expectation 'blocked' must be true or false"),
        ({"blocked": 1}, "expectation 'blocked' must be true or false"),
        ({"response_nonempty": "true"}, "expectation 'response_nonempty' must be true or false"),
        ({"detected_topics": "cooking"}, "'detected_topics' must be a list of non-blank strings"),
        ({"detected_topics": ["cooking", "cooking"]}, "'detected_topics' contains duplicate"),
        ({"response_contains": "Paris"}, "'response_contains' must be a list of non-blank"),
        ({"response_contains": [""]}, "'response_contains' must be a list of non-blank"),
        ({"response_contains": []}, "'response_contains' must list at least one entry"),
        ({"response_not_contains": [3]}, "'response_not_contains' must be a list of non-blank"),
        ({"outcome": "blocked"}, "unsupported expectation(s) ['outcome']"),
        ({}, "must define at least one supported expectation"),
        (["blocked"], "'expected' must be an object, got array"),
    ],
)
def test_invalid_expectations_rejected(raw_case, expected, fragment):
    problems = validation_problems(dataset(raw_case(expected=expected)))
    assert any(fragment in p for p in problems), problems


def test_contradictory_phrases_rejected(raw_case):
    expected = {"response_contains": ["Paris"], "response_not_contains": ["  PARIS "]}
    (problem,) = validation_problems(dataset(raw_case(expected=expected)))
    assert "['paris'] are both required and prohibited" in problem


def test_nonempty_false_with_required_phrase_rejected(raw_case):
    expected = {"response_nonempty": False, "response_contains": ["Paris"]}
    (problem,) = validation_problems(dataset(raw_case(expected=expected)))
    assert "'response_nonempty' is false" in problem


@pytest.mark.parametrize(
    "configuration",
    [
        {},
        {"guardrails": {}},
        {"guardrails": {"cooking": {"enabled": "yes"}}},
        {"guardrails": {"cooking": {"enabled": True, "threshold": 100}}},
    ],
)
def test_invalid_guardrail_configuration_rejected(raw_case, configuration):
    (problem,) = validation_problems(dataset(raw_case(configuration=configuration)))
    assert "invalid 'configuration'" in problem


def test_configuration_validation_can_be_skipped_for_other_agents(raw_case):
    raw = dataset(raw_case(configuration={"model": "other-agent"}))
    (case,) = parse_dataset(raw, validate_configuration=None)
    assert case.configuration == {"model": "other-agent"}


def test_multiple_problems_reported_together(raw_case):
    raw = dataset(
        raw_case(id="A", severity="urgent"),
        raw_case(id="B", expected={"blocked": "no"}),
        "not a case",
        raw_case(id="A"),
    )
    problems = validation_problems(raw)
    assert len(problems) == 4
    assert any("(id 'A')" in p and "severity" in p for p in problems)
    assert any("(id 'B')" in p and "blocked" in p for p in problems)
    assert any(p.startswith("cases[2]: case must be an object") for p in problems)
    assert any("duplicate id 'A'" in p for p in problems)


def test_error_message_lists_every_problem(raw_case):
    with pytest.raises(DatasetValidationError) as excinfo:
        parse_dataset(dataset(raw_case(severity="x"), raw_case(id="T-002", category="y")))
    message = str(excinfo.value)
    assert message.startswith("Evaluation dataset is invalid (2 problem(s)):")
    assert "severity" in message and "category" in message


def test_validation_error_is_a_value_error():
    assert issubclass(DatasetValidationError, ValueError)
