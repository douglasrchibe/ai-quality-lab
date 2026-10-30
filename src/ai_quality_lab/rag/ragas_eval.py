"""ADVISORY Ragas evaluation of RAG results. Run ONLY with the separate Ragas environment:

    .\\.venv-ragas\\Scripts\\python.exe -m ai_quality_lab.rag.ragas_eval \\
        --results reports/rag/latest.json

It reads the ``ragas_inputs`` written by ``ai_quality_lab.rag.evaluate --mode rag`` (question,
generated response, the context actually sent to the model, reference answer), so Ragas never
needs to share an environment with DeepEval (their dependencies conflict).

Policy (Phase 5.5 finding: the qwen2.5:3b judge is unreliable on statement-level metrics):
* every score here is ADVISORY and never changes release readiness;
* a judge calibration runs first on known-good / known-bad examples; the judge is reported
  TRUSTED only if it classifies every calibration example correctly;
* a metric that cannot be computed (judge error, timeout, NaN) is reported as ERROR, never PASS;
* the judge comes from ``AIQL_JUDGE_*`` (default: the chatbot model) and is never swapped silently.

Metrics (Ragas 0.4.3 collections API):
* faithfulness       (user_input, response, retrieved_contexts): claims supported by context
* answer_relevancy   (user_input, response; needs embeddings): answer addresses the question
* context_precision  (user_input, reference, retrieved_contexts): relevant context ranked first
* context_recall     (user_input, retrieved_contexts, reference): reference covered by context
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from ai_quality_lab.llm_client import LLMError, LLMSettings, check_model_available, load_env_file
from ai_quality_lab.rag.embeddings import DEFAULT_EMBED_MODEL

ADVISORY_THRESHOLDS: Mapping[str, float] = {
    "faithfulness": 0.7,
    "answer_relevancy": 0.7,
    "context_precision": 0.5,
    "context_recall": 0.7,
}
METRIC_INPUTS: Mapping[str, tuple[str, ...]] = {
    "faithfulness": ("user_input", "response", "retrieved_contexts"),
    "answer_relevancy": ("user_input", "response"),
    "context_precision": ("user_input", "reference", "retrieved_contexts"),
    "context_recall": ("user_input", "retrieved_contexts", "reference"),
}
POLICY = ("ADVISORY ONLY: Ragas scores are LLM-judged and never affect release readiness. "
          "Deterministic retrieval and safety checks are the release gates.")
DEFAULT_OUTPUT = Path("reports") / "rag" / "ragas_advisory.json"

SUP = "P1 tickets receive a first response within 1 hour, 24 hours a day."
FAC = "The parking garage has 12 electric vehicle charging stations."
VPN = "VPN sessions disconnect automatically after 12 hours."
OPS = ("Every production change requires two approvals: one from a peer engineer and one from the "
       "service owner.")
PWD = "Passwords must be at least 14 characters long."
CALIBRATION: tuple[dict[str, Any], ...] = (
    {"label": "faithful-answer", "metric": "faithfulness", "should_pass": True,
     "inputs": {"user_input": "What is the P1 first-response time?",
                "response": "P1 tickets get a first response within 1 hour.",
                "retrieved_contexts": [SUP]}},
    {"label": "unsupported-answer", "metric": "faithfulness", "should_pass": False,
     "inputs": {"user_input": "What is the P1 first-response time?",
                "response": "P1 tickets get a first response within 10 minutes, and customers "
                            "receive a free month of service.",
                "retrieved_contexts": [SUP]}},
    {"label": "hallucinated-statement", "metric": "faithfulness", "should_pass": False,
     "inputs": {"user_input": "What is the minimum password length?",
                "response": "Passwords must be 14 characters long and are emailed to managers "
                            "every Friday for review.",
                "retrieved_contexts": [PWD]}},
    {"label": "relevant-answer", "metric": "answer_relevancy", "should_pass": True,
     "inputs": {"user_input": "What is the minimum password length?",
                "response": "The minimum password length is 14 characters."}},
    {"label": "irrelevant-answer", "metric": "answer_relevancy", "should_pass": False,
     "inputs": {"user_input": "What is the minimum password length?",
                "response": "The parking garage has 12 electric vehicle charging stations."}},
    {"label": "precise-context", "metric": "context_precision", "should_pass": True,
     "inputs": {"user_input": "What is the P1 first-response time?",
                "reference": "Within 1 hour.", "retrieved_contexts": [SUP, FAC]}},
    {"label": "imprecise-context", "metric": "context_precision", "should_pass": False,
     "inputs": {"user_input": "What is the P1 first-response time?",
                "reference": "Within 1 hour.", "retrieved_contexts": [FAC, VPN]}},
    {"label": "complete-context", "metric": "context_recall", "should_pass": True,
     "inputs": {"user_input": "How many approvals does a deployment need?",
                "reference": "Two approvals: a peer engineer and the service owner.",
                "retrieved_contexts": [OPS]}},
    {"label": "missing-context", "metric": "context_recall", "should_pass": False,
     "inputs": {"user_input": "How many approvals does a deployment need?",
                "reference": "Two approvals: a peer engineer and the service owner.",
                "retrieved_contexts": [FAC]}},
)


# --- Ragas wiring (imports Ragas lazily; requires .venv-ragas) ----------------------------------


def prepare_ragas_environment() -> None:
    os.environ.setdefault("RAGAS_DO_NOT_TRACK", "true")


def _openai_compatible(settings: LLMSettings) -> tuple[str, str]:
    if settings.provider == "ollama":
        return settings.base_url.rstrip("/") + "/v1", "ollama"
    return settings.base_url, settings.api_key or ""


def build_ragas_metrics(judge: LLMSettings, embed_model: str, embed_base_url: str) -> dict:
    prepare_ragas_environment()
    from openai import AsyncOpenAI
    from ragas.embeddings import OpenAIEmbeddings
    from ragas.llms import llm_factory
    from ragas.metrics.collections import (
        AnswerRelevancy,
        ContextPrecision,
        ContextRecall,
        Faithfulness,
    )

    base_url, api_key = _openai_compatible(judge)
    client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=judge.timeout_seconds)
    llm = llm_factory(judge.model, provider="openai", client=client,
                      max_tokens=judge.max_tokens)
    embed_client = AsyncOpenAI(base_url=embed_base_url.rstrip("/") + "/v1", api_key="ollama",
                               timeout=judge.timeout_seconds)
    embeddings = OpenAIEmbeddings(client=embed_client, model=embed_model)
    return {
        "faithfulness": Faithfulness(llm=llm),
        "answer_relevancy": AnswerRelevancy(llm=llm, embeddings=embeddings),
        "context_precision": ContextPrecision(llm=llm),
        "context_recall": ContextRecall(llm=llm),
    }


# --- Scoring (pure; testable with fake metric objects) ------------------------------------------


async def score_metric(metric: Any, inputs: Mapping[str, Any], timeout: float) -> dict[str, Any]:
    """{"value": float} or {"error": str}. NaN/None/exceptions are errors, never a pass."""
    try:
        result = await asyncio.wait_for(metric.ascore(**inputs), timeout=timeout)
        value = getattr(result, "value", result)
        value = float(value)
        if math.isnan(value):
            return {"value": None, "error": "metric returned NaN (could not be computed)"}
        return {"value": round(value, 4), "error": None}
    except Exception as exc:  # noqa: BLE001 - reported as ERROR
        return {"value": None, "error": f"{type(exc).__name__}: {str(exc)[:200]}"}


def verdict(metric_name: str, scored: Mapping[str, Any]) -> str:
    if scored.get("error"):
        return "ERROR"
    return "PASS" if scored["value"] >= ADVISORY_THRESHOLDS[metric_name] else "FAIL"


def applicable_metrics(result: Mapping[str, Any]) -> tuple[list[str], str | None]:
    """Which metrics make sense for one RAG result, or why none do."""
    inputs = result.get("ragas_inputs") or {}
    if result.get("expected_behavior") != "answer":
        return [], "not an answer case (refusal/not-found/safe cases are checked deterministically)"
    if not inputs.get("response"):
        return [], "no response (case errored)"
    if (result.get("response") or {}).get("blocked"):
        return [], "response was blocked by a guardrail"
    if not inputs.get("retrieved_contexts") or not any(inputs["retrieved_contexts"]):
        return ["answer_relevancy"], "no retrieved context"
    return list(METRIC_INPUTS), None


async def run_calibration(metrics: Mapping[str, Any], timeout: float) -> dict[str, Any]:
    rows = []
    for example in CALIBRATION:
        scored = await score_metric(metrics[example["metric"]], example["inputs"], timeout)
        outcome = verdict(example["metric"], scored)
        correct = outcome != "ERROR" and (outcome == "PASS") == example["should_pass"]
        rows.append({"label": example["label"], "metric": example["metric"],
                     "expected": "PASS" if example["should_pass"] else "FAIL",
                     "actual": outcome, "score": scored["value"], "error": scored["error"],
                     "correct": correct})
    per_metric = {}
    for name in METRIC_INPUTS:
        mine = [r for r in rows if r["metric"] == name]
        per_metric[name] = ("TRUSTED" if mine and all(r["correct"] for r in mine)
                            else "UNTRUSTED")
    status = "TRUSTED" if all(r["correct"] for r in rows) else "UNTRUSTED"
    return {"status": status, "correct": sum(r["correct"] for r in rows), "total": len(rows),
            "per_metric": per_metric, "examples": rows}


async def score_results(results: Sequence[Mapping[str, Any]], metrics: Mapping[str, Any],
                        timeout: float) -> list[dict[str, Any]]:
    rows = []
    for result in results:
        names, note = applicable_metrics(result)
        inputs = result.get("ragas_inputs") or {}
        scores = {}
        for name in names:
            scored = await score_metric(
                metrics[name], {k: inputs[k] for k in METRIC_INPUTS[name]}, timeout)
            scores[name] = {**scored, "verdict": verdict(name, scored),
                            "threshold": ADVISORY_THRESHOLDS[name]}
        rows.append({"id": result.get("id"), "deterministic_outcome": result.get("outcome"),
                     "note": note, "scores": scores})
        print(f"  {result.get('id')}: " + (", ".join(
            f"{n}={s['value'] if s['value'] is not None else 'ERROR'}" for n, s in scores.items())
            or f"skipped ({note})"), flush=True)
    return rows


def summarize(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    summary = {}
    for name in METRIC_INPUTS:
        scored = [r["scores"][name] for r in rows if name in r["scores"]]
        values = [s["value"] for s in scored if s["value"] is not None]
        summary[name] = {
            "evaluated": len(scored), "errors": sum(1 for s in scored if s["error"]),
            "below_threshold": sum(1 for s in scored if s["verdict"] == "FAIL"),
            "mean": round(sum(values) / len(values), 4) if values else None,
        }
    return summary


# --- CLI ----------------------------------------------------------------------------------------


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_quality_lab.rag.ragas_eval",
                                     description="Advisory Ragas scores for RAG results.")
    parser.add_argument("--results", type=Path, default=Path("reports") / "rag" / "latest.json")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--case", action="append", default=[])
    parser.add_argument("--calibration-only", action="store_true")
    parser.add_argument("--skip-calibration", action="store_true")
    args = parser.parse_args(argv)

    load_env_file()
    try:
        data = json.loads(args.results.read_text(encoding="utf-8")) if not args.calibration_only \
            else {"results": []}
        if data.get("mode") not in (None, "rag"):
            raise ValueError("Ragas needs a --mode rag results file (generated answers).")
        judge = LLMSettings.judge_from_env()
        check_model_available(judge)
        embed_model = os.environ.get("AIQL_EMBED_MODEL", "").strip() or DEFAULT_EMBED_MODEL
        embed_base = os.environ.get("AIQL_EMBED_BASE_URL", "").strip() or judge.base_url
        metrics = build_ragas_metrics(judge, embed_model, embed_base)
    except (OSError, ValueError, json.JSONDecodeError, LLMError) as exc:
        print(f"Setup error: {exc}", file=sys.stderr)
        return 2
    except ImportError as exc:
        print(f"Ragas is not installed in this environment ({exc}). Use .venv-ragas "
              "(see requirements-ragas.txt).", file=sys.stderr)
        return 2

    results = [r for r in data.get("results", []) if not args.case or r.get("id") in args.case]
    print(POLICY)
    print(f"Judge: {judge.label}   Embeddings: ollama:{embed_model}", flush=True)
    calibration = None
    if not args.skip_calibration:
        print(f"Calibrating judge on {len(CALIBRATION)} known examples...", flush=True)
        calibration = asyncio.run(run_calibration(metrics, judge.timeout_seconds))
        for row in calibration["examples"]:
            mark = "ok " if row["correct"] else "WRONG"
            detail = f", {row['error']}" if row["error"] else ""
            print(f"  [{mark}] {row['metric']:<18} {row['label']:<24} expected {row['expected']}"
                  f" got {row['actual']} (score {row['score']}{detail})", flush=True)
        print(f"Judge calibration: {calibration['status']} "
              f"({calibration['correct']}/{calibration['total']} correct)", flush=True)
    rows = asyncio.run(score_results(results, metrics, judge.timeout_seconds)) if results else []
    output = {
        "policy": POLICY, "judge": judge.label, "embeddings": f"ollama:{embed_model}",
        "source_results": str(args.results), "calibration": calibration,
        "summary": summarize(rows), "cases": rows,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(output, indent=2), encoding="utf-8")
    if rows:
        print("Advisory summary: " + json.dumps(output["summary"]))
    print(f"Written to {args.output}. These scores do not affect release readiness.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
