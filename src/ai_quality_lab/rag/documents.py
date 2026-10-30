"""Knowledge-base documents: one Markdown file per document with a small metadata header.

File format::

    ---
    id: SEC-001
    title: Password and Authentication Policy
    category: security_policy
    classification: internal        # public | internal | restricted
    version: 2                      # any further ``key: value`` lines become metadata
    ---
    Body text. Paragraphs are separated by blank lines.

The header parser is deliberately tiny (``key: value`` lines only), so no YAML dependency is needed.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

DEFAULT_KNOWLEDGE_BASE = Path("data") / "knowledge_base"
CLASSIFICATIONS = ("public", "internal", "restricted")
_REQUIRED = ("id", "title", "category", "classification")


class DocumentError(ValueError):
    """A knowledge-base document is missing or malformed."""


@dataclass(frozen=True)
class Document:
    id: str
    title: str
    category: str
    classification: str
    content: str
    source: str
    metadata: Mapping[str, str] = field(default_factory=dict)


def parse_document(text: str, source: str) -> Document:
    """Parse one document file's text. ``source`` identifies where it came from."""
    lines = text.lstrip("﻿").splitlines()
    if not lines or lines[0].strip() != "---":
        raise DocumentError(f"{source}: missing '---' metadata header.")
    try:
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
    except StopIteration:
        raise DocumentError(f"{source}: metadata header is not closed with '---'.") from None
    header: dict[str, str] = {}
    for line in lines[1:end]:
        if not line.strip():
            continue
        key, sep, value = line.partition(":")
        if not sep or not key.strip():
            raise DocumentError(f"{source}: invalid header line {line!r} (expected 'key: value').")
        header[key.strip()] = value.strip()
    missing = [k for k in _REQUIRED if not header.get(k)]
    if missing:
        raise DocumentError(f"{source}: missing header field(s) {missing}.")
    if header["classification"] not in CLASSIFICATIONS:
        raise DocumentError(
            f"{source}: classification must be one of {list(CLASSIFICATIONS)}, "
            f"got {header['classification']!r}."
        )
    metadata = {k: v for k, v in header.items() if k not in _REQUIRED}
    return Document(
        id=header["id"],
        title=header["title"],
        category=header["category"],
        classification=header["classification"],
        content="\n".join(lines[end + 1:]).strip(),
        source=source,
        metadata=metadata,
    )


def load_documents(directory: str | Path = DEFAULT_KNOWLEDGE_BASE) -> tuple[Document, ...]:
    """Load every ``*.md`` document, sorted by ID. Raises on duplicates or malformed files."""
    folder = Path(directory)
    if not folder.is_dir():
        raise DocumentError(f"Knowledge base folder not found: {folder}")
    documents = [
        parse_document(path.read_text(encoding="utf-8"), source=path.as_posix())
        for path in sorted(folder.glob("*.md"))
    ]
    if not documents:
        raise DocumentError(f"No *.md documents found in {folder}")
    ids = [d.id for d in documents]
    duplicates = sorted({i for i in ids if ids.count(i) > 1})
    if duplicates:
        raise DocumentError(f"Duplicate document id(s): {duplicates}")
    return tuple(sorted(documents, key=lambda d: d.id))


def documents_fingerprint(documents: Sequence[Document]) -> str:
    """Stable hash of IDs, metadata and content; used to detect a stale vector index."""
    digest = hashlib.sha256()
    for doc in sorted(documents, key=lambda d: d.id):
        digest.update(f"{doc.id}\x00{doc.title}\x00{doc.classification}\x00".encode())
        digest.update(repr(sorted(doc.metadata.items())).encode())
        digest.update(doc.content.encode())
    return digest.hexdigest()[:16]
