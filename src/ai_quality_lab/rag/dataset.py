"""Ground-truth RAG evaluation dataset (``evals/rag_cases.json``).

Each case keeps these concepts separate:

* ``reference_context``: known-correct passages (verbatim sentences from the cited documents).
  This is the ground truth. Retrieved context is produced at run time and is NEVER ground truth.
* ``relevant_document_ids``: documents that contain the answer (empty when it is not in the corpus).
* ``expected_output``: a reference answer, for humans and for Ragas (``reference``).
* ``expected_facts``: strings the generated answer must contain. ``"a || b"`` means either form.
* ``incorrect_facts``: known-wrong values (outdated or contradicted) the answer must not state.
* ``prohibited_content``: strings that indicate a leak or followed injection.
* ``injection_document_ids``: adversarial documents whose instructions must not be followed.
* ``expected_behavior``: ``answer`` | ``not_found`` | ``refuse`` | ``safe`` (``safe`` checks
  only that nothing prohibited is disclosed; ``refuse`` also requires an explicit decline).

Chunk-level relevance is derived, not stored: a chunk is relevant if it contains a reference
passage. That keeps ground truth valid when chunk size changes, and validation proves every
passage exists in a relevant document.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from ai_quality_lab.dataset import DatasetValidationError, Severity, normalize_for_match
from ai_quality_lab.rag.chunking import Chunk
from ai_quality_lab.rag.documents import Document

DEFAULT_RAG_DATASET = Path("evals") / "rag_cases.json"
SCHEMA_VERSION = 1


class RAGCategory(str, Enum):
    FACTUAL = "factual"
    MULTI_DOCUMENT = "multi_document"
    IRRELEVANT_NOISE = "irrelevant_noise"
    ANSWER_NOT_PRESENT = "answer_not_present"
    INSUFFICIENT_INFORMATION = "insufficient_information"
    UNCERTAINTY = "uncertainty"
    CONTRADICTORY_DOCUMENTS = "contradictory_documents"
    OUTDATED_INFORMATION = "outdated_information"
    DISTRIBUTED_ANSWER = "distributed_answer"
    EMBEDDED_INJECTION = "embedded_injection"
    PROMPT_EXTRACTION = "prompt_extraction"
    GUARDRAIL_OVERRIDE = "guardrail_override"
    REFUSAL_REQUIRED = "refusal_required"
    MISLEADING_SIMILARITY = "misleading_similarity"


class ExpectedBehavior(str, Enum):
    ANSWER = "answer"  # must contain the expected facts
    NOT_FOUND = "not_found"  # must say the information is unavailable
    REFUSE = "refuse"  # must explicitly decline (or be blocked)
    SAFE = "safe"  # any answer is acceptable if nothing prohibited/restricted is disclosed


@dataclass(frozen=True)
class RAGCase:
    id: str
    category: RAGCategory
    severity: Severity
    question: str
    expected_behavior: ExpectedBehavior
    expected_output: str
    reference_context: tuple[str, ...]
    relevant_document_ids: tuple[str, ...]
    expected_facts: tuple[str, ...] = ()
    incorrect_facts: tuple[str, ...] = ()
    prohibited_content: tuple[str, ...] = ()
    injection_document_ids: tuple[str, ...] = ()
    configuration: Mapping[str, Any] | None = None
    tags: tuple[str, ...] = ()

    def fact_alternatives(self, fact: str) -> tuple[str, ...]:
        return tuple(a.strip() for a in fact.split("||") if a.strip())


_FIELDS = {"id", "category", "severity", "question", "expected_behavior", "expected_output",
           "reference_context", "relevant_document_ids", "expected_facts", "incorrect_facts",
           "prohibited_content", "injection_document_ids", "configuration", "tags", "notes"}
_REQUIRED = ("id", "category", "severity", "question", "expected_behavior", "expected_output",
             "reference_context", "relevant_document_ids")
_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.-]*$")


def load_rag_dataset(path: str | Path = DEFAULT_RAG_DATASET,
                     documents: Sequence[Document] | None = None) -> tuple[RAGCase, ...]:
    """Load and validate. With ``documents``, also validate every reference against the corpus."""
    try:
        raw = json.loads(Path(path).read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DatasetValidationError([f"Invalid JSON at line {exc.lineno}: {exc.msg}."]) from exc
    cases = parse_rag_dataset(raw)
    if documents is not None:
        validate_against_corpus(cases, documents)
    return cases


def parse_rag_dataset(raw: Any) -> tuple[RAGCase, ...]:
    if not isinstance(raw, Mapping) or raw.get("schema_version") != SCHEMA_VERSION:
        raise DatasetValidationError([f"Dataset must be an object with schema_version "
                                      f"{SCHEMA_VERSION}."])
    items = raw.get("cases")
    if not isinstance(items, list) or not items:
        raise DatasetValidationError(["'cases' must be a non-empty list."])
    problems: list[str] = []
    cases: list[RAGCase] = []
    seen: set[str] = set()
    for index, item in enumerate(items):
        where = f"cases[{index}]"
        errors: list[str] = []
        case = _parse_case(item, errors)
        if case is not None:
            where = f"{where} ({case.id})"
            if case.id in seen:
                errors.append(f"duplicate id '{case.id}'.")
            seen.add(case.id)
        problems.extend(f"{where}: {e}" for e in errors)
        if case is not None and not errors:
            cases.append(case)
    if problems:
        raise DatasetValidationError(problems)
    return tuple(cases)


def _strings(item: Mapping[str, Any], key: str, errors: list[str]) -> tuple[str, ...]:
    value = item.get(key, [])
    if not isinstance(value, list) or not all(isinstance(v, str) and v.strip() for v in value):
        errors.append(f"'{key}' must be a list of non-blank strings.")
        return ()
    return tuple(value)


def _parse_case(item: Any, errors: list[str]) -> RAGCase | None:
    if not isinstance(item, Mapping):
        errors.append("case must be an object.")
        return None
    missing = [k for k in _REQUIRED if k not in item]
    unknown = sorted(set(item) - _FIELDS)
    if missing:
        errors.append(f"missing field(s) {missing}.")
    if unknown:
        errors.append(f"unsupported field(s) {unknown}.")
    if missing:
        return None
    for key in ("id", "question", "expected_output"):
        if not isinstance(item[key], str) or not item[key].strip():
            errors.append(f"'{key}' must be a non-blank string.")
    if isinstance(item["id"], str) and not _ID.fullmatch(item["id"]):
        errors.append(f"invalid id {item['id']!r}.")
    enums = {}
    for key, enum_type in (("category", RAGCategory), ("severity", Severity),
                           ("expected_behavior", ExpectedBehavior)):
        try:
            enums[key] = enum_type(item[key])
        except ValueError:
            errors.append(f"'{key}' must be one of {[m.value for m in enum_type]}.")
    lists = {key: _strings(item, key, errors) for key in (
        "reference_context", "relevant_document_ids", "expected_facts", "incorrect_facts",
        "prohibited_content", "injection_document_ids", "tags")}
    configuration = item.get("configuration")
    if configuration is not None and not isinstance(configuration, Mapping):
        errors.append("'configuration' must be an object.")
    behavior = enums.get("expected_behavior")
    if behavior is ExpectedBehavior.ANSWER:
        for key in ("reference_context", "relevant_document_ids", "expected_facts"):
            if not lists[key]:
                errors.append(f"an 'answer' case needs non-empty '{key}'.")
    elif behavior is not None and lists["expected_facts"]:
        errors.append(f"a '{behavior.value}' case must not list expected_facts.")
    if errors:
        return None
    return RAGCase(
        id=item["id"], category=enums["category"], severity=enums["severity"],
        question=item["question"], expected_behavior=behavior,
        expected_output=item["expected_output"], configuration=configuration, **lists,
    )


def validate_against_corpus(cases: Sequence[RAGCase], documents: Sequence[Document]) -> None:
    """Every referenced document must exist, and every reference passage must appear verbatim
    (whitespace/case-normalized) in one of the case's relevant documents."""
    by_id = {d.id: d for d in documents}
    problems = []
    for case in cases:
        for doc_id in case.relevant_document_ids + case.injection_document_ids:
            if doc_id not in by_id:
                problems.append(f"{case.id}: unknown document id '{doc_id}'.")
        texts = [normalize_for_match(by_id[d].content) for d in case.relevant_document_ids
                 if d in by_id]
        for passage in case.reference_context:
            if not any(normalize_for_match(passage) in text for text in texts):
                problems.append(f"{case.id}: reference passage not found in its relevant "
                                f"documents: {passage[:60]!r}.")
    if problems:
        raise DatasetValidationError(problems)


def relevant_chunk_ids(case: RAGCase, chunks: Sequence[Chunk]) -> tuple[str, ...]:
    """Chunks (of relevant documents) that contain at least one reference passage."""
    passages = [normalize_for_match(p) for p in case.reference_context]
    return tuple(
        c.id for c in chunks
        if c.doc_id in case.relevant_document_ids
        and any(p in normalize_for_match(c.text) for p in passages)
    )
