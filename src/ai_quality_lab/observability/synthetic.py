"""SYNTHETIC live-run records for tests and the example calibration report. Phase 9.

Every record is marked ``source.origin = "synthetic"``. Baseline approval and live batches refuse
them, and reports label them "SYNTHETIC: not live evidence". They are shaped like real ``rag_live``
records (25 cases, 19 answerable) so the analysis code is exercised end to end.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from ai_quality_lab.observability.records import (
    SCHEMA_VERSION,
    blocking_reasons,
    metric,
    rate,
    short_hash,
)

SEVERITIES = (["critical"] * 6) + (["high"] * 10) + (["medium"] * 7) + (["low"] * 2)
CASE_IDS = [f"RAG-{i:03d}" for i in range(1, 26)]
ANSWERABLE = set(CASE_IDS[:19])
OVER_ABSTAINING = ("RAG-007", "RAG-009", "RAG-010", "RAG-015")  # fail by abstaining, every run
SYNTHETIC_IDENTITY = {
    "evaluation_type": "rag_live", "dataset_fingerprint": "synthetic-dataset",
    "corpus_fingerprint": "synthetic-corpus", "llm": "ollama:synthetic-model",
    "embedder": "ollama:synthetic-embedder",
    "generation": {"temperature": 0, "seed": 42, "max_tokens": 300},
    "ollama_version": "0.0.0-synthetic", "model_digests": {"synthetic-model": "0" * 64},
}


def synthetic_run(run_id: str, *, fail: Sequence[str] = (), abstain: Sequence[str] = (),
                  safety: Sequence[str] = (), seed: int = 42, batch: str = "synthetic",
                  purpose: str = "official", recorded_at: str = "2026-11-01T12:00:00Z",
                  case_ids: Sequence[str] = CASE_IDS,
                  identity: Mapping[str, Any] | None = None) -> dict[str, Any]:
    """One synthetic rag_live record. ``fail``/``abstain``/``safety`` add to the fixed pattern."""
    failing = set(OVER_ABSTAINING) | set(fail) | set(safety)
    abstaining = set(OVER_ABSTAINING) | set(abstain)
    cases = [{"id": cid, "severity": SEVERITIES[i], "outcome": "FAIL" if cid in failing else "PASS",
              "answerable": cid in ANSWERABLE, "abstained": cid in abstaining,
              "safety_failure": cid in safety}
             for i, cid in enumerate(CASE_IDS) if cid in case_ids]
    metrics: dict[str, Any] = {}
    unmeasured: dict[str, str] = {}
    total = len(cases)
    passed = sum(1 for c in cases if c["outcome"] == "PASS")
    metrics["case_count"] = metric(total)
    rate(passed, total, "pass_rate", metrics, unmeasured)
    metrics["error_count"] = metric(0, numerator=0, denominator=total)
    for severity in ("critical", "high", "medium"):
        scoped = [c for c in cases if c["severity"] == severity]
        failed = sum(1 for c in scoped if c["outcome"] == "FAIL")
        metrics[f"{severity}_failures"] = metric(failed, numerator=failed, denominator=len(scoped))
    unsafe = sum(1 for c in cases if c["safety_failure"])
    metrics["safety_failures"] = metric(unsafe, numerator=unsafe, denominator=total)
    metrics["restricted_retrievals"] = metric(0, numerator=0, denominator=total)
    for name, value in (("doc_hit_rate", 1.0), ("doc_recall", 1.0), ("doc_mrr", 0.9211)):
        metrics[name] = metric(value, denominator=19)
    answerable = [c for c in cases if c["answerable"]]
    rate(sum(1 for c in answerable if c["abstained"]), len(answerable),
         "answerable_abstention_rate", metrics, unmeasured)
    rate(sum(1 for c in answerable if c["abstained"] and c["outcome"] == "FAIL"),
         len(answerable), "over_abstention_rate", metrics, unmeasured)
    full_identity = dict(identity or SYNTHETIC_IDENTITY)
    full_identity["generation"] = {**full_identity["generation"], "seed": seed}
    reasons = blocking_reasons(metrics, {"hit_rate": 0.9, "recall": 0.8})
    return {
        "schema_version": SCHEMA_VERSION, "run_id": run_id, "recorded_at": recorded_at,
        "evaluation_type": "rag_live", "deterministic": False,
        "source": {"commit": "synthetic", "branch": "synthetic", "origin": "synthetic",
                   "ci_run_id": None, "report_file": "synthetic", "note": "SYNTHETIC",
                   "report_sha256": short_hash([run_id, sorted(failing), sorted(abstaining)]),
                   "uncommitted_changes": False, "batch": batch, "purpose": purpose},
        "dataset": {"path": "synthetic", "fingerprint": "synthetic-dataset",
                    "case_count": total, "corpus_fingerprint": "synthetic-corpus"},
        "identity": full_identity, "comparability_key": short_hash(full_identity),
        "parameters": {"top_k": 4, "chunk_size": 40, "overlap": 10, "simulated_failure": "none"},
        "thresholds": {}, "status": "BLOCKED" if reasons else "PASS",
        "metrics": metrics, "unmeasured": unmeasured, "cases": cases,
    }


def example_batch() -> list[dict[str, Any]]:
    """The batch behind docs/live-calibration-example.md: 5 synthetic runs, two unstable cases."""
    return [
        synthetic_run("synthetic-run-1"),
        synthetic_run("synthetic-run-2", fail=["RAG-012"]),
        synthetic_run("synthetic-run-3"),
        synthetic_run("synthetic-run-4", abstain=["RAG-016"], fail=["RAG-016"]),
        synthetic_run("synthetic-run-5", fail=["RAG-012"]),
    ]
