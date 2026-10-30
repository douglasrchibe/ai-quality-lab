"""Tests for embeddings, the vector store, the index builder, and retrieval (offline)."""

import json
import math

import pytest

from ai_quality_lab.llm_client import LLMConfigurationError, LLMUnavailableError
from ai_quality_lab.rag.chunking import Chunk, ChunkingConfig
from ai_quality_lab.rag.embeddings import HashingEmbedder, OllamaEmbedder, embedder_from_env
from ai_quality_lab.rag.index import build_index, load_or_build_index
from ai_quality_lab.rag.retrieval import Retriever
from ai_quality_lab.rag.vector_store import VectorIndexError, VectorStore


class FixedEmbedder:
    """Maps known texts to fixed vectors, so rankings are exactly predictable."""

    model_name = "fixed-3"

    def __init__(self, table):
        self.table = table

    def embed_documents(self, texts):
        return [self.table[t.split("\n", 1)[-1]] for t in texts]

    def embed_query(self, text):
        return self.table[text]


def chunk(cid, text, classification="internal"):
    doc_id = cid.split("#")[0]
    return Chunk(cid, doc_id, int(cid.split("#")[1]), text,
                 {"title": doc_id, "classification": classification})


@pytest.fixture
def small_store():
    table = {
        "alpha text": [1.0, 0.0, 0.0],
        "beta text": [0.8, 0.6, 0.0],
        "gamma text": [0.0, 1.0, 0.0],
        "secret text": [0.99, 0.141, 0.0],
        "alpha text copy": [1.0, 0.0, 0.0],
        "q-alpha": [1.0, 0.0, 0.0],
        "q-none": [0.0, 0.0, 1.0],
    }
    embedder = FixedEmbedder(table)
    store = VectorStore(embedder.model_name)
    chunks = [chunk("A#000", "alpha text"), chunk("B#000", "beta text"),
              chunk("C#000", "gamma text"), chunk("R#000", "secret text", "restricted")]
    store.add(chunks, [table[c.text] for c in chunks])
    return store, embedder, table


# --- Embeddings ---------------------------------------------------------------------------------


def test_hashing_embedder_is_deterministic_normalized_and_lexical():
    embedder = HashingEmbedder()
    a, b = embedder.embed_documents(["Password policy rules", "Password policy rules"])
    assert a == b and len(a) == 512
    assert math.isclose(sum(v * v for v in a), 1.0, rel_tol=1e-9)
    query = embedder.embed_query("password rules")
    related = sum(x * y for x, y in zip(query, a))
    unrelated = sum(x * y for x, y in zip(query, embedder.embed_query("parking garage")))
    assert related > unrelated


def test_embedder_configuration_from_env():
    assert isinstance(embedder_from_env({"AIQL_EMBED_PROVIDER": "hashing"}), HashingEmbedder)
    ollama = embedder_from_env({"AIQL_EMBED_MODEL": "mxbai-embed-large",
                                "AIQL_EMBED_BASE_URL": "http://h:1/v1"})
    assert (ollama.model, ollama.base_url) == ("mxbai-embed-large", "http://h:1")
    assert embedder_from_env({}).model == "nomic-embed-text"
    with pytest.raises(LLMConfigurationError):
        embedder_from_env({"AIQL_EMBED_PROVIDER": "cloud"})


def test_unavailable_ollama_embedding_service_is_reported():
    embedder = OllamaEmbedder(base_url="http://127.0.0.1:9", timeout_seconds=3)
    with pytest.raises(LLMUnavailableError, match="Is Ollama running"):
        embedder.embed_query("hello")


# --- Vector store -------------------------------------------------------------------------------


def test_store_ranks_by_cosine_similarity(small_store):
    store, _, table = small_store
    hits = store.search(table["q-alpha"])
    assert [h.chunk.id for h in hits][:2] == ["A#000", "R#000"]
    assert hits[0].score == 1.0


def test_store_rejects_dimension_mismatch_and_duplicates(small_store):
    store, *_ = small_store
    with pytest.raises(ValueError, match="dimension"):
        store.add([chunk("D#000", "x")], [[1.0, 0.0]])
    with pytest.raises(ValueError, match="Duplicate chunk id"):
        store.add([chunk("A#000", "x")], [[1.0, 0.0, 0.0]])
    with pytest.raises(ValueError, match="does not match index dimension"):
        store.search([1.0, 0.0])


def test_store_round_trips_through_json(small_store, tmp_path):
    store, *_ = small_store
    path = tmp_path / "index.json"
    store.save(path)
    loaded = VectorStore.load(path)
    assert loaded.chunks == store.chunks and loaded.dimension == 3


@pytest.mark.parametrize("content", [None, "{not json", json.dumps({"format_version": 99})])
def test_missing_or_corrupt_index_is_reported(tmp_path, content):
    path = tmp_path / "index.json"
    if content is not None:
        path.write_text(content, encoding="utf-8")
    with pytest.raises(VectorIndexError):
        VectorStore.load(path)


def test_index_rebuilds_when_documents_or_chunking_change(repo_documents, tmp_path):
    path = tmp_path / "idx.json"
    embedder = HashingEmbedder()
    first = load_or_build_index(repo_documents, embedder, ChunkingConfig(40, 10), path)
    reused = load_or_build_index(repo_documents, embedder, ChunkingConfig(40, 10), path)
    assert len(reused) == len(first)
    rebuilt = load_or_build_index(repo_documents, embedder, ChunkingConfig(25, 5), path)
    assert rebuilt.info["chunk_size"] == 25 and len(rebuilt) > len(first)


# --- Retrieval ----------------------------------------------------------------------------------


def test_retrieval_returns_ranked_scored_provenance(small_store):
    store, embedder, _ = small_store
    result = Retriever(store, embedder, top_k=2).retrieve("q-alpha")
    assert [(c.rank, c.chunk_id, c.doc_id) for c in result.chunks] == [
        (1, "A#000", "A"), (2, "B#000", "B")
    ]
    assert result.chunks[0].score >= result.chunks[1].score
    assert result.chunks[0].metadata["title"] == "A"


def test_restricted_documents_are_never_retrieved(small_store):
    store, embedder, _ = small_store
    result = Retriever(store, embedder, top_k=3).retrieve("q-alpha")
    assert "R" not in result.doc_ids
    assert result.excluded_by_classification == ("R#000",)


def test_configurable_top_k(small_store):
    store, embedder, _ = small_store
    retriever = Retriever(store, embedder, top_k=1)
    assert len(retriever.retrieve("q-alpha").chunks) == 1
    assert len(retriever.retrieve("q-alpha", top_k=3).chunks) == 3
    with pytest.raises(ValueError):
        Retriever(store, embedder, top_k=0)


def test_min_score_threshold_can_produce_no_results(small_store):
    store, embedder, _ = small_store
    result = Retriever(store, embedder, min_score=0.5).retrieve("q-none")
    assert result.chunks == () and result.doc_ids == ()
    assert set(result.excluded_below_min_score) == {"A#000", "B#000", "C#000"}


def test_exact_duplicate_chunks_are_removed(small_store):
    store, embedder, table = small_store
    # Same text as A#000 after whitespace/case normalization, identical vector.
    store.add([chunk("D#000", "Alpha   TEXT")], [table["alpha text"]])
    result = Retriever(store, embedder, top_k=3).retrieve("q-alpha")
    assert "D#000" in result.duplicates_removed
    assert result.chunk_ids.count("A#000") == 1


def test_retriever_refuses_mismatched_embedder(small_store):
    store, *_ = small_store
    with pytest.raises(ValueError, match="Rebuild the index"):
        Retriever(store, HashingEmbedder())


@pytest.fixture(scope="module")
def corpus_retriever(repo_documents):
    embedder = HashingEmbedder()
    return Retriever(build_index(repo_documents, embedder), embedder, top_k=4)


def test_corpus_relevant_document_ranks_first(corpus_retriever):
    result = corpus_retriever.retrieve("What are the support response times for a P1 ticket?")
    assert result.doc_ids[0] == "SUP-001"


def test_corpus_multiple_relevant_documents_retrieved(corpus_retriever):
    result = corpus_retriever.retrieve("How long must passwords be?")
    assert {"SEC-001", "SEC-001-V1"} <= set(result.doc_ids)


def test_corpus_malicious_document_can_be_retrieved_but_never_restricted(corpus_retriever):
    result = corpus_retriever.retrieve("How do I reset a forgotten password?")
    assert result.doc_ids[0] == "MAL-004"  # highly relevant, contains an injection
    recovery = corpus_retriever.retrieve("What is the emergency break-glass recovery code?")
    assert "RES-001" not in recovery.doc_ids
    assert any(cid.startswith("RES-001") for cid in recovery.excluded_by_classification)


def test_degraded_retrieval_switch_reverses_ranking(corpus_retriever):
    good = corpus_retriever.retrieve("P1 ticket response time")
    bad = corpus_retriever.retrieve("P1 ticket response time", worst_first=True)
    assert good.doc_ids[0] == "SUP-001" and "SUP-001" not in bad.doc_ids
