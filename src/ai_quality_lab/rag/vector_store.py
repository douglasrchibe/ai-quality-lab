"""A small in-memory vector store with JSON persistence.

~40 chunks do not justify a vector database: an exact cosine-similarity scan is instant, fully
deterministic, and easy to inspect. The saved file records the embedding model, dimension,
chunking settings and a fingerprint of the source documents, so a stale index is detected
instead of silently used.
"""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ai_quality_lab.rag.chunking import Chunk

FORMAT_VERSION = 1


class VectorIndexError(RuntimeError):
    """The vector index is missing, invalid, or stale."""


@dataclass(frozen=True)
class SearchHit:
    chunk: Chunk
    score: float


class VectorStore:
    def __init__(self, embedding_model: str, info: Mapping[str, Any] | None = None):
        self.embedding_model = embedding_model
        self.info = dict(info or {})  # e.g. chunking settings, document fingerprint
        self._chunks: list[Chunk] = []
        self._vectors: list[list[float]] = []
        self.dimension: int | None = None

    def __len__(self) -> int:
        return len(self._chunks)

    @property
    def chunks(self) -> tuple[Chunk, ...]:
        return tuple(self._chunks)

    def add(self, chunks: Sequence[Chunk], vectors: Sequence[Sequence[float]]) -> None:
        if len(chunks) != len(vectors):
            raise ValueError(f"{len(chunks)} chunks but {len(vectors)} vectors.")
        existing = {c.id for c in self._chunks}
        for chunk, vector in zip(chunks, vectors):
            if chunk.id in existing:
                raise ValueError(f"Duplicate chunk id {chunk.id!r}.")
            if self.dimension is None:
                self.dimension = len(vector)
            if len(vector) != self.dimension or not vector:
                raise ValueError(
                    f"Chunk {chunk.id} has dimension {len(vector)}, expected {self.dimension}."
                )
            self._chunks.append(chunk)
            self._vectors.append([float(v) for v in vector])
            existing.add(chunk.id)

    def search(self, query_vector: Sequence[float], top_k: int | None = None) -> list[SearchHit]:
        """All chunks ranked by cosine similarity (vectors are pre-normalized), ties by chunk id."""
        if self.dimension is not None and len(query_vector) != self.dimension:
            raise ValueError(
                f"Query dimension {len(query_vector)} does not match index dimension "
                f"{self.dimension} (embedding model {self.embedding_model})."
            )
        hits = [
            SearchHit(chunk, round(sum(q * v for q, v in zip(query_vector, vector)), 6))
            for chunk, vector in zip(self._chunks, self._vectors)
        ]
        hits.sort(key=lambda h: (-h.score, h.chunk.id))
        return hits if top_k is None else hits[:top_k]

    def save(self, path: str | Path) -> None:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "format_version": FORMAT_VERSION,
            "embedding_model": self.embedding_model,
            "dimension": self.dimension,
            "info": self.info,
            "chunks": [
                {**asdict(chunk), "metadata": dict(chunk.metadata), "vector": vector}
                for chunk, vector in zip(self._chunks, self._vectors)
            ],
        }
        path.write_text(json.dumps(payload), encoding="utf-8")

    @classmethod
    def load(cls, path: str | Path) -> VectorStore:
        path = Path(path)
        if not path.is_file():
            raise VectorIndexError(f"No vector index at {path}. Build it with: "
                              "python -m ai_quality_lab.rag.index build")
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if payload.get("format_version") != FORMAT_VERSION:
                raise VectorIndexError(f"Unsupported index format in {path}; rebuild the index.")
            store = cls(payload["embedding_model"], payload.get("info"))
            chunks = [
                Chunk(c["id"], c["doc_id"], c["index"], c["text"], c["metadata"])
                for c in payload["chunks"]
            ]
            store.add(chunks, [c["vector"] for c in payload["chunks"]])
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise VectorIndexError(
                f"Corrupt vector index {path}: {exc}. Rebuild the index."
            ) from exc
        return store
