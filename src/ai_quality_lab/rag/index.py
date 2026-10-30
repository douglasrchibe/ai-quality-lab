"""Ingestion: documents -> chunks -> embeddings -> vector store (and the index CLI).

    python -m ai_quality_lab.rag.index build     # (re)build from data/knowledge_base
    python -m ai_quality_lab.rag.index info      # show what an index contains

The index file lives under ``.rag_index/`` (git-ignored) and is named after the embedding model,
so indexes built with different embedders never mix. It can always be rebuilt from the documents.
"""

from __future__ import annotations

import argparse
import re
import sys
from collections.abc import Sequence
from pathlib import Path

from ai_quality_lab.llm_client import LLMError, load_env_file
from ai_quality_lab.rag.chunking import ChunkingConfig, chunk_documents
from ai_quality_lab.rag.documents import (
    DEFAULT_KNOWLEDGE_BASE,
    Document,
    DocumentError,
    documents_fingerprint,
    load_documents,
)
from ai_quality_lab.rag.embeddings import Embedder, embedder_from_env
from ai_quality_lab.rag.vector_store import VectorIndexError, VectorStore

INDEX_DIR = Path(".rag_index")


def default_index_path(embedder: Embedder) -> Path:
    return INDEX_DIR / (re.sub(r"[^A-Za-z0-9._-]+", "_", embedder.model_name) + ".json")


def embedding_text(title: str, text: str) -> str:
    """Text that is embedded for a chunk: the document title gives short chunks topical context."""
    return f"{title}\n{text}"


def build_index(
    documents: Sequence[Document], embedder: Embedder, chunking: ChunkingConfig | None = None
) -> VectorStore:
    chunking = chunking or ChunkingConfig()
    chunks = chunk_documents(documents, chunking)
    vectors = embedder.embed_documents([embedding_text(c.title, c.text) for c in chunks])
    store = VectorStore(
        embedder.model_name,
        {
            "chunk_size": chunking.chunk_size,
            "overlap": chunking.overlap,
            "documents_fingerprint": documents_fingerprint(documents),
            "document_count": len(documents),
        },
    )
    store.add(chunks, vectors)
    return store


def load_or_build_index(
    documents: Sequence[Document],
    embedder: Embedder,
    chunking: ChunkingConfig | None = None,
    path: Path | None = None,
    rebuild: bool = False,
) -> VectorStore:
    """Load a saved index if it matches the documents, embedder and chunking; otherwise rebuild."""
    chunking = chunking or ChunkingConfig()
    path = path or default_index_path(embedder)
    if not rebuild and path.is_file():
        try:
            store = VectorStore.load(path)
            if (store.embedding_model == embedder.model_name
                    and store.info.get("documents_fingerprint") == documents_fingerprint(documents)
                    and store.info.get("chunk_size") == chunking.chunk_size
                    and store.info.get("overlap") == chunking.overlap):
                return store
        except VectorIndexError:
            pass  # corrupt or old format: rebuild below
    store = build_index(documents, embedder, chunking)
    store.save(path)
    return store


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m ai_quality_lab.rag.index")
    parser.add_argument("command", choices=["build", "info"])
    parser.add_argument("--docs", type=Path, default=DEFAULT_KNOWLEDGE_BASE)
    parser.add_argument("--chunk-size", type=int, default=ChunkingConfig.chunk_size)
    parser.add_argument("--overlap", type=int, default=ChunkingConfig.overlap)
    args = parser.parse_args(argv)

    load_env_file()
    try:
        embedder = embedder_from_env()
        documents = load_documents(args.docs)
        chunking = ChunkingConfig(args.chunk_size, args.overlap)
        path = default_index_path(embedder)
        if args.command == "build":
            store = load_or_build_index(documents, embedder, chunking, path, rebuild=True)
            print(f"Built {path}: {len(store)} chunks from {len(documents)} documents, "
                  f"embedder {store.embedding_model}, dimension {store.dimension}.")
        else:
            store = VectorStore.load(path)
            print(f"{path}: {len(store)} chunks, embedder {store.embedding_model}, "
                  f"dimension {store.dimension}, info {store.info}")
    except (LLMError, DocumentError, VectorIndexError, ValueError) as exc:
        print(f"Index error: {exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
