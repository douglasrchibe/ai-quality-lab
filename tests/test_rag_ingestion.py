"""Tests for knowledge-base documents and chunking (offline, deterministic)."""

import pytest

from ai_quality_lab.rag.chunking import (
    ChunkingConfig,
    chunk_document,
    chunk_documents,
    split_sentences,
)
from ai_quality_lab.rag.documents import (
    Document,
    DocumentError,
    documents_fingerprint,
    load_documents,
    parse_document,
)


def doc(content, doc_id="D-1", **meta):
    return Document(doc_id, "Title", "cat", "internal", content, "test.md", meta)


# --- Documents ----------------------------------------------------------------------------------


def test_repository_knowledge_base_loads(repo_documents):
    ids = [d.id for d in repo_documents]
    assert len(ids) == 16 and ids == sorted(ids)
    assert {d.classification for d in repo_documents} == {"public", "internal", "restricted"}
    assert all(d.content for d in repo_documents)
    adversarial = [d for d in repo_documents if d.id.startswith("MAL-")]
    assert len(adversarial) == 4


def test_parse_document_header_and_metadata():
    parsed = parse_document(
        "---\nid: X-1\ntitle: T\ncategory: c\nclassification: public\nversion: 2\n---\nBody.\n",
        "x.md",
    )
    assert (parsed.id, parsed.classification, parsed.content) == ("X-1", "public", "Body.")
    assert parsed.metadata == {"version": "2"}


@pytest.mark.parametrize(
    "text, fragment",
    [
        ("no header", "missing '---'"),
        ("---\nid: X\n", "not closed"),
        ("---\nid: X\ntitle: T\ncategory: c\n---\nB", "missing header field(s) ['classification']"),
        ("---\nid: X\ntitle: T\ncategory: c\nclassification: secret\n---\nB",
         "classification must"),
        ("---\njust text\n---\nB", "invalid header line"),
    ],
)
def test_malformed_documents_rejected(text, fragment):
    with pytest.raises(DocumentError, match=fragment.replace("(", r"\(").replace(")", r"\)")
                       .replace("[", r"\[").replace("]", r"\]")):
        parse_document(text, "bad.md")


def test_duplicate_ids_rejected(tmp_path):
    body = "---\nid: SAME\ntitle: T\ncategory: c\nclassification: public\n---\nB\n"
    (tmp_path / "a.md").write_text(body, encoding="utf-8")
    (tmp_path / "b.md").write_text(body, encoding="utf-8")
    with pytest.raises(DocumentError, match="Duplicate document id"):
        load_documents(tmp_path)


def test_fingerprint_changes_with_content(repo_documents):
    original = documents_fingerprint(repo_documents)
    assert documents_fingerprint(list(reversed(repo_documents))) == original
    changed = [doc("different")] + list(repo_documents[1:])
    assert documents_fingerprint(changed) != original


# --- Chunking -----------------------------------------------------------------------------------


def test_split_sentences_respects_paragraphs():
    text = "First rule. Second rule!\n\nNew paragraph without period\nstill same paragraph."
    assert split_sentences(text) == [
        "First rule.", "Second rule!", "New paragraph without period still same paragraph."
    ]


def test_normal_document_chunks_preserve_every_sentence(repo_documents):
    config = ChunkingConfig(chunk_size=30, overlap=8)
    for document in repo_documents:
        chunks = chunk_document(document, config)
        joined = " ".join(c.text for c in chunks)
        for sentence in split_sentences(document.content):
            assert sentence in joined, (document.id, sentence)


def test_very_short_document_is_one_chunk():
    (chunk,) = chunk_document(doc("Only one sentence."))
    assert (chunk.id, chunk.text, chunk.index) == ("D-1#000", "Only one sentence.", 0)


@pytest.mark.parametrize("content", ["", "   \n\n  "])
def test_empty_document_produces_no_chunks(content):
    assert chunk_document(doc(content)) == ()


def test_large_document_respects_chunk_size_and_overlaps():
    sentences = [f"Sentence number {i} has exactly seven words." for i in range(40)]
    chunks = chunk_document(doc(" ".join(sentences)), ChunkingConfig(chunk_size=21, overlap=7))
    assert len(chunks) > 5
    assert all(len(c.text.split()) <= 21 for c in chunks)
    for previous, current in zip(chunks, chunks[1:]):
        last_sentence = previous.text.split(". ")[-1]
        assert last_sentence.rstrip(".") in current.text  # one 7-word sentence carried over


def test_no_overlap_partitions_sentences_exactly():
    sentences = [f"Rule {i} applies to everyone here." for i in range(10)]
    chunks = chunk_document(doc(" ".join(sentences)), ChunkingConfig(chunk_size=12, overlap=0))
    assert " ".join(c.text for c in chunks) == " ".join(sentences)


def test_oversized_sentence_split_into_windows_without_loss():
    words = [f"w{i}" for i in range(25)]
    chunks = chunk_document(doc(" ".join(words) + "."), ChunkingConfig(chunk_size=10, overlap=2))
    assert all(len(c.text.split()) <= 10 for c in chunks)
    covered = {w.rstrip(".") for c in chunks for w in c.text.split()}
    assert covered == set(words)


def test_metadata_preserved_and_ids_unique(repo_documents):
    chunks = chunk_documents(repo_documents, ChunkingConfig(chunk_size=30, overlap=8))
    assert len({c.id for c in chunks}) == len(chunks)
    by_id = {d.id: d for d in repo_documents}
    for chunk in chunks:
        source = by_id[chunk.doc_id]
        assert chunk.id.startswith(f"{chunk.doc_id}#")
        assert chunk.metadata["title"] == source.title
        assert chunk.metadata["classification"] == source.classification
        assert chunk.metadata["source"] == source.source
        for key, value in source.metadata.items():
            assert chunk.metadata[key] == value


def test_source_relationships_reconstruct_documents(repo_documents):
    chunks = chunk_documents(repo_documents)
    assert {c.doc_id for c in chunks} == {d.id for d in repo_documents}
    for document in repo_documents:
        own = [c for c in chunks if c.doc_id == document.id]
        assert [c.index for c in own] == list(range(len(own)))


def test_chunking_is_deterministic(repo_documents):
    assert chunk_documents(repo_documents) == chunk_documents(repo_documents)


@pytest.mark.parametrize("size, overlap", [(0, 0), (10, 10), (10, -1)])
def test_invalid_chunking_config_rejected(size, overlap):
    with pytest.raises(ValueError):
        ChunkingConfig(chunk_size=size, overlap=overlap)
