# CLAUDE.md — AI Quality Lab

Instructions for Claude Code working in this repository.

## Project objective

Build an automated evaluation framework for a **mock** AI chatbot that simulates configurable
topic guardrails (modelled on an LLM Guard + CMS-config setup), and (Phase 5) for a real LLM
behind the same guardrail. The framework will eventually
evaluate responses, detect regressions, measure quality, generate reports, and enforce CI/CD
quality gates. This is a learning/portfolio project for AI-evaluation QA automation.

## Technology choices

- **Python 3.12+**, standard library first.
- **pytest** is the only Python dev dependency. There are no runtime dependencies.
- **DeepEval** (`deepeval==4.3.2`) and the `ollama` client are an **optional** `llm` extra
  (`requirements-llm.txt`). The LLM chat client itself is standard-library only. Code that
  imports `deepeval` must import it lazily (inside functions) after
  `prepare_deepeval_environment()`, so the base install and offline tests never need it.
- **Promptfoo** (Node.js, pinned exactly in `package.json`, lockfile committed) is the only
  Node dependency. Install with `npm ci`; run only through the npm scripts / `promptfoo/run.mjs`.
- **src layout** (`src/ai_quality_lab/`), installed editable via `pip install -r requirements.txt`.
- **pyproject.toml** is the single source of truth for metadata, dependencies, and pytest config.
  `requirements.txt` only contains `-e .[dev]`.
- Eval cases are **data** (`evals/*.json`), kept separate from test code.
- Approved in Phase 5: a real LLM (local Ollama by default; OpenAI-compatible hosted optional)
  and DeepEval LLM-as-a-judge metrics.
- Approved in Phase 6: a local RAG pipeline (stdlib only), Ollama `nomic-embed-text` embeddings,
  and Ragas 0.4.3 in a SEPARATE venv (`.venv-ragas`, `requirements-ragas.txt`). Ragas must never
  be installed into `.venv` (instructor pins jiter<0.15; openai 3.x needs >=0.16).
- Out of scope unless explicitly approved: making paid API calls on the user's behalf,
  databases, hosted vector stores, Docker, cloud services, custom web UIs, autonomous agents,
  deployment pipelines, dashboards.

## Layout

| Path | Purpose |
|---|---|
| `src/ai_quality_lab/mock_agent.py` | `MockChatbot`, `ChatResponse`, `SimulatedFailure` (Phase 2) |
| `src/ai_quality_lab/guardrails.py` | Config validation (`GuardrailConfig`) and `KeywordTopicDetector` |
| `src/ai_quality_lab/dataset.py` | Eval dataset schema, loading, validation (`EvalCase`, `Severity`, `Category`) |
| `src/ai_quality_lab/evaluator.py` | Deterministic assertions, `CaseResult`, `summarize()`, release readiness |
| `src/ai_quality_lab/runner.py` | `Agent` protocol, `run_evaluation_suite()`, CLI (`python -m ai_quality_lab.runner`) |
| `src/ai_quality_lab/promptfoo_adapter.py` | Promptfoo Python provider (`call_api`) and dataset-to-Promptfoo test conversion |
| `src/ai_quality_lab/engine_comparison.py` | Compares a Promptfoo results file with the custom engine, case by case |
| `src/ai_quality_lab/llm_client.py` | Env-based LLM settings, `.env` loader, stdlib OpenAI-compatible client, typed errors |
| `src/ai_quality_lab/llm_agent.py` | `GuardedLLMChatbot` (keyword guardrail + model), CLI for one question |
| `src/ai_quality_lab/llm_dataset.py` | Schema/validation for `evals/llm_cases.json` |
| `src/ai_quality_lab/llm_eval.py` | DeepEval runner: judge, metrics, checks, summary, CLI |
| `src/ai_quality_lab/rag/` | Phase 6 RAG: documents, chunking, embeddings, vector_store, index, retrieval, security, prompting, chatbot, mock_llm, dataset, retrieval_metrics, failures, evaluate, ragas_eval |
| `data/knowledge_base/` | 16 synthetic documents (MAL-* adversarial, RES-001 restricted) |
| `evals/rag_cases.json` | RAG ground truth (reference context, relevant doc IDs, facts) |
| `src/ai_quality_lab/ci_check.py` / `ci_summary.py` | CI check wrapper (exit code -> PASS/BLOCKED/ERROR) and skip audit, job summary, final gate |
| `.github/workflows/` | `ci.yml` (deterministic quality gates), `ragas-environment.yml` (path-filtered) |
| `constraints/` | CI lock files (primary and Ragas environments), generated from CI `pip freeze` |
| `ci/known_defects.json` | Open defects that only live runs can detect; listed in every CI summary |
| `src/ai_quality_lab/reporting.py` | Markdown quality reports (CI job summary, artifacts, local report) |
| `src/ai_quality_lab/observability/` | Phase 8: records, history (JSONL), regression rules/detection, baselines, CLI |
| `ci/regression_rules.json` | Regression directions, tolerances, actions |
| `reports/baselines/` | Approved baselines (committed): manifest, audit log, immutable records |
| `docs/quality-observability.md` | Phase 8 data model, metrics, rules, baseline approval, limitations |
| `tests/` | pytest tests, one file per module + `test_setup.py`; shared fixtures in `conftest.py` |
| `evals/guardrail_cases.json` | Evaluation dataset shared by both engines (schema in `dataset.py` and README) |
| `promptfoo/` | Promptfoo configs (normal + failure demo), thin Python shims, `assertions.js`, `run.mjs` launcher |
| `docs/promptfoo-comparison.md` | Custom engine vs. Promptfoo comparison and run results |
| `evals/llm_cases.json` | Real-LLM cases (behavioural expectations, DeepEval metrics, robust checks) |
| `docs/llm-evaluation.md` | Metric choices, layers, cost, and limitations of the LLM evaluation |
| `.env.example` | Documented settings; copy to git-ignored `.env`. Never commit secrets |
| `reports/` | Generated output, git-ignored except `.gitkeep` and `reports/baselines/` |

## Coding standards

- Type hints on all public functions; prefer `dataclass`/`enum` over loose dicts and magic strings.
- Small, pure functions. **Deterministic behaviour**: no randomness, clock, or network
  dependence in the agent or evaluator unless injected and controllable.
- Docstrings on modules and public APIs; comments explain *why*, not *what*.
- No speculative abstractions. Build what the current milestone needs.

## Testing expectations

- Every behaviour change ships with tests. Run with `python -m pytest` from the activated venv.
- Tests must be deterministic, isolated, and fast (no network, no shared state, `tmp_path` for files).
- Use parametrization for case tables; use the registered markers only (`--strict-markers`).
- **Report actual results.** Never claim a test passed unless it was executed in this session and
  the output showed it passing. If tests were skipped or not run, say so explicitly.
- A deliberately injected guardrail failure must be *detected* by the tests/evaluator. This is how we
  prove the framework can catch regressions.

## Security and privacy

- Never commit or print secrets, API keys, tokens, or environment variable values.
- `.env` files are git-ignored; document required variables in `.env.example` (no real values).
- Eval data and reports must contain synthetic data only, never real user conversations or PII.

## Dependency management

- Do not add a dependency without first explaining why the standard library is insufficient and
  getting approval.
- Declare dependencies in `pyproject.toml` only, with a lower bound (e.g. `pytest>=8.3`).
- CI installs through `constraints/ci-primary.txt` / `constraints/ragas.txt` (Linux lock files).
  Regenerate them from a CI `pip freeze` artifact (docs/ci-pipeline.md); never hand-edit them.
- Never modify the global Python installation or system-wide settings (e.g. execution policy).

## Workflow

1. Inspect the current state before changing anything.
2. Present a short plan; explain significant design decisions and trade-offs.
3. Make the smallest change that satisfies the current milestone.
4. Run the relevant tests and report real output.
5. Summarise files changed, commands run, results, and open issues.
6. Do **not** commit, reset, overwrite, or discard changes without explicit user approval.

## Milestones and definition of done

A milestone is done only when **all** of the following are true:
- Acceptance criteria for that milestone are met and covered by tests.
- `python -m pytest` passes in a clean venv (actually executed).
- README "Current status" is updated; planned features remain labelled as planned.
- No out-of-scope functionality has been added.

| Phase | Scope | Status |
|---|---|---|
| 1 | Environment, structure, docs, setup test | Done |
| 2 | Mock chatbot with configurable guardrails | Done |
| 3 | Evaluation engine: dataset, runner, assertions, summary, release readiness | Done |
| 4 | Promptfoo integration on the shared dataset + engine comparison | Done |
| 5 | Real LLM + DeepEval evaluation | Done with conditions (Phase 5.5 audit): live-verified on 14 of 27 cases (+ LLM-015 in fail-open mode) + judge calibration; full run not completed |
| 6 | RAG + deterministic retrieval evaluation + advisory Ragas | Done (see reports/phase_6_completion_report.md) |
| 7 | GitHub Actions CI with deterministic quality gates | Done (see reports/phase_7_completion_report.md) |
| 8 | Quality observability: history, trends, regression detection, approved baselines | Implemented and validated locally; GitHub run pending (see Phase 8 report) |

## Evaluation engine rules (Phase 3)

- Dependency direction: `runner` -> `evaluator` -> `dataset`. `mock_agent` must never import
  evaluation code. The evaluator judges only `ObservedReply` (`response`, `blocked`,
  `detected_topics`), never agent internals.
- Expected values in `evals/` must match the chatbot's **actual** contract. Known detector gaps are
  characterized with `known-limitation` tags, not hidden. Never edit expectations just to turn a
  run green; a changed expectation needs a stated reason.
- Severity lives in the dataset. Release readiness is BLOCKED on any critical FAIL, any ERROR, or
  zero cases (illustrative policy). Pass rate = passed / all executed cases.
- `tests/test_runner.py` requires that the full dataset passes against the healthy chatbot and that
  `GUARDRAIL_FAIL_OPEN` fails exactly the cases expecting `blocked: true`.

## Phase 2 acceptance criteria: mock chatbot

**Configurable topic guardrails**
- AC1: The agent is constructed with an explicit config
  (`{"guardrails": {"<topic>": {"enabled": bool}}}`), validated into `GuardrailConfig` at startup.
  `enabled: true` blocks the topic. Invalid config raises `InvalidGuardrailConfigError` (fail closed).
- AC2: At least one topic (`cooking`) is supported. Topic detection is rule-based (keywords) and
  case-insensitive, lives in `guardrails.py` separately from the chatbot, and defines each topic's
  keyword list in one place.

**Allowed vs blocked behaviour**
- AC3: With `cooking` **enabled**, a cooking request returns a refusal response.
- AC4: With `cooking` **disabled**, the same request returns a normal (non-refusal) answer.
- AC5: Responses are a frozen `ChatResponse` (`message`, `response`, `blocked`, `detected_topics`,
  `reason`, `outcome`), so evaluators assert on fields, not on fragile string matching.
  Refusal text is a single defined constant/template.

**Unrelated questions**
- AC6: Non-cooking questions get a fixed answer or neutral fallback, with `blocked=False` and no
  cooking topic detected, regardless of whether the cooking guardrail is enabled or disabled.
- AC7: Edge inputs (empty string, whitespace, mixed case, punctuation) are handled without exceptions
  and with defined, documented behaviour.

**Deliberate guardrail failure**
- AC8: An explicit, opt-in failure mode (`SimulatedFailure.GUARDRAIL_FAIL_OPEN`, off by default) makes
  the agent answer a request that *should* be blocked, as if the guardrail had silently failed.
- AC9: The failure mode is visible only through behaviour (the response looks like a normal answer).
  It does not set a flag in the response that would make detection trivial.
- AC10: A test proves that when the failure mode is on, an expected-block assertion fails. This
  demonstrates that the test suite can detect the regression.

**Deterministic behaviour**
- AC11: The same config + input always produces an identical response (no randomness, time, or I/O).
- AC12: The agent is a pure in-process Python object. No network, LLM, or external service is involved.

**Unit tests**
- AC13: `tests/test_mock_agent.py` covers AC3–AC11 using `pytest.mark.parametrize` tables for
  blocked, allowed, and unrelated prompts across both config states.
- AC14: All tests pass via `python -m pytest`. The existing setup tests still pass.
- Out of scope for Phase 2: evaluator, runner, reporting, eval JSON population.

## Promptfoo integration rules (Phase 4)

- `evals/guardrail_cases.json` is the **single source of truth**. Never hand-write Promptfoo tests
  that duplicate it; `promptfoo/tests.py` generates them. New scenarios go in the JSON.
- The Promptfoo provider must call the real `MockChatbot`; never reimplement chatbot logic in
  Python shims or JavaScript. Keep `promptfoo/*.py` as thin shims; logic and tests live in
  `src/ai_quality_lab/promptfoo_adapter.py`.
- Every Promptfoo run goes through `promptfoo/run.mjs` (telemetry/update/sharing disabled,
  venv Python) with `--no-cache --no-share`. No LLM providers in Promptfoo configs.
- The failure demo (`promptfooconfig.failure-demo.yaml`) is the only place `simulated_failure` may
  appear in Promptfoo config; it is expected to exit 100 and must never run in routine checks.
- After changing the dataset, adapter, or assertions, run `npm run eval:promptfoo`, the failure
  demo, and `python -m ai_quality_lab.engine_comparison` on both results files; both must report
  0 disagreements.

## Real-LLM and DeepEval rules (Phase 5)

- `MockChatbot` stays the default everywhere; never replace it with the LLM. `GuardedLLMChatbot`
  must keep the same `respond()`/`ChatResponse` contract and run the deterministic guardrail
  before any model call.
- `python -m pytest` must stay offline and free: use fakes (`FakeClient`, `FakeAgent`,
  `FakeMetric`, local HTTP server). Real model calls only in `tests/test_llm_live.py`, marked
  `live_llm` and skipped unless `AIQL_RUN_LIVE_TESTS=1`.
- Never run hosted-provider (paid) evaluations without the user's explicit go-ahead. Never print
  or log API keys; `LLMSettings.api_key` is excluded from repr.
- LLM expectations describe behaviour; avoid exact-string checks on generated text except for
  robust facts, word limits, and leakage strings. A judge/metric failure to compute is ERROR,
  never PASS.
- DeepEval 4.x: every metric passes when `score >= threshold` (Hallucination/Toxicity/Bias were
  inverted in older versions). Use `SingleTurnParams` (not `LLMTestCaseParams`) and `OpenAIModel`
  (not the deprecated `GPTModel`).
- Report live-run results only from runs actually executed, with the model and judge used.
- Critical safety cases must have a deterministic backstop (e.g. `response_not_contains` with
  leaked-content indicators); a judge score alone never decides a critical verdict
  (enforced by `test_critical_bypass_cases_have_a_deterministic_backstop`).
- Treat judge metrics as advisory until the configured judge passes calibration:
  `$env:AIQL_RUN_LIVE_TESTS="1"; python -m pytest -m live_llm -k calibration`. With the default
  3B self-judge, Answer Relevancy / Contextual Relevancy / Faithfulness are NOT calibrated.

## RAG rules (Phase 6)

- `ai_quality_lab/rag/` must stay standard-library only and must not import DeepEval or Ragas.
  `ragas_eval.py` imports Ragas lazily and runs only in `.venv-ragas`; it reads the JSON written by
  `rag.evaluate --mode rag`.
- Retrieved documents are untrusted data. Keep the deterministic controls (classification filter,
  sanitizer, output guard) in code; never rely on prompt wording alone, and never claim the
  pattern-based controls stop every injection.
- Ground truth = `reference_context` / `relevant_document_ids` / `expected_facts`. Retrieved context
  is never ground truth. New reference passages must be verbatim sentences from the cited document
  (`validate_against_corpus` enforces it).
- Release gates are deterministic: critical FAIL, ERROR, retrieval gates (doc Hit Rate@K >= 0.9,
  Recall@K >= 0.8), restricted retrieval. Ragas is ADVISORY and never changes readiness; do not
  combine Ragas with deterministic metrics into one score. Do not tune gate thresholds to results.
- Failure classes must cite deterministic evidence; unexplained failures are `evaluation_failure`.
- Offline tests use `HashingEmbedder` and mock LLMs; live RAG tests are `live_llm` + opt-in.

## CI rules (Phase 7)

- Standard CI is offline and deterministic: never add Ollama, model downloads, API keys, secrets,
  or live-model tests to `ci.yml`. `tests/test_ci_workflow.py` enforces this.
- Never chase a green build: no `continue-on-error`, no lowered thresholds, no edited expected
  results, no new skips. Only `tests/test_llm_live.py` and `tests/test_rag_live.py` may skip (skip
  audit). Fix the cause or record a known defect with evidence.
- Every check runs through `python -m ai_quality_lab.ci_check` with an explicit `--expect` and
  writes to `ci-artifacts/<job>/`; failure demonstrations use `--expect blocked` and must stay so.
- Custom-engine and RAG checks pass `--report <json>`: exit 1 is also a crash, so BLOCKED/PASS must
  agree with the report (`release_readiness`, `errored == 0`, `total > 0`) or the check is ERROR.
- PASS / BLOCKED / ERROR are distinct (exit 0 / 1 / 2). Missing or unreadable job results are
  ERROR, never a pass.
- Keep `ci/known_defects.json` current; the six D-6.1 over-abstention cases stay listed until a
  live run shows them fixed. CI summaries must not claim the AI system is safe.
- Ragas is validated only in its own environment (`ragas-environment.yml`); never install it into
  the primary CI environment.

## Quality observability rules (Phase 8)

- Records are built from the reports existing tools write; never re-score or invent metrics. Every
  metric keeps numerator/denominator and `kind` (deterministic | advisory). Unmeasured data goes
  in `unmeasured` with a reason, never as 0.
- Runs are compared only when `identity` matches (type, dataset/corpus fingerprints, model,
  embedder). Otherwise ERROR; coverage shrinkage is still flagged. Never combine incompatible runs.
- PASS / BLOCKED / ERROR (0 / 1 / 2). Missing or corrupt history, baselines, rules, or records are
  ERROR, never PASS. Advisory (LLM-judge) metrics can never block (enforced in code).
- Baselines change only via `observability baseline approve` with an approver and reason; never in
  CI (`tests/test_ci_workflow.py` enforces it). Never use `--accept-regression` /
  `--accept-identity-change` to make a run pass; never hand-edit `reports/baselines/`.
- Rule tolerances in `ci/regression_rules.json` change only with a stated reason in
  `docs/quality-observability.md`; never tune them to results.
- Keep `ci/known_defects.json` (D-6.1 to D-6.4) open until a recorded live run shows otherwise.
- Metric names, units (count | rate | score) and kinds are defined once in the metric catalog
  (`records.py`); add new metrics there. A report whose claimed status disagrees with its own
  results is refused, and approval re-checks content independently of `status`.

