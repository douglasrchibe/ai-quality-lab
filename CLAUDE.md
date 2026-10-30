# CLAUDE.md — AI Quality Lab

Instructions for Claude Code working in this repository.

## Project objective

Build an automated evaluation framework for a **mock** AI chatbot that simulates configurable
topic guardrails (modelled on an LLM Guard + CMS-config setup). The framework will eventually
evaluate responses, detect regressions, measure quality, generate reports, and enforce CI/CD
quality gates. This is a learning/portfolio project for AI-evaluation QA automation.

## Technology choices

- **Python 3.12+**, standard library first.
- **pytest** is the only dev dependency. There are no runtime dependencies.
- **src layout** (`src/ai_quality_lab/`), installed editable via `pip install -r requirements.txt`.
- **pyproject.toml** is the single source of truth for metadata, dependencies, and pytest config.
  `requirements.txt` only contains `-e .[dev]`.
- Eval cases are **data** (`evals/*.json`), kept separate from test code.
- Out of scope unless explicitly approved: real LLMs, paid APIs, Promptfoo/DeepEval, databases,
  Docker, cloud services, web UIs, autonomous agents.

## Layout

| Path | Purpose |
|---|---|
| `src/ai_quality_lab/mock_agent.py` | `MockChatbot`, `ChatResponse`, `SimulatedFailure` (Phase 2) |
| `src/ai_quality_lab/guardrails.py` | Config validation (`GuardrailConfig`) and `KeywordTopicDetector` |
| `src/ai_quality_lab/dataset.py` | Eval dataset schema, loading, validation (`EvalCase`, `Severity`, `Category`) |
| `src/ai_quality_lab/evaluator.py` | Deterministic assertions, `CaseResult`, `summarize()`, release readiness |
| `src/ai_quality_lab/runner.py` | `Agent` protocol, `run_evaluation_suite()`, CLI (`python -m ai_quality_lab.runner`) |
| `src/ai_quality_lab/reporting.py` | Placeholder: report files and baselines (Phase 4) |
| `tests/` | pytest tests, one file per module + `test_setup.py`; shared fixtures in `conftest.py` |
| `evals/guardrail_cases.json` | Evaluation dataset (schema documented in `dataset.py` and README) |
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
| 4 | Report files (JSON/Markdown) + regression comparison vs baseline | Planned |
| 5 | CI pipeline with quality gates | Planned |

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
