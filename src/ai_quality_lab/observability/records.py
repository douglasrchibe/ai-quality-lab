"""Evaluation records: build them from existing reports, fingerprint datasets, validate.

A record is a plain JSON object (``schema_version`` 1)::

    run_id, recorded_at (UTC, ...Z), evaluation_type, deterministic
    source:      commit, branch, origin (local | github-actions), ci_run_id, report_file, note
    dataset:     path, fingerprint, case_count, corpus_fingerprint (RAG only)
    identity:    what must match for two runs to be comparable (type, fingerprints, model, embedder)
    comparability_key: hash of ``identity``
    parameters:  system settings that may legitimately differ (top_k, chunking, simulated_failure)
    thresholds:  release thresholds in force when the run was evaluated
    status:      PASS | BLOCKED | ERROR (the run's own release decision)
    metrics:     name -> {value, numerator, denominator, kind}
    unmeasured:  name -> reason (missing data is listed here, never stored as 0)
    cases:       [{id, outcome, severity}]

Metrics are extracted from the reports the existing tools already write; nothing is re-scored.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import subprocess
from collections.abc import Callable, Mapping, Sequence
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1


class EvaluationType(str, Enum):
    CUSTOM_ENGINE = "custom_engine"
    PROMPTFOO = "promptfoo"
    RAG_RETRIEVAL = "rag_retrieval"
    RAG_MOCK = "rag_mock"
    RAG_LIVE = "rag_live"
    PYTEST = "pytest"
    RAGAS_ADVISORY = "ragas_advisory"


class MetricKind(str, Enum):
    DETERMINISTIC = "deterministic"
    ADVISORY = "advisory"  # LLM-judged: reported, never release-blocking


STATUSES = ("PASS", "BLOCKED", "ERROR")
SEVERITIES = ("critical", "high", "medium", "low")
CASE_OUTCOMES = ("PASS", "FAIL", "ERROR")
TIMESTAMP_FORMAT = "%Y-%m-%dT%H:%M:%SZ"
TIMESTAMP_PATTERN = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$")


class RecordError(ValueError):
    """A report cannot be turned into a trustworthy record, or a record is invalid."""


# --- Small helpers ------------------------------------------------------------------------------


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime(TIMESTAMP_FORMAT)


def canonical_json(value: Any) -> str:
    """Key-sorted compact JSON: identical on every platform, whatever the file's line endings."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def short_hash(value: Any) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()[:16]


def dataset_fingerprint(path: str | Path) -> str:
    """Hash of the parsed JSON, so CRLF (Windows checkout) and LF (Linux) files agree."""
    try:
        return short_hash(json.loads(Path(path).read_text(encoding="utf-8-sig")))
    except (OSError, ValueError) as exc:
        raise RecordError(f"cannot fingerprint dataset {path}: {exc}") from None


def corpus_fingerprint(directory: str | Path) -> str:
    from ai_quality_lab.rag.documents import DocumentError, documents_fingerprint, load_documents

    try:
        return documents_fingerprint(load_documents(directory))
    except DocumentError as exc:
        raise RecordError(f"cannot fingerprint knowledge base {directory}: {exc}") from None


def metric(value: float | int, kind: MetricKind = MetricKind.DETERMINISTIC,
           numerator: int | None = None, denominator: int | None = None) -> dict[str, Any]:
    return {"value": value, "numerator": numerator, "denominator": denominator,
            "kind": kind.value}


def rate(numerator: int, denominator: int, name: str, metrics: dict, unmeasured: dict,
         kind: MetricKind = MetricKind.DETERMINISTIC) -> None:
    """Store numerator/denominator, or record why the rate is undefined (never as 0)."""
    if denominator == 0:
        unmeasured[name] = "denominator is 0: no applicable cases in this run"
        return
    metrics[name] = metric(round(numerator / denominator, 4), kind, numerator, denominator)


def git_source(cwd: str | Path = ".") -> dict[str, str | None]:
    """Commit and branch: GitHub Actions variables first, then ``git``, else None (unknown)."""
    env = os.environ
    if env.get("GITHUB_ACTIONS") == "true":
        return {"commit": env.get("GITHUB_SHA"),
                "branch": env.get("GITHUB_HEAD_REF") or env.get("GITHUB_REF_NAME"),
                "origin": "github-actions",
                "ci_run_id": f"{env.get('GITHUB_RUN_ID')}-{env.get('GITHUB_RUN_ATTEMPT', '1')}"}

    def git(*args: str) -> str | None:
        try:
            done = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True,
                                  timeout=10)
        except (OSError, subprocess.SubprocessError):
            return None
        return done.stdout.strip() or None if done.returncode == 0 else None

    status = git("status", "--porcelain")
    commit = git("rev-parse", "HEAD")
    return {"commit": commit, "branch": git("rev-parse", "--abbrev-ref", "HEAD"),
            "origin": "local", "ci_run_id": None,
            # True: the run included uncommitted changes, so `commit` alone does not reproduce it.
            "uncommitted_changes": None if commit is None else bool(status)}


def _load_report(path: str | Path) -> dict[str, Any]:
    try:
        # utf-8-sig: PowerShell 5.1 writes a UTF-8 byte-order mark. Invalid bytes (e.g. UTF-16)
        # still raise UnicodeDecodeError (a ValueError) and the record is refused.
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise RecordError(f"report {path} is missing or unreadable: {exc}") from None
    if not isinstance(data, dict):
        raise RecordError(f"report {path} is not a JSON object")
    return data


def _require(data: Mapping[str, Any], keys: Sequence[str], what: str) -> None:
    missing = [k for k in keys if k not in data]
    if missing:
        raise RecordError(f"{what} is missing required field(s): {missing}")


def _severity_failures(cases: Sequence[Mapping[str, Any]], metrics: dict) -> None:
    """``<severity>_failures`` = cases of that severity whose outcome is FAIL (as in the reports;
    errored cases are counted separately in ``error_count``)."""
    for severity in ("critical", "high", "medium"):
        scoped = [c for c in cases if c["severity"] == severity]
        failed = sum(1 for c in scoped if c["outcome"] == "FAIL")
        metrics[f"{severity}_failures"] = metric(failed, numerator=failed,
                                                 denominator=len(scoped))


def _outcome_metrics(cases: Sequence[Mapping[str, Any]], metrics: dict, unmeasured: dict) -> None:
    total = len(cases)
    passed = sum(1 for c in cases if c["outcome"] == "PASS")
    errored = sum(1 for c in cases if c["outcome"] == "ERROR")
    metrics["case_count"] = metric(total)
    rate(passed, total, "pass_rate", metrics, unmeasured)
    metrics["error_count"] = metric(errored, numerator=errored, denominator=total)
    _severity_failures(cases, metrics)


def _status_from_readiness(readiness: Any) -> str:
    mapping = {"READY": "PASS", "BLOCKED": "BLOCKED"}
    if readiness not in mapping:
        raise RecordError(f"unknown release_readiness {readiness!r}")
    return mapping[readiness]


def blocking_reasons(metrics: Mapping[str, Any],
                     retrieval_gates: Mapping[str, float] | None = None) -> list[str]:
    """Why the existing release policy blocks this run, recomputed from the record's metrics.

    Custom engine / Promptfoo: zero cases, any critical FAIL, any ERROR (decide_release_readiness).
    RAG adds the retrieval gates (document hit rate / recall minimums) and restricted retrieval.
    """
    def value(name: str) -> Any:
        return metrics[name]["value"] if name in metrics else None

    reasons = []
    if "case_count" in metrics and not value("case_count"):
        reasons.append("no cases were executed")
    if value("critical_failures"):
        reasons.append(f"{value('critical_failures')} critical failure(s)")
    if value("error_count"):
        reasons.append(f"{value('error_count')} errored case(s)")
    if retrieval_gates is not None:
        for gate, minimum in retrieval_gates.items():
            v = value(f"doc_{gate}")
            if v is None or v < minimum:
                reasons.append(f"retrieval gate failed: document {gate} {v} < {minimum}")
        if value("restricted_retrievals"):
            reasons.append(f"{value('restricted_retrievals')} restricted retrieval(s)")
    return reasons


def _consistent_status(claimed: str, metrics: Mapping[str, Any],
                       retrieval_gates: Mapping[str, float] | None = None) -> str:
    """The report's claimed status, only if its own results support it (D-8.1).

    An inconsistent report (READY with critical failures or errors, or BLOCKED with nothing
    blocking) means the producing tool or the file cannot be trusted, so it is refused rather
    than recorded: a refused record makes the comparison ERROR, never PASS.
    """
    reasons = blocking_reasons(metrics, retrieval_gates)
    if claimed == "PASS" and reasons:
        raise RecordError("inconsistent report: it claims READY but its results contain "
                          + "; ".join(reasons))
    if claimed == "BLOCKED" and not reasons:
        raise RecordError("inconsistent report: it claims BLOCKED but its results contain no "
                          "critical failure, error, failed gate, or restricted retrieval")
    return claimed


def _case(case_id: Any, outcome: Any, severity: Any) -> dict[str, str]:
    if outcome not in CASE_OUTCOMES or severity not in SEVERITIES or not isinstance(case_id, str):
        raise RecordError(f"invalid case result: id={case_id!r} outcome={outcome!r} "
                          f"severity={severity!r}")
    return {"id": case_id, "outcome": outcome, "severity": severity}


def _check_case_ids(cases: Sequence[Mapping[str, Any]], expected_ids: Sequence[str],
                    dataset: Path) -> None:
    """The report must cover exactly the dataset being fingerprinted (no stale reports)."""
    got, want = sorted(c["id"] for c in cases), sorted(expected_ids)
    if got != want:
        missing, extra = sorted(set(want) - set(got)), sorted(set(got) - set(want))
        raise RecordError(f"report cases do not match dataset {dataset}: missing {missing[:5]}, "
                          f"unexpected {extra[:5]}; was the report produced from another dataset?")


# --- Builders: existing report -> record parts --------------------------------------------------


def _guardrail_parts(cases: list[dict], dataset: Path, simulated_failure: str) -> dict[str, Any]:
    from ai_quality_lab.dataset import DatasetValidationError, load_dataset

    try:
        dataset_cases = load_dataset(dataset)
    except (DatasetValidationError, OSError) as exc:
        raise RecordError(f"cannot load dataset {dataset}: {exc}") from None
    _check_case_ids(cases, [c.id for c in dataset_cases], dataset)
    metrics: dict[str, Any] = {}
    unmeasured: dict[str, str] = {}
    _outcome_metrics(cases, metrics, unmeasured)
    # Safety = the guardrail must block: cases whose expectation is blocked=true.
    must_block = {c.id for c in dataset_cases if c.expected.blocked is True}
    unsafe = sum(1 for c in cases if c["id"] in must_block and c["outcome"] != "PASS")
    metrics["safety_failures"] = metric(unsafe, numerator=unsafe, denominator=len(must_block))
    fingerprint = dataset_fingerprint(dataset)
    return {
        "dataset": {"path": dataset.as_posix(), "fingerprint": fingerprint,
                    "case_count": len(dataset_cases), "corpus_fingerprint": None},
        "identity_extra": {"dataset_fingerprint": fingerprint, "agent": "mock_chatbot"},
        "parameters": {"simulated_failure": simulated_failure},
        "thresholds": {"release_policy": "BLOCKED on any critical FAIL, any ERROR, or zero cases"},
        "metrics": metrics, "unmeasured": unmeasured, "cases": cases, "deterministic": True,
    }


def build_custom_engine(report: Mapping[str, Any], dataset: Path) -> dict[str, Any]:
    _require(report, ("results", "release_readiness", "total", "errored", "simulated_failure"),
             "custom engine report (runner --json)")
    cases = [_case(r.get("id"), r.get("outcome"), r.get("severity")) for r in report["results"]]
    parts = _guardrail_parts(cases, dataset, str(report["simulated_failure"]))
    parts["status"] = _consistent_status(_status_from_readiness(report["release_readiness"]),
                                         parts["metrics"])
    return parts


def build_promptfoo(results_path: Path, dataset: Path) -> dict[str, Any]:
    from ai_quality_lab.dataset import DatasetValidationError, load_dataset
    from ai_quality_lab.engine_comparison import PromptfooResultsError, load_promptfoo_results
    from ai_quality_lab.evaluator import decide_release_readiness

    try:
        run = load_promptfoo_results(results_path)
        severity = {c.id: c.severity.value for c in load_dataset(dataset)}
    except (PromptfooResultsError, DatasetValidationError, OSError) as exc:
        raise RecordError(str(exc)) from None
    cases = [_case(cid, r.outcome.value, severity.get(cid)) for cid, r in run.results.items()]
    parts = _guardrail_parts(cases, dataset, run.simulated_failure.value)
    critical = tuple(c["id"] for c in cases if c["severity"] == "critical"
                     and c["outcome"] == "FAIL")
    errored = tuple(c["id"] for c in cases if c["outcome"] == "ERROR")
    readiness, _ = decide_release_readiness(len(cases), critical, errored)
    parts["status"] = _status_from_readiness(readiness.value)
    parts["identity_extra"]["agent"] = "mock_chatbot via promptfoo"
    return parts


def rag_type(configuration: Mapping[str, Any]) -> EvaluationType:
    """Retrieval-only, mock-model, or live-model run, from the report's own configuration."""
    llm = str(configuration.get("llm", ""))
    if configuration.get("mode") == "retrieval":
        return EvaluationType.RAG_RETRIEVAL
    return EvaluationType.RAG_MOCK if llm.startswith("mock:") else EvaluationType.RAG_LIVE


SAFETY_FAILURE_TYPES = ("prompt_injection", "guardrail_failure")


def build_rag(report: Mapping[str, Any], dataset: Path, knowledge_base: Path) -> dict[str, Any]:
    from ai_quality_lab.rag.dataset import load_rag_dataset
    from ai_quality_lab.rag.failures import is_abstention

    _require(report, ("mode", "configuration", "results", "retrieval_metrics", "release_gates",
                      "release_readiness", "critical_failures"), "RAG report (rag.evaluate)")
    config = report["configuration"]
    _require(config, ("mode", "embedder", "llm", "top_k", "chunk_size", "overlap",
                      "simulated_failure"), "RAG report configuration")
    try:
        dataset_cases = load_rag_dataset(dataset)
    except (ValueError, OSError) as exc:
        raise RecordError(f"cannot load RAG dataset {dataset}: {exc}") from None
    results = report["results"]
    cases = [_case(r.get("id"), r.get("outcome"), r.get("severity")) for r in results]
    _check_case_ids(cases, [c.id for c in dataset_cases], dataset)
    evaluation_type = rag_type(config)

    metrics: dict[str, Any] = {}
    unmeasured: dict[str, str] = {}
    _outcome_metrics(cases, metrics, unmeasured)
    # Prefer the report's own critical list (same definition: critical severity and FAIL).
    critical = report["critical_failures"]
    metrics["critical_failures"]["value"] = len(critical)
    metrics["critical_failures"]["numerator"] = len(critical)

    document = report["retrieval_metrics"].get("document")
    if not isinstance(document, dict) or "applicable" not in document:
        raise RecordError("RAG report has no document-level retrieval metrics")
    for name in ("hit_rate", "recall", "precision", "mrr", "ndcg"):
        value = document.get(name)
        if document["applicable"] == 0 or value is None:
            unmeasured[f"doc_{name}"] = "no applicable questions (no relevant documents)"
        else:
            metrics[f"doc_{name}"] = metric(value, denominator=document["applicable"])
    restricted = report["release_gates"].get("no_restricted_retrieval", {}).get("cases", [])
    metrics["restricted_retrievals"] = metric(len(restricted), numerator=len(restricted),
                                              denominator=len(cases))

    if evaluation_type is EvaluationType.RAG_RETRIEVAL:
        reason = "retrieval-only run: no answers were generated"
        for name in ("safety_failures", "answerable_abstention_rate", "over_abstention_rate"):
            unmeasured[name] = reason
    else:
        unsafe = sum(1 for r in results
                     if any(f.get("type") in SAFETY_FAILURE_TYPES for f in r.get("failures", [])))
        metrics["safety_failures"] = metric(unsafe, numerator=unsafe, denominator=len(cases))
        # Abstention signal in an answer to an answerable question. This includes hedged answers
        # that abstain and then answer anyway (e.g. RAG-011, D-6.3), so it is reported alongside
        # the stricter over-abstention rate (signal AND the case failed: D-6.1).
        answerable = [r for r in results if r.get("expected_behavior") == "answer"]
        abstained = [r for r in answerable if _abstained(r, is_abstention)]
        over = sum(1 for r in abstained if r.get("outcome") == "FAIL")
        rate(len(abstained), len(answerable), "answerable_abstention_rate", metrics, unmeasured)
        rate(over, len(answerable), "over_abstention_rate", metrics, unmeasured)

    from ai_quality_lab.rag.evaluate import RETRIEVAL_GATES

    fingerprint = dataset_fingerprint(dataset)
    corpus = corpus_fingerprint(knowledge_base)
    return {
        "dataset": {"path": dataset.as_posix(), "fingerprint": fingerprint,
                    "case_count": len(dataset_cases), "corpus_fingerprint": corpus},
        "identity_extra": {"dataset_fingerprint": fingerprint, "corpus_fingerprint": corpus,
                           "llm": config["llm"], "embedder": config["embedder"]},
        "parameters": {"top_k": config["top_k"], "chunk_size": config["chunk_size"],
                       "overlap": config["overlap"],
                       "simulated_failure": config["simulated_failure"]},
        "thresholds": {"document_hit_rate_min": RETRIEVAL_GATES["hit_rate"],
                       "document_recall_min": RETRIEVAL_GATES["recall"],
                       "release_policy": "BLOCKED on any critical FAIL, any ERROR, a failed "
                                         "retrieval gate, or restricted retrieval"},
        "metrics": metrics, "unmeasured": unmeasured, "cases": cases,
        "status": _consistent_status(_status_from_readiness(report["release_readiness"]),
                                     metrics, RETRIEVAL_GATES),
        "deterministic": evaluation_type is not EvaluationType.RAG_LIVE,
        "evaluation_type": evaluation_type,
    }


def _abstained(result: Mapping[str, Any], is_abstention: Callable[[str], bool]) -> bool:
    response = result.get("response")
    if not isinstance(response, dict) or response.get("blocked"):
        return False  # a guardrail block is a refusal, not an abstention
    return is_abstention(str(response.get("response", "")))


def build_pytest(audit: Mapping[str, Any]) -> dict[str, Any]:
    _require(audit, ("tests", "passed", "failures", "errors", "skipped", "allowed_skips",
                     "unexpected_skips"), "skip audit (ci_summary skips)")
    counts = {k: audit[k] for k in ("tests", "passed", "failures", "errors", "skipped",
                                     "allowed_skips")}
    if (any(isinstance(v, bool) or not isinstance(v, int) or v < 0 for v in counts.values())
            or not isinstance(audit["unexpected_skips"], list)):
        raise RecordError(f"skip audit counts must be non-negative integers: {counts}")
    tests, passed = counts["tests"], counts["passed"]
    unexpected = len(audit["unexpected_skips"])
    failed = counts["failures"] + counts["errors"]
    # Executed = tests that ran (passed or failed). Skips, expected or not, did not run, so adding
    # an opt-in live test (an expected skip) cannot lower the pass rate (D-8.2).
    executed = tests - counts["skipped"]
    if executed != passed + failed or counts["allowed_skips"] + unexpected != counts["skipped"]:
        raise RecordError(f"skip audit is internally inconsistent: {counts}, "
                          f"{unexpected} unexpected skip(s)")
    metrics = {
        "test_count": metric(tests),
        "executed_tests": metric(executed, numerator=executed, denominator=tests),
        "passed_tests": metric(passed, numerator=passed, denominator=executed),
        "tests_failed": metric(failed, numerator=failed, denominator=executed),
        "expected_skips": metric(counts["allowed_skips"], numerator=counts["allowed_skips"],
                                 denominator=tests),
        "unexpected_skips": metric(unexpected, numerator=unexpected, denominator=tests),
    }
    unmeasured: dict[str, str] = {}
    rate(passed, executed, "executed_pass_rate", metrics, unmeasured)
    if executed == 0:
        status = "ERROR"  # nothing ran: there is no evidence either way
    else:
        status = "BLOCKED" if failed or unexpected else "PASS"
    return {"dataset": {"path": None, "fingerprint": None, "case_count": None,
                        "corpus_fingerprint": None},
            "identity_extra": {"suite": "offline pytest"}, "parameters": {},
            "thresholds": {"allowed_skip_modules": ["tests.test_llm_live", "tests.test_rag_live"]},
            "metrics": metrics, "unmeasured": unmeasured, "cases": [], "status": status,
            "deterministic": True}


def build_ragas_advisory(report: Mapping[str, Any]) -> dict[str, Any]:
    _require(report, ("judge", "summary"), "Ragas advisory report")
    metrics: dict[str, Any] = {}
    unmeasured: dict[str, str] = {}
    errors = 0
    for name, summary in report["summary"].items():
        evaluated = summary.get("evaluated", 0)
        errors += summary.get("errors", 0)
        if not evaluated or summary.get("mean") is None:
            unmeasured[f"ragas_{name}"] = "no case was scored"
            continue
        metrics[f"ragas_{name}"] = metric(summary["mean"], MetricKind.ADVISORY,
                                          denominator=evaluated)
    calibration = report.get("calibration", {})
    return {"dataset": {"path": report.get("source_results"), "fingerprint": None,
                        "case_count": None, "corpus_fingerprint": None},
            "identity_extra": {"judge": report["judge"], "embeddings": report.get("embeddings")},
            "parameters": {"calibration": calibration.get("status")},
            "thresholds": {"policy": "advisory only; never release-blocking"},
            "metrics": metrics, "unmeasured": unmeasured, "cases": [],
            "status": "ERROR" if errors else "PASS", "deterministic": False}


def build_record(kind: str, report_path: Path, *, dataset: Path | None = None,
                 knowledge_base: Path | None = None, run_id: str | None = None,
                 recorded_at: str | None = None, source: Mapping[str, Any] | None = None,
                 note: str | None = None) -> dict[str, Any]:
    """Build a validated record. ``kind``: custom_engine | promptfoo | rag | pytest | ragas."""
    from ai_quality_lab.rag.dataset import DEFAULT_RAG_DATASET
    from ai_quality_lab.rag.documents import DEFAULT_KNOWLEDGE_BASE
    from ai_quality_lab.runner import DEFAULT_DATASET_PATH

    if kind == "promptfoo":
        parts, etype = build_promptfoo(report_path, dataset or DEFAULT_DATASET_PATH), \
            EvaluationType.PROMPTFOO
    else:
        report = _load_report(report_path)
        if kind == "custom_engine":
            parts = build_custom_engine(report, dataset or DEFAULT_DATASET_PATH)
            etype = EvaluationType.CUSTOM_ENGINE
        elif kind == "rag":
            parts = build_rag(report, dataset or DEFAULT_RAG_DATASET,
                              knowledge_base or DEFAULT_KNOWLEDGE_BASE)
            etype = parts.pop("evaluation_type")
        elif kind == "pytest":
            parts, etype = build_pytest(report), EvaluationType.PYTEST
        elif kind == "ragas":
            parts, etype = build_ragas_advisory(report), EvaluationType.RAGAS_ADVISORY
        else:
            raise RecordError(f"unknown record kind {kind!r}")

    identity = {"evaluation_type": etype.value, **parts.pop("identity_extra")}
    source = dict(source if source is not None else git_source())
    # Content hash of the source report (canonical JSON, so BOM / line endings don't matter):
    # lets `record` warn when the same report is recorded twice.
    source.update({"report_file": Path(report_path).as_posix(), "note": note,
                   "report_sha256": short_hash(_load_report(report_path))})
    recorded_at = recorded_at or utc_now()
    record = {
        "schema_version": SCHEMA_VERSION,
        "run_id": run_id or f"{recorded_at.replace(':', '').replace('-', '')}-{etype.value}-"
                            f"{os.urandom(3).hex()}",
        "recorded_at": recorded_at,
        "evaluation_type": etype.value,
        "source": source,
        "identity": identity,
        "comparability_key": short_hash(identity),
        **parts,
    }
    errors = validate_record(record)
    if errors:
        raise RecordError("built record is invalid: " + "; ".join(errors))
    return record


# --- Validation ---------------------------------------------------------------------------------

REQUIRED_FIELDS = {
    "schema_version": int, "run_id": str, "recorded_at": str, "evaluation_type": str,
    "deterministic": bool, "source": dict, "dataset": dict, "identity": dict,
    "comparability_key": str, "parameters": dict, "thresholds": dict, "status": str,
    "metrics": dict, "unmeasured": dict, "cases": list,
}


def _is_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def validate_record(record: Any) -> list[str]:
    """Every problem found (empty list = valid). Incompatible versions are rejected, not guessed."""
    if not isinstance(record, dict):
        return ["record is not a JSON object"]
    errors: list[str] = []
    if record.get("schema_version") != SCHEMA_VERSION:
        return [f"unsupported schema_version {record.get('schema_version')!r} "
                f"(this tool reads version {SCHEMA_VERSION})"]
    for name, kind in REQUIRED_FIELDS.items():
        if name not in record:
            errors.append(f"missing field {name!r}")
        elif not isinstance(record[name], kind) or (kind is int and isinstance(record[name], bool)):
            errors.append(f"field {name!r} must be {kind.__name__}")
    if errors:
        return errors
    if not record["run_id"].strip():
        errors.append("run_id is empty")
    if not TIMESTAMP_PATTERN.match(record["recorded_at"]):
        errors.append(f"recorded_at {record['recorded_at']!r} is not UTC 'YYYY-MM-DDTHH:MM:SSZ'")
    else:
        try:
            datetime.strptime(record["recorded_at"], TIMESTAMP_FORMAT)
        except ValueError:
            errors.append(f"recorded_at {record['recorded_at']!r} is not a valid date")
    if record["evaluation_type"] not in {t.value for t in EvaluationType}:
        errors.append(f"unknown evaluation_type {record['evaluation_type']!r}")
    if record["identity"].get("evaluation_type") != record["evaluation_type"]:
        errors.append("identity.evaluation_type does not match evaluation_type")
    if record["comparability_key"] != short_hash(record["identity"]):
        errors.append("comparability_key does not match identity (edited or corrupted record)")
    if record["status"] not in STATUSES:
        errors.append(f"status must be one of {STATUSES}")
    for name, m in record["metrics"].items():
        errors += [f"metric {name!r}: {e}" for e in _metric_errors(name, m)]
    overlap = set(record["metrics"]) & set(record["unmeasured"])
    if overlap:
        errors.append(f"metric(s) both measured and unmeasured: {sorted(overlap)}")
    for case in record["cases"]:
        if (not isinstance(case, dict) or not isinstance(case.get("id"), str)
                or case.get("outcome") not in CASE_OUTCOMES
                or case.get("severity") not in SEVERITIES):
            errors.append(f"invalid case entry {case!r}")
            break
    ids = [c.get("id") for c in record["cases"] if isinstance(c, dict)]
    if len(ids) != len(set(ids)):
        errors.append("duplicate case ids")
    count = record["dataset"].get("case_count")
    if record["cases"] and count is not None and count != len(record["cases"]):
        errors.append(f"dataset.case_count {count} != number of case results {len(ids)}")
    return errors


class MetricUnit(str, Enum):
    COUNT = "count"  # integer; optional numerator (== value) and denominator (scope); 0 of 0 ok
    RATE = "rate"    # numerator / denominator, both required, denominator > 0, value in [0, 1]
    SCORE = "score"  # a mean in [0, 1] over `denominator` items; numerator must be null


COUNT_METRICS = (
    "case_count", "error_count", "critical_failures", "high_failures", "medium_failures",
    "safety_failures", "restricted_retrievals", "test_count", "executed_tests", "passed_tests",
    "tests_failed", "expected_skips", "unexpected_skips",
)
RATE_METRICS = (
    "pass_rate", "answerable_abstention_rate", "over_abstention_rate", "executed_pass_rate",
    "test_pass_rate",  # legacy (Phase 8.0, denominator included skips): read, never produced
)
SCORE_METRICS = ("doc_hit_rate", "doc_recall", "doc_precision", "doc_mrr", "doc_ndcg")
METRIC_CATALOG: dict[str, tuple[MetricUnit, MetricKind]] = {
    **{n: (MetricUnit.COUNT, MetricKind.DETERMINISTIC) for n in COUNT_METRICS},
    **{n: (MetricUnit.RATE, MetricKind.DETERMINISTIC) for n in RATE_METRICS},
    **{n: (MetricUnit.SCORE, MetricKind.DETERMINISTIC) for n in SCORE_METRICS},
}
ADVISORY_PREFIX = "ragas_"  # ragas_<metric>: advisory LLM-judge score


def metric_spec(name: str) -> tuple[MetricUnit, MetricKind] | None:
    """Unit and kind of a metric, from its name (never from the value's Python type)."""
    if name in METRIC_CATALOG:
        return METRIC_CATALOG[name]
    if name.startswith(ADVISORY_PREFIX) and len(name) > len(ADVISORY_PREFIX):
        return MetricUnit.SCORE, MetricKind.ADVISORY
    return None


def _metric_errors(name: str, m: Any) -> list[str]:
    if not isinstance(m, dict):
        return ["not an object"]
    if set(m) != {"value", "numerator", "denominator", "kind"}:
        return [f"fields must be value/numerator/denominator/kind, got {sorted(m)}"]
    errors = []
    value, num, den = m["value"], m["numerator"], m["denominator"]
    if not _is_number(value):
        errors.append(f"value {value!r} is not a finite number (missing data belongs in "
                      "'unmeasured')")
    if m["kind"] not in {k.value for k in MetricKind}:
        errors.append(f"unknown kind {m['kind']!r}")
    for part, v in (("numerator", num), ("denominator", den)):
        if v is not None and (not isinstance(v, int) or isinstance(v, bool) or v < 0):
            errors.append(f"{part} must be a non-negative integer or null")
    spec = metric_spec(name)
    if spec is None:
        errors.append("unknown metric name (not in the metric catalog)")
    elif m["kind"] != spec[1].value:
        errors.append(f"kind must be {spec[1].value!r} for this metric, got {m['kind']!r}")
    if errors:
        return errors
    if isinstance(num, int) and isinstance(den, int) and num > den:
        return [f"numerator {num} > denominator {den}"]
    unit = spec[0]
    if unit is MetricUnit.COUNT:
        if isinstance(value, float) or value < 0:
            errors.append(f"count value {value!r} must be a non-negative integer")
        elif num is not None and value != num:
            errors.append(f"count value {value} disagrees with numerator {num}")
    elif unit is MetricUnit.RATE:
        if num is None or den is None:
            errors.append("a rate needs both numerator and denominator")
        elif den == 0:
            errors.append("rate with denominator 0 (an undefined rate belongs in 'unmeasured')")
        elif not 0 <= value <= 1:
            errors.append(f"rate value {value} is outside [0, 1]")
        elif abs(value - round(num / den, 4)) > 1e-4:
            errors.append(f"value {value} disagrees with {num}/{den}")
    else:  # SCORE
        if num is not None:
            errors.append("a score has no numerator (it is a mean, not a count ratio)")
        elif den is None or den == 0:
            errors.append("a score needs the number of scored items as its denominator (> 0)")
        elif not 0 <= value <= 1:
            errors.append(f"score value {value} is outside [0, 1]")
    return errors
