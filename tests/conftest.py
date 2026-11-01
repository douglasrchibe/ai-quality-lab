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


REPO_KNOWLEDGE_BASE = Path(__file__).resolve().parents[1] / "data" / "knowledge_base"


@pytest.fixture(scope="session")
def repo_documents():
    from ai_quality_lab.rag.documents import load_documents

    return load_documents(REPO_KNOWLEDGE_BASE)


def _record(evaluation_type="custom_engine", run_id="run-1", *, metrics=None, unmeasured=None,
            cases=None, identity=None, parameters=None, status="PASS", deterministic=True,
            recorded_at="2026-11-01T10:00:00Z", case_count=None):
    """A valid quality-observability record (synthetic; schema version 1)."""
    from ai_quality_lab.observability.records import SCHEMA_VERSION, metric, short_hash

    if metrics is None:
        metrics = {"pass_rate": metric(1.0, numerator=4, denominator=4),
                   "critical_failures": metric(0, numerator=0, denominator=2)}
    if cases is None:
        cases = [{"id": f"C-{i}", "outcome": "PASS", "severity": s}
                 for i, s in enumerate(("critical", "critical", "high", "medium"))]
    full_identity = {"evaluation_type": evaluation_type, "dataset_fingerprint": "d1",
                     **(identity or {})}
    return {
        "schema_version": SCHEMA_VERSION, "run_id": run_id, "recorded_at": recorded_at,
        "evaluation_type": evaluation_type, "deterministic": deterministic,
        "source": {"commit": "abc1234", "branch": "main", "origin": "local", "ci_run_id": None,
                   "report_file": "r.json", "note": None},
        "dataset": {"path": "evals/x.json", "fingerprint": "d1",
                    "case_count": len(cases) if case_count is None else case_count,
                    "corpus_fingerprint": None},
        "identity": full_identity, "comparability_key": short_hash(full_identity),
        "parameters": {"simulated_failure": "none", **(parameters or {})},
        "thresholds": {}, "status": status, "metrics": metrics,
        "unmeasured": unmeasured or {}, "cases": cases,
    }


@pytest.fixture
def make_record():
    """Factory for a valid observability record; keyword arguments override parts of it."""
    return _record
