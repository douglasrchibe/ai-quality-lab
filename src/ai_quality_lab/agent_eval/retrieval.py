"""Integration adapter: ``search_kb`` backed by the real Phase 6 retriever.

Used only by the retrieval integration tests, never by the 28 deterministic scenarios (they declare
their own synthetic passages). It reuses Phase 6 unchanged: the knowledge base in
``data/knowledge_base/``, the offline ``HashingEmbedder`` (no network), the default chunking, and the
``Retriever`` with its classification filter, so restricted documents never reach the agent. The
index is built in memory; nothing is written to disk.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

from ai_quality_lab.agent.sandbox import KBPassage
from ai_quality_lab.rag.chunking import ChunkingConfig
from ai_quality_lab.rag.documents import DEFAULT_KNOWLEDGE_BASE, load_documents
from ai_quality_lab.rag.embeddings import HashingEmbedder
from ai_quality_lab.rag.index import build_index
from ai_quality_lab.rag.retrieval import DEFAULT_TOP_K, Retriever


def phase6_kb_search(knowledge_base: Path = DEFAULT_KNOWLEDGE_BASE,
                     top_k: int = DEFAULT_TOP_K) -> Callable[[str], Sequence[KBPassage]]:
    """A ``kb_search`` function for the sandbox, using the Phase 6 retrieval pipeline."""
    embedder = HashingEmbedder()
    store = build_index(load_documents(knowledge_base), embedder, ChunkingConfig())
    retriever = Retriever(store, embedder, top_k=top_k)

    def search(query: str) -> Sequence[KBPassage]:
        return tuple(KBPassage(chunk.doc_id, chunk.text)
                     for chunk in retriever.retrieve(query).chunks)

    return search
