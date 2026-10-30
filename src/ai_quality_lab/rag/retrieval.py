"""Retrieval: question -> ranked, filtered chunks with scores and provenance.

Filtering happens in code, not in the prompt:
* ``allowed_classifications`` excludes documents the chatbot may never see (default: public and
  internal; ``restricted`` documents are never retrieved).
* ``min_score`` drops weak matches (default 0.0 = no threshold).
* exact-duplicate chunk text is removed, keeping the best-ranked copy.
Everything removed is recorded on the result, so retrieval decisions stay explainable.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ai_quality_lab.rag.embeddings import Embedder
from ai_quality_lab.rag.vector_store import VectorStore

DEFAULT_TOP_K = 4
DEFAULT_ALLOWED_CLASSIFICATIONS = ("public", "internal")


@dataclass(frozen=True)
class RetrievedChunk:
    rank: int
    chunk_id: str
    doc_id: str
    score: float
    text: str
    metadata: Mapping[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {"rank": self.rank, "chunk_id": self.chunk_id, "doc_id": self.doc_id,
                "score": self.score, "title": self.metadata.get("title", ""), "text": self.text}


@dataclass(frozen=True)
class RetrievalResult:
    query: str
    chunks: tuple[RetrievedChunk, ...]
    top_k: int
    min_score: float
    embedding_model: str
    excluded_by_classification: tuple[str, ...] = ()
    excluded_below_min_score: tuple[str, ...] = ()
    duplicates_removed: tuple[str, ...] = ()

    @property
    def chunk_ids(self) -> tuple[str, ...]:
        return tuple(c.chunk_id for c in self.chunks)

    @property
    def doc_ids(self) -> tuple[str, ...]:
        """Unique document IDs in rank order of their best chunk."""
        return tuple(dict.fromkeys(c.doc_id for c in self.chunks))

    def to_dict(self) -> dict:
        return {
            "query": self.query, "top_k": self.top_k, "min_score": self.min_score,
            "embedding_model": self.embedding_model,
            "chunks": [c.to_dict() for c in self.chunks],
            "excluded_by_classification": list(self.excluded_by_classification),
            "excluded_below_min_score": list(self.excluded_below_min_score),
            "duplicates_removed": list(self.duplicates_removed),
        }


class Retriever:
    def __init__(
        self,
        store: VectorStore,
        embedder: Embedder,
        *,
        top_k: int = DEFAULT_TOP_K,
        min_score: float = 0.0,
        allowed_classifications: Sequence[str] = DEFAULT_ALLOWED_CLASSIFICATIONS,
        dedupe: bool = True,
    ):
        if top_k < 1:
            raise ValueError("top_k must be at least 1.")
        if store.embedding_model != embedder.model_name:
            raise ValueError(
                f"Index was built with {store.embedding_model}, but the query embedder is "
                f"{embedder.model_name}. Rebuild the index or change AIQL_EMBED_*."
            )
        self.store = store
        self.embedder = embedder
        self.top_k = top_k
        self.min_score = min_score
        self.allowed_classifications = tuple(allowed_classifications)
        self.dedupe = dedupe

    def retrieve(self, query: str, top_k: int | None = None, *, worst_first: bool = False
                 ) -> RetrievalResult:
        """Rank chunks for ``query``. ``worst_first`` is a test-only degradation switch."""
        top_k = top_k or self.top_k
        if top_k < 1:
            raise ValueError("top_k must be at least 1.")
        hits = self.store.search(self.embedder.embed_query(query))
        if worst_first:
            hits = list(reversed(hits))
        kept, by_class, below, duplicates, seen_text = [], [], [], [], set()
        for hit in hits:
            chunk = hit.chunk
            if chunk.metadata.get("classification") not in self.allowed_classifications:
                by_class.append(chunk.id)
                continue
            if hit.score < self.min_score:
                below.append(chunk.id)
                continue
            key = " ".join(chunk.text.lower().split())
            if self.dedupe and key in seen_text:
                duplicates.append(chunk.id)
                continue
            if len(kept) < top_k:
                seen_text.add(key)
                kept.append(RetrievedChunk(len(kept) + 1, chunk.id, chunk.doc_id, hit.score,
                                           chunk.text, chunk.metadata))
        return RetrievalResult(
            query=query,
            chunks=tuple(kept),
            top_k=top_k,
            min_score=self.min_score,
            embedding_model=self.store.embedding_model,
            excluded_by_classification=tuple(by_class),
            excluded_below_min_score=tuple(below),
            duplicates_removed=tuple(duplicates),
        )
