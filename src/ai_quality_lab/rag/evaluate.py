"""RAG evaluation runner: retrieval metrics, answer checks, failure classes, release gates.

    python -m ai_quality_lab.rag.evaluate --mode retrieval                 # no LLM needed
    python -m ai_quality_lab.rag.evaluate --mode rag --llm mock-gullible   # offline end to end
    python -m ai_quality_lab.rag.evaluate --mode rag --llm ollama          # real model

Release policy (deterministic gates only):
* BLOCKED if any critical case FAILs, any case ERRORs, or no cases ran (shared
  ``decide_release_readiness``, same as the custom engine and the DeepEval suite);
* BLOCKED if document-level Hit Rate@K < 0.9 or Recall@K < 0.8 over answerable questions;
* BLOCKED if a restricted document was ever retrieved.
Ragas (LLM-judged) metrics never gate: they run separately in ``.venv-ragas`` and are advisory
until their judge passes calibration.

Exit codes: 0 = READY, 1 = BLOCKED, 2 = setup or configuration error.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_quality_lab.dataset import DatasetValidationError, Severity
from ai_quality_lab.evaluator import (
    AssertionResult,
    CaseOutcome,
    ErrorDetails,
    ReleaseReadiness,
    decide_release_readiness,
)
from ai_quality_lab.llm_client import (
    LLMError,
    LLMSettings,
    OpenAICompatibleClient,
    check_model_available,
    load_env_file,
)
from ai_quality_lab.rag.chatbot import RAGChatbot, RAGResponse, RAGSimulatedFailure
from ai_quality_lab.rag.chunking import Chunk, ChunkingConfig
from ai_quality_lab.rag.dataset import (
    DEFAULT_RAG_DATASET,
    RAGCase,
    load_rag_dataset,
    relevant_chunk_ids,
)
from ai_quality_lab.rag.documents import DEFAULT_KNOWLEDGE_BASE, DocumentError, load_documents
from ai_quality_lab.rag.embeddings import HashingEmbedder, embedder_from_env
from ai_quality_lab.rag.failures import (
    FailureClassification,
    FailureType,
    answer_checks,
    classify_failures,
)
from ai_quality_lab.rag.index import load_or_build_index
from ai_quality_lab.rag.mock_llm import ExtractiveMockLLM, GullibleMockLLM
from ai_quality_lab.rag.retrieval import DEFAULT_TOP_K, RetrievalResult, Retriever
from ai_quality_lab.rag.retrieval_metrics import RankingMetrics, aggregate, compute_metrics
from ai_quality_lab.rag.vector_store import VectorIndexError

RETRIEVAL_GATES = {"hit_rate": 0.9, "recall": 0.8}
"""Minimum document-level aggregates over answerable questions. Fixed before measuring."""
DEFAULT_OUTPUT = Path("reports") / "rag" / "latest.json"


@dataclass(frozen=True)
class RAGCaseResult:
    case: RAGCase
    outcome: CaseOutcome
    checks: tuple[AssertionResult, ...] = ()
    response: RAGResponse | None = None
    retrieval: RetrievalResult | None = None
    doc_metrics: RankingMetrics | None = None
    chunk_metrics: RankingMetrics | None = None
    failures: tuple[FailureClassification, ...] = ()
    error: ErrorDetails | None = None
    duration_s: float = 0.0

    @property
    def critical_failure(self) -> bool:
        return self.case.severity is Severity.CRITICAL and self.outcome is CaseOutcome.FAIL

    def ragas_inputs(self) -> dict[str, Any]:
        """Fields Ragas needs. retrieved_contexts = the context actually sent to the model."""
        return {
            "user_input": self.case.question,
            "response": self.response.response if self.response else None,
            "retrieved_contexts": [i.text for i in self.response.context] if self.response else [],
            "reference": self.case.expected_output,
            "reference_contexts": list(self.case.reference_context),
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.case.id, "category": self.case.category.value,
            "severity": self.case.severity.value, "question": self.case.question,
            "expected_behavior": self.case.expected_behavior.value,
            "outcome": self.outcome.value, "critical_failure": self.critical_failure,
            "checks": [c.to_dict() for c in self.checks],
            "failures": [f.to_dict() for f in self.failures],
            "document_metrics": self.doc_metrics.to_dict() if self.doc_metrics else None,
            "chunk_metrics": self.chunk_metrics.to_dict() if self.chunk_metrics else None,
            "response": self.response.to_dict() if self.response else None,
            "retrieval": self.retrieval.to_dict() if self.retrieval else None,
            "ragas_inputs": self.ragas_inputs(),
            "error": self.error.to_dict() if self.error else None,
            "duration_s": self.duration_s,
        }


def retrieval_metrics_for(case: RAGCase, retrieval: RetrievalResult, chunks: Sequence[Chunk],
                          k: int) -> tuple[RankingMetrics, RankingMetrics]:
    docs = compute_metrics(retrieval.doc_ids, case.relevant_document_ids, k, "document")
    chunk_level = compute_metrics(retrieval.chunk_ids, relevant_chunk_ids(case, chunks), k, "chunk")
    return docs, chunk_level


def evaluate_retrieval_case(case: RAGCase, retriever: Retriever, chunks: Sequence[Chunk],
                            *, worst_first: bool = False) -> RAGCaseResult:
    start = time.perf_counter()
    try:
        retrieval = retriever.retrieve(case.question, worst_first=worst_first)
    except Exception as exc:  # noqa: BLE001 - reported as ERROR, never PASS
        return RAGCaseResult(case, CaseOutcome.ERROR, duration_s=_elapsed(start),
                             error=ErrorDetails("retrieval", type(exc).__name__, str(exc)))
    docs, chunk_level = retrieval_metrics_for(case, retrieval, chunks, retriever.top_k)
    checks: list[AssertionResult] = []
    if docs.applicable:
        hit = docs.hit_rate == 1.0
        checks.append(AssertionResult("retrieval_hit", list(case.relevant_document_ids),
                                      list(docs.ranked), hit,
                                      "" if hit else "No relevant document in the top K."))
    restricted = [c.doc_id for c in retrieval.chunks
                  if c.metadata.get("classification") == "restricted"]
    checks.append(AssertionResult("restricted_not_retrieved", [], restricted, not restricted,
                                  "" if not restricted else f"Restricted retrieved: {restricted}"))
    failures = ()
    if not all(c.passed for c in checks):
        failures = tuple(
            FailureClassification(FailureType.RETRIEVAL if c.name == "retrieval_hit"
                                  else FailureType.GUARDRAIL, c.explanation)
            for c in checks if not c.passed)
    outcome = CaseOutcome.PASS if all(c.passed for c in checks) else CaseOutcome.FAIL
    return RAGCaseResult(case, outcome, tuple(checks), retrieval=retrieval, doc_metrics=docs,
                         chunk_metrics=chunk_level, failures=failures, duration_s=_elapsed(start))


def evaluate_rag_case(case: RAGCase, bot: RAGChatbot, retriever: Retriever,
                      chunks: Sequence[Chunk], *, worst_first: bool = False) -> RAGCaseResult:
    start = time.perf_counter()
    stage = "execution"
    try:
        response = bot.respond(case.question)
        stage = "retrieval"
        # Retrieval quality is measured on the system's actual retrieval; when the chatbot
        # refused before retrieving, measure the retriever directly so metrics stay comparable.
        retrieval = response.retrieval or retriever.retrieve(case.question,
                                                             worst_first=worst_first)
        stage = "evaluation"
        docs, chunk_level = retrieval_metrics_for(case, retrieval, chunks, retriever.top_k)
        checks = answer_checks(case, response, docs)
        failures = classify_failures(case, response, checks, docs, chunk_level)
    except Exception as exc:  # noqa: BLE001 - reported as ERROR, never PASS
        return RAGCaseResult(
            case, CaseOutcome.ERROR, duration_s=_elapsed(start),
            failures=(FailureClassification(FailureType.EVALUATION,
                                            f"{stage} error: {type(exc).__name__}: {exc}"),),
            error=ErrorDetails(stage, type(exc).__name__, str(exc)),
        )
    outcome = CaseOutcome.PASS if all(c.passed for c in checks) else CaseOutcome.FAIL
    return RAGCaseResult(case, outcome, checks, response, retrieval, docs, chunk_level,
                         failures, duration_s=_elapsed(start))


@dataclass(frozen=True)
class RAGSummary:
    mode: str
    configuration: dict[str, Any]
    results: tuple[RAGCaseResult, ...]

    def count(self, outcome: CaseOutcome) -> int:
        return sum(1 for r in self.results if r.outcome is outcome)

    @property
    def critical_failures(self) -> tuple[str, ...]:
        return tuple(r.case.id for r in self.results if r.critical_failure)

    def retrieval_summary(self) -> dict[str, dict]:
        return {
            "document": aggregate([r.doc_metrics for r in self.results if r.doc_metrics]),
            "chunk": aggregate([r.chunk_metrics for r in self.results if r.chunk_metrics]),
        }

    def failure_counts(self) -> dict[str, int]:
        counts = {t.value: 0 for t in FailureType}
        for result in self.results:
            for kind in {f.type for f in result.failures}:
                counts[kind.value] += 1
        return counts

    def release(self) -> tuple[ReleaseReadiness, tuple[str, ...], dict[str, Any]]:
        errored = tuple(r.case.id for r in self.results if r.outcome is CaseOutcome.ERROR)
        readiness, reasons = decide_release_readiness(len(self.results), self.critical_failures,
                                                      errored)
        docs = self.retrieval_summary()["document"]
        gates: dict[str, Any] = {}
        extra: list[str] = []
        for metric, minimum in RETRIEVAL_GATES.items():
            value = docs.get(metric)
            passed = value is not None and value >= minimum
            gates[f"document_{metric}"] = {"value": value, "minimum": minimum, "passed": passed}
            if not passed:
                extra.append(f"Retrieval gate failed: document {metric}@K = {value} "
                             f"(minimum {minimum}).")
        restricted = [r.case.id for r in self.results for c in r.checks
                      if c.name == "restricted_not_retrieved" and not c.passed]
        gates["no_restricted_retrieval"] = {"passed": not restricted, "cases": restricted}
        if restricted:
            extra.append(f"Restricted document retrieved in: {', '.join(restricted)}.")
        if extra:
            base = [r for r in reasons if readiness is ReleaseReadiness.BLOCKED]
            return ReleaseReadiness.BLOCKED, tuple(base + extra), gates
        return readiness, reasons, gates

    def to_dict(self) -> dict[str, Any]:
        readiness, reasons, gates = self.release()
        total = len(self.results)
        return {
            "mode": self.mode, "configuration": self.configuration, "total": total,
            "passed": self.count(CaseOutcome.PASS), "failed": self.count(CaseOutcome.FAIL),
            "errored": self.count(CaseOutcome.ERROR),
            "pass_rate": round(self.count(CaseOutcome.PASS) / total, 4) if total else None,
            "critical_failures": list(self.critical_failures),
            "retrieval_metrics": self.retrieval_summary(),
            "failure_classifications": self.failure_counts(),
            "release_gates": gates, "release_readiness": readiness.value,
            "readiness_reasons": list(reasons),
            "ragas": {"status": "not run in this process",
                      "note": "Ragas is advisory; run python -m ai_quality_lab.rag.ragas_eval "
                              "in .venv-ragas on this file."},
            "results": [r.to_dict() for r in self.results],
        }


def run_suite(cases: Sequence[RAGCase], evaluate_case: Callable[[RAGCase], RAGCaseResult],
              mode: str, configuration: dict[str, Any],
              progress: Callable[[int, int, RAGCaseResult], None] | None = None) -> RAGSummary:
    results = []
    for index, case in enumerate(cases, start=1):
        results.append(evaluate_case(case))
        if progress:
            progress(index, len(cases), results[-1])
    return RAGSummary(mode, configuration, tuple(results))


def format_summary(summary: RAGSummary) -> str:
    readiness, reasons, gates = summary.release()
    retrieval = summary.retrieval_summary()
    lines = ["", "Summary", "-------"]
    lines.append(" ".join(f"{k}={v}" for k, v in summary.configuration.items()))
    total = len(summary.results)
    rate = f"{summary.count(CaseOutcome.PASS) / total:.2%}" if total else "n/a"
    lines.append(f"Total: {total}  Passed: {summary.count(CaseOutcome.PASS)}  "
                 f"Failed: {summary.count(CaseOutcome.FAIL)}  "
                 f"Errored: {summary.count(CaseOutcome.ERROR)}  Pass rate: {rate}")
    lines.append(f"Retrieval metrics @K={summary.configuration.get('top_k')} "
                 "(answerable questions only; N/A excluded):")
    for level, values in retrieval.items():
        lines.append(f"  {level:<8} applicable={values['applicable']:<3} " + "  ".join(
            f"{m}={values[m]}" for m in ("hit_rate", "recall", "precision", "mrr", "ndcg")))
    lines.append("Failure classifications (cases): " + "  ".join(
        f"{k}={v}" for k, v in summary.failure_counts().items() if v) or "none")
    lines.append("Release gates: " + "  ".join(
        f"{name}={'PASS' if g['passed'] else 'FAIL'}" for name, g in gates.items()))
    lines.append("Ragas: advisory only, not part of this decision "
                 "(run ai_quality_lab.rag.ragas_eval in .venv-ragas).")
    lines.append(f"Critical failures: {', '.join(summary.critical_failures) or 'none'}")
    lines.append(f"Release readiness: {readiness.value}")
    lines += [f"  - {r}" for r in reasons]
    return "\n".join(lines)


def _progress(index: int, total: int, result: RAGCaseResult) -> None:
    case = result.case
    docs = result.doc_metrics
    retrieved = ",".join(docs.ranked) if docs else "-"
    print(f"[{index}/{total}] {result.outcome.value:<5} {case.id} {case.severity.value:<8} "
          f"{case.category.value:<24} docs@K={retrieved} ({result.duration_s:.1f}s)", flush=True)
    for check in result.checks:
        if not check.passed:
            print(f"        check {check.name}: {check.explanation}", flush=True)
    for failure in result.failures:
        print(f"        -> {failure.type.value}: {failure.evidence}", flush=True)
    if result.error:
        print(f"        {result.error.stage} error: {result.error.type}: {result.error.message}",
              flush=True)


def _make_client(name: str):
    if name == "mock-extractive":
        return ExtractiveMockLLM()
    if name == "mock-gullible":
        return GullibleMockLLM()
    settings = LLMSettings.from_env()
    check_model_available(settings)
    return OpenAICompatibleClient(settings)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_quality_lab.rag.evaluate",
                                     description="Evaluate retrieval and the RAG chatbot.")
    parser.add_argument("--mode", choices=["retrieval", "rag"], default="retrieval")
    parser.add_argument("--llm", choices=["ollama", "mock-extractive", "mock-gullible"],
                        default="ollama", help="model for --mode rag (ollama uses AIQL_LLM_*)")
    parser.add_argument("--embedder", choices=["env", "hashing"], default="env",
                        help="env = AIQL_EMBED_* (Ollama by default); hashing = offline lexical")
    parser.add_argument("--top-k", type=int, default=DEFAULT_TOP_K)
    parser.add_argument("--dataset", type=Path, default=DEFAULT_RAG_DATASET)
    parser.add_argument("--docs", type=Path, default=DEFAULT_KNOWLEDGE_BASE)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--tag", action="append", default=[])
    parser.add_argument("--simulate-failure", default=RAGSimulatedFailure.NONE.value,
                        choices=[m.value for m in RAGSimulatedFailure],
                        help="TEST-ONLY defect to prove the evaluation detects it")
    parser.add_argument("--rebuild-index", action="store_true")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args(argv)

    load_env_file()
    failure = RAGSimulatedFailure(args.simulate_failure)
    try:
        documents = load_documents(args.docs)
        cases = load_rag_dataset(args.dataset, documents)
        unknown = sorted(set(args.case) - {c.id for c in cases})
        if unknown:
            raise DatasetValidationError([f"Unknown case id(s): {unknown}"])
        cases = [c for c in cases if (not args.case or c.id in args.case)
                 and (not args.tag or set(args.tag) & set(c.tags))]
        embedder = HashingEmbedder() if args.embedder == "hashing" else embedder_from_env()
        chunking = ChunkingConfig()
        store = load_or_build_index(documents, embedder, chunking, rebuild=args.rebuild_index)
        retriever = Retriever(store, embedder, top_k=args.top_k)
        client = _make_client(args.llm) if args.mode == "rag" else None
    except (DatasetValidationError, DocumentError, VectorIndexError, LLMError, ValueError,
            OSError) as exc:
        print(f"Setup error: {exc}", file=sys.stderr)
        return 2

    chunks = store.chunks
    worst_first = failure is RAGSimulatedFailure.RETRIEVAL_DEGRADED
    configuration = {"mode": args.mode, "embedder": store.embedding_model, "top_k": args.top_k,
                     "chunk_size": chunking.chunk_size, "overlap": chunking.overlap,
                     "llm": client.label if client else "none",
                     "simulated_failure": failure.value}
    print(f"Evaluating {len(cases)} case(s): {configuration}", flush=True)
    if failure is not RAGSimulatedFailure.NONE:
        print(f"WARNING: simulated failure '{failure.value}' is ENABLED (test-only).", flush=True)
    def evaluate(case: RAGCase) -> RAGCaseResult:
        if args.mode == "retrieval":
            return evaluate_retrieval_case(case, retriever, chunks, worst_first=worst_first)
        bot = RAGChatbot(retriever, client, case.configuration, simulated_failure=failure)
        return evaluate_rag_case(case, bot, retriever, chunks, worst_first=worst_first)

    summary = run_suite(cases, evaluate, args.mode, configuration, _progress)
    print(format_summary(summary))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary.to_dict(), indent=2, ensure_ascii=False),
                           encoding="utf-8")
    print(f"Results written to {args.output}")
    return 0 if summary.release()[0] is ReleaseReadiness.READY else 1


def _elapsed(start: float) -> float:
    return round(time.perf_counter() - start, 3)


if __name__ == "__main__":
    raise SystemExit(main())
