"""Tests for the real-LLM evaluation dataset schema and the repository dataset."""

import copy
import json
from pathlib import Path

import pytest

from ai_quality_lab.dataset import DatasetValidationError
from ai_quality_lab.guardrails import KeywordTopicDetector
from ai_quality_lab.llm_dataset import LLMCategory, load_llm_dataset, parse_llm_dataset

REPO_LLM_DATASET = Path(__file__).resolve().parents[1] / "evals" / "llm_cases.json"

BASE_CASE = {
    "id": "T-1",
    "name": "Test",
    "category": "informational",
    "severity": "high",
    "input": "What causes the seasons?",
    "configuration": {"guardrails": {"cooking": {"enabled": True}}},
    "expected_behavior": "Explains axial tilt.",
    "criteria": ["Check the response mentions axial tilt."],
    "metrics": ["behavior"],
}


def dataset(*cases):
    return {"schema_version": 1, "cases": list(cases)}


def case(**overrides):
    data = copy.deepcopy(BASE_CASE)
    for key, value in overrides.items():
        if value is None:
            data.pop(key, None)
        else:
            data[key] = value
    return data


def problems(raw):
    with pytest.raises(DatasetValidationError) as excinfo:
        parse_llm_dataset(raw)
    return " | ".join(excinfo.value.problems)


# --- Repository dataset -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def repo_cases():
    return load_llm_dataset(REPO_LLM_DATASET)


def test_repository_dataset_is_valid_and_covers_every_category(repo_cases):
    assert len(repo_cases) >= 20
    assert {c.category for c in repo_cases} == set(LLMCategory)
    assert len({c.id for c in repo_cases}) == len(repo_cases)


def test_keyword_guardrail_matches_each_case_expectation(repo_cases):
    # Prevents an accidental cooking keyword from turning a model case into a blocked one.
    detector = KeywordTopicDetector()
    for c in repo_cases:
        enabled = c.configuration["guardrails"]["cooking"]["enabled"]
        will_block = enabled and "cooking" in detector.detect(c.input)
        assert will_block == (c.checks.blocked is True), c.id


def test_bypass_cases_really_bypass_the_keyword_layer(repo_cases):
    bypass = [c for c in repo_cases if c.category is LLMCategory.GUARDRAIL_BYPASS]
    assert bypass
    assert all(KeywordTopicDetector().detect(c.input) == () for c in bypass)


def test_critical_bypass_cases_have_a_deterministic_backstop(repo_cases):
    # A critical safety verdict must not rest on the LLM judge alone (Phase 5.5 audit).
    for c in repo_cases:
        if c.category is LLMCategory.GUARDRAIL_BYPASS and c.severity.value == "critical":
            assert c.checks.response_not_contains, c.id
            assert c.checks.blocked is False, c.id


def test_smoke_subset_exists(repo_cases):
    smoke = [c for c in repo_cases if "smoke" in c.tags]
    assert 3 <= len(smoke) <= 8


# --- Validation ---------------------------------------------------------------------------------


def test_valid_case_parses():
    (parsed,) = parse_llm_dataset(dataset(case()))
    assert parsed.metrics == ("behavior",)
    assert parsed.checks.is_empty()


@pytest.mark.parametrize(
    "overrides, fragment",
    [
        ({"metrics": ["faithfulness"], "criteria": None}, "require a non-empty 'context'"),
        ({"criteria": None}, "requires 'criteria'"),
        ({"metrics": ["vibes"]}, "unsupported metric(s) ['vibes']"),
        ({"metrics": ["behavior", "behavior"]}, "duplicates"),
        ({"metrics": [], "criteria": None}, "at least one metric or deterministic check"),
        ({"checks": {"blocked": True}}, "must not list metrics"),
        ({"checks": {"blocked": "yes"}}, "check 'blocked' must be true or false"),
        ({"checks": {"max_words": 0}}, "'max_words' must be a positive integer"),
        ({"checks": {"length": 3}}, "unsupported check(s) ['length']"),
        ({"checks": {"response_contains": ["A"], "response_not_contains": ["a"]}},
         "both required and prohibited"),
        ({"category": "trivia"}, "'category' must be one of"),
        ({"severity": "urgent"}, "'severity' must be one of"),
        ({"configuration": {"guardrails": {}}}, "invalid 'configuration'"),
        ({"expected_behavior": " "}, "'expected_behavior' must be a non-blank string"),
        ({"surprise": 1}, "unsupported field(s): ['surprise']"),
        ({"input": None}, "missing required field(s): ['input']"),
    ],
)
def test_invalid_cases_rejected(overrides, fragment):
    assert fragment in problems(dataset(case(**overrides)))


def test_duplicate_ids_and_multiple_problems_reported_together():
    message = problems(dataset(case(), case(), case(id="T-2", severity="x")))
    assert "duplicate id 'T-1'" in message
    assert "(id 'T-2'): 'severity'" in message


def test_invalid_json_rejected(tmp_path):
    bad = tmp_path / "bad.json"
    bad.write_text("{nope", encoding="utf-8")
    with pytest.raises(DatasetValidationError, match="Invalid JSON"):
        load_llm_dataset(bad)


def test_round_trip_from_file(tmp_path):
    path = tmp_path / "cases.json"
    path.write_text(json.dumps(dataset(case())), encoding="utf-8")
    assert load_llm_dataset(path)[0].id == "T-1"
