"""Shared fixtures for building evaluation cases in tests."""

import copy
from pathlib import Path

import pytest

from ai_quality_lab.dataset import parse_dataset

REPO_DATASET = Path(__file__).resolve().parents[1] / "evals" / "guardrail_cases.json"
BLOCKING_CONFIG = {"guardrails": {"cooking": {"enabled": True}}}


def _raw_case(**overrides):
    case = {
        "id": "T-001",
        "name": "Test case",
        "category": "positive",
        "severity": "high",
        "input": "Give me a pasta recipe.",
        "configuration": copy.deepcopy(BLOCKING_CONFIG),
        "expected": {"blocked": True},
        "description": "Test scenario.",
        "tags": ["test"],
    }
    case.update(overrides)
    return case


@pytest.fixture
def raw_case():
    """Factory for a valid raw (JSON-shaped) case; keyword arguments override fields."""
    return _raw_case


@pytest.fixture
def make_case():
    """Factory for a validated EvalCase; keyword arguments override raw fields."""

    def build(**overrides):
        return parse_dataset({"schema_version": 1, "cases": [_raw_case(**overrides)]})[0]

    return build


@pytest.fixture
def repo_dataset_path() -> Path:
    return REPO_DATASET
