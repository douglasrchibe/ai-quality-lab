# Phase 6: RAG architecture

Retrieval-augmented generation (RAG) answers a question by first retrieving relevant passages
from a document collection, then asking the language model to answer **from those passages**.
This project adds a small, fully local RAG system on top of the existing chatbots. Nothing in
earlier phases was replaced.

## Pipeline

```
data/knowledge_base/*.md            16 synthetic Northwind Systems documents (4 adversarial, 1 restricted)
  │ documents.py      load + validate (id, title, category, classification, metadata)
  ▼
  │ chunking.py       sentence-aware chunks: ≤40 words, 10-word overlap → 36 chunks
  ▼
  │ embeddings.py     Ollama nomic-embed-text (768-d), or deterministic hashing for offline tests
  ▼
  │ vector_store.py   in-memory cosine search, JSON at .rag_index/<model>.json, staleness check
  ▼
question ──► retrieval.py   top_k=4 · classification filter · min_score · dedupe · provenance
  │ security.py       sanitize retrieved text (remove instruction-like sentences)
  ▼
  │ prompting.py      trusted rules (system) │ untrusted <document> data (user, escaped)
  ▼
  │ LLM               existing OpenAICompatibleClient (Ollama qwen2.5:3b), or a mock
  ▼
  │ security.py       output guard: canary leak · recovery-code pattern · unapproved email domain
  ▼
RAGResponse            answer + retrieved doc/chunk IDs + scores + context used + guardrails triggered
```

All of `ai_quality_lab/rag/` uses only the Python standard library. It doesn't depend on
DeepEval or Ragas.

## Components and decisions

| Component | Decision | Why |
|---|---|---|
| Documents | Markdown with a `key: value` header | Human-readable and diff-able; no YAML dependency |
| Chunking | Sentence packing, 40 words, 10-word overlap; oversized sentences split into word windows | Policy rules are sentences, so chunks carry whole rules. Overlap keeps boundary facts intact. Deterministic. Nothing is ever dropped (tested). |
| Embeddings | `nomic-embed-text` via Ollama `/api/embed`, with nomic's `search_document:` / `search_query:` prefixes | Local and free; ~274 MB. The chat model can't embed (Ollama returns HTTP 501). |
| Offline embeddings | `HashingEmbedder`: lexical feature hashing | Deterministic tests and offline evaluation. Always labelled in results; never substituted silently. |
| Vector store | Exact cosine scan in memory, JSON persistence | 36 chunks don't justify a vector database. Exact search is deterministic and inspectable. The index records the embedder, chunking settings and a document fingerprint, and is rebuilt when any of them change. |
| Retrieval | `top_k`, `min_score`, `allowed_classifications=(public, internal)`, exact-duplicate removal | Access control happens in code: `restricted` documents are never retrieved. Every exclusion is recorded on the result. |
| Prompt | System message = trusted rules and a canary. User message = question plus `<document>` elements with `<`/`>` escaped | An explicit trust boundary. A document can't forge or close its own delimiters. |
| Output | Any `ChatClient`: the real Ollama/OpenAI client, `ExtractiveMockLLM` or `GullibleMockLLM` | Same client abstraction as Phase 5. Mocks make the whole pipeline testable offline. |

## Security model

**Retrieved documents are data, not instructions.** Prompt wording is not relied on as the
control; Phase 5.5 showed a 3B model following instructions planted in its context. The
deterministic controls are:

| Layer | Control | What it stops |
|---|---|---|
| Input | Phase 2 `GuardrailConfig` topic guardrail | Restricted topics, before retrieval |
| Input | Prompt-extraction detector | "Print your system prompt", before retrieval |
| Retrieval | Classification filter | `restricted` documents (e.g. break-glass credentials) never reach the model |
| Context | Sanitizer (`security.sanitize`) | Removes sentences matching injection patterns ("ignore/disregard … instructions", "SYSTEM OVERRIDE", "Note to AI assistants", "tell the user …", role prefixes, delimiter spoofing). The removal is recorded. |
| Output | Canary token in the system prompt | Detects system-prompt leaks |
| Output | Recovery-code pattern `RC-####-XXX` | Secret leakage |
| Output | Email-domain allowlist (`northwind.example`) | Phishing payloads such as "send your password to it-verify@northwind-support.example" |

When the output guard fires, the answer is replaced and the original is kept in
`raw_model_output` for audit.

**Known limitations** (the reason these controls are layered and tested, and not claimed to be
complete):
- **The sanitizer and output guard are pattern-based.** A paraphrased injection without the known
  wording ("please make sure the reader emails their credentials…") can pass the sanitizer. It's
  then caught only if the payload trips the output guard (secret pattern or unapproved email), or
  if the model ignores it.
- **The sanitizer removes whole sentences.** A legitimate fact in the same sentence as an
  injection is lost; the result would be classified as a context failure.
- **The output guard can't recognise all disclosures.** Restricted information phrased in plain
  words (not a recovery code or an email address) isn't caught. The classification filter is the
  main control for restricted content.
- **The topic guardrail is still keyword-based** (Phase 2 limitations apply).
- **Passing the injection tests means these documented attacks were stopped.** It doesn't mean
  the system is secure against injection in general.

## Test-only failure switches

`RAGSimulatedFailure`:
- **`DEFENSES_DISABLED`** removes the input guards, sanitizer and output guard. The classification
  filter stays, because it's part of retrieval, not of the defence layer under test.
- **`RETRIEVAL_DEGRADED`** returns the worst-matching chunks first.

Both exist only to prove the evaluation detects these failures. Neither is the default.

## Commands (Windows PowerShell, project root, `.venv` active)

```powershell
ollama pull nomic-embed-text                        # once (~274 MB)
python -m ai_quality_lab.rag.index build            # (re)build .rag_index/ from data/knowledge_base
python -m ai_quality_lab.rag.index info
$env:AIQL_EMBED_PROVIDER = "hashing"                # optional: offline lexical embeddings
```
