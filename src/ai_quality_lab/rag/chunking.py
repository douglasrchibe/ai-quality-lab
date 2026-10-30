"""Deterministic, sentence-aware chunking.

Strategy: split a document into sentences (paragraph breaks are hard boundaries), then pack
consecutive sentences into chunks of at most ``chunk_size`` words. The next chunk re-uses trailing
sentences of the previous one, up to ``overlap`` words, so a fact near a boundary appears whole in
at least one chunk. A single sentence longer than ``chunk_size`` is split into overlapping word
windows, so no text is ever dropped.

Why sentences rather than fixed character windows: policy documents state one rule per sentence;
keeping sentences intact means a retrieved chunk carries complete, quotable rules, and the
ground-truth reference passages (single sentences) map cleanly onto chunk-level relevance.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

from ai_quality_lab.rag.documents import Document

_SENTENCE_END = re.compile(r"(?<=[.!?])\s+(?=[A-Z0-9\"'(])")


@dataclass(frozen=True)
class ChunkingConfig:
    # Defaults suit this corpus: short policy documents with 10-20-word sentences, so a chunk holds
    # 2-3 complete rules and longer documents span several chunks (36 chunks for 16 documents).
    chunk_size: int = 40  # words
    overlap: int = 10  # words

    def __post_init__(self) -> None:
        if self.chunk_size < 1:
            raise ValueError("chunk_size must be at least 1 word.")
        if not 0 <= self.overlap < self.chunk_size:
            raise ValueError("overlap must be >= 0 and smaller than chunk_size.")


@dataclass(frozen=True)
class Chunk:
    id: str  # "<doc_id>#<index:03d>"
    doc_id: str
    index: int
    text: str
    metadata: Mapping[str, str] = field(default_factory=dict)

    @property
    def title(self) -> str:
        return self.metadata.get("title", "")


def split_sentences(text: str) -> list[str]:
    """Split on blank lines (paragraphs), then on sentence-ending punctuation."""
    sentences: list[str] = []
    for paragraph in re.split(r"\n\s*\n", text):
        flat = " ".join(paragraph.split())
        if flat:
            sentences.extend(s.strip() for s in _SENTENCE_END.split(flat) if s.strip())
    return sentences


def _units(sentences: Sequence[str], config: ChunkingConfig) -> list[str]:
    """Sentences, with any sentence longer than chunk_size split into overlapping word windows."""
    units: list[str] = []
    step = config.chunk_size - config.overlap
    for sentence in sentences:
        words = sentence.split()
        if len(words) <= config.chunk_size:
            units.append(sentence)
            continue
        for start in range(0, len(words), step):
            units.append(" ".join(words[start:start + config.chunk_size]))
            if start + config.chunk_size >= len(words):
                break
    return units


def chunk_document(document: Document, config: ChunkingConfig | None = None) -> tuple[Chunk, ...]:
    """Chunk one document. Empty documents produce no chunks."""
    config = config or ChunkingConfig()
    units = _units(split_sentences(document.content), config)
    sizes = [len(u.split()) for u in units]
    metadata = {
        "title": document.title,
        "category": document.category,
        "classification": document.classification,
        "source": document.source,
        **document.metadata,
    }
    chunks: list[Chunk] = []
    start = 0
    while start < len(units):
        end, words = start, 0
        while end < len(units) and (end == start or words + sizes[end] <= config.chunk_size):
            words += sizes[end]
            end += 1
        chunks.append(Chunk(
            id=f"{document.id}#{len(chunks):03d}",
            doc_id=document.id,
            index=len(chunks),
            text=" ".join(units[start:end]),
            metadata=metadata,
        ))
        if end >= len(units):
            break
        # Step back over trailing units that fit in the overlap budget, but always advance.
        next_start, carried = end, 0
        while next_start - 1 > start and carried + sizes[next_start - 1] <= config.overlap:
            next_start -= 1
            carried += sizes[next_start]
        start = next_start
    return tuple(chunks)


def chunk_documents(
    documents: Sequence[Document], config: ChunkingConfig | None = None
) -> tuple[Chunk, ...]:
    return tuple(chunk for doc in documents for chunk in chunk_document(doc, config))
