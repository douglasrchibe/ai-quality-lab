"""Live RAG tests against local Ollama (embeddings + chat). Opt-in:

    $env:AIQL_RUN_LIVE_TESTS = "1"; python -m pytest -m live_llm tests/test_rag_live.py
"""

import os

import pytest

from ai_quality_lab.llm_client import LLMSettings, OpenAICompatibleClient, load_env_file
from ai_quality_lab.rag.chatbot import RAGChatbot
from ai_quality_lab.rag.embeddings import OllamaEmbedder, embedder_from_env
from ai_quality_lab.rag.index import build_index
from ai_quality_lab.rag.retrieval import Retriever

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(os.environ.get("AIQL_RUN_LIVE_TESTS") != "1",
                       reason="live LLM tests are opt-in: set AIQL_RUN_LIVE_TESTS=1"),
]


@pytest.fixture(scope="module")
def embedder():
    load_env_file()
    embedder = embedder_from_env()
    if not isinstance(embedder, OllamaEmbedder):
        pytest.skip("AIQL_EMBED_PROVIDER is not ollama")
    return embedder


@pytest.fixture(scope="module")
def retriever(embedder, repo_documents):
    return Retriever(build_index(repo_documents, embedder), embedder, top_k=4)


def test_embeddings_have_consistent_dimensions(embedder):
    vectors = embedder.embed_documents(["Password policy", "Parking garage"])
    query = embedder.embed_query("How long must passwords be?")
    assert len({len(v) for v in vectors + [query]}) == 1 and len(query) > 100


def test_semantic_retrieval_finds_the_relevant_policy(retriever):
    result = retriever.retrieve("How quickly do we respond to an outage ticket?")
    assert "SUP-001" in result.doc_ids
    assert "RES-001" not in retriever.retrieve("break-glass admin recovery code").doc_ids


def test_real_model_answers_from_retrieved_context(retriever):
    settings = LLMSettings.from_env()
    bot = RAGChatbot(retriever, OpenAICompatibleClient(settings))
    response = bot.respond("What is the first-response time for a P1 support ticket?")
    assert "1 hour" in response.response.lower() or "one hour" in response.response.lower()
    assert response.retrieved_doc_ids and not response.blocked
