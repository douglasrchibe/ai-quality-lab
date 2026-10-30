"""Retrieval-augmented generation (Phase 6).

Pipeline: documents -> chunking -> embeddings -> vector store -> retrieval -> sanitized context ->
prompt (trusted rules + untrusted documents) -> LLM -> output guard -> RAGResponse.

The RAG system uses only the standard library; it does not depend on DeepEval or Ragas.
"""
