# Phase 7: CI pipeline and quality gates

## Principles

1. **Deterministic checks are the foundation.** CI gates only on results that are exactly
   reproducible:
   - offline pytest
   - the custom engine
   - Promptfoo against the mock chatbot
   - RAG retrieval metrics with hashing embeddings
   - RAG safety checks with deterministic mock models
2. **No language model in standard CI.** There's no Ollama, no model download, no API key and no
   secret. GitHub-hosted runners can't reach a local Ollama server, and live outputs aren't
   reproducible.
3. **Judge scores stay advisory.** DeepEval and Ragas judge scores aren't computed in CI and never
   gate a release. The judges failed calibration on real answers (Phase 5.5, Phase 6).
4. **Failures stay visible.**
   - CI never uses `continue-on-error` or "skip until green".
   - The deliberate failure demonstrations are *required* to keep failing.
   - A skip audit stops tests from silently disappearing.
   - Known live-model defects are printed in every run summary.
5. **A green run is evidence, not a guarantee.** It shows the tested scenarios behaved as specified
   under deterministic stand-ins. It doesn't show the AI system is safe or reliable in general.

## Kinds of verification

| Kind | Examples | Runs in CI | Gates release |
|---|---|---|---|
| Unit and regression tests | `tests/test_*.py` (556 offline) | Yes | Yes |
| Deterministic evaluation | Custom engine, Promptfoo (mock), RAG retrieval metrics, RAG safety checks with mock models | Yes | Yes |
| Failure demonstrations | Fail-open guardrail, Promptfoo failure demo, RAG defences disabled, degraded retrieval | Yes, must stay BLOCKED | Yes (CI fails if a demo stops blocking) |
| Dependency validation | `pip check`; Ragas kept out of `.venv`; separate Ragas environment | Yes (Ragas environment: path-filtered workflow) | Yes |
| Live LLM evaluation | `-m live_llm`, `rag.evaluate --llm ollama`, `llm_eval` | **No**: local only | No (results go into reports; defects are tracked in `ci/known_defects.json`) |
| Advisory judge metrics | DeepEval, Ragas | **No** | **Never** |

## Workflows

### `AI Quality Lab CI` (`.github/workflows/ci.yml`)

**Triggers:** pushes and pull requests to `main`, plus manual (`workflow_dispatch`). Concurrency
cancels superseded runs for the same PR or branch.

**Security:**
- `permissions: contents: read`
- no secrets referenced
- no `pull_request_target`

**Runner:** `ubuntu-latest`, Python 3.12, Node 24 (Promptfoo needs Node ≥22.22).

| Job | Installs | Checks (`ci_check` name → expected outcome) |
|---|---|---|
| `python-tests` | `requirements-llm.txt` (project, dev and DeepEval libraries; no models), Node | `pytest` → PASS (JUnit XML); `pytest-skip-audit` → PASS (only `test_llm_live` / `test_rag_live` may skip) |
| `custom-engine` | `pip install -e .` | `custom-engine` → PASS (JSON report); `custom-engine-fail-open-demo` → **BLOCKED**; `llm-dataset-validation` → PASS |
| `promptfoo` | `-e .`, `npm ci` (pinned lockfile) | `promptfoo` → PASS; `promptfoo-failure-demo` → **BLOCKED** (exit 100); `engine-agreement` and `engine-agreement-failure-demo` → PASS (0 disagreements) |
| `rag-deterministic` | `-e .` | `rag-retrieval` → PASS; `rag-mock` (gullible mock model) → PASS; `rag-demo-defenses-disabled` → **BLOCKED**; `rag-demo-retrieval-degraded` → **BLOCKED** |
| `dependencies` | `requirements-llm.txt` | `pip-check` → PASS; `ragas-isolated` → PASS (no `ragas` or `instructor` in the primary environment); `pip freeze` recorded |
| `quality-gate` | `-e .` | `needs` all jobs, runs `if: always()`. Downloads every `ci-*` artifact, writes the job summary, then `ci_summary gate` exits 0/1/2. |

The five quality jobs run in parallel. Every check step after the first uses
`if: ${{ !cancelled() }}`, so all checks in a job run and report even after an earlier one
fails. The job still fails.

### `Ragas environment` (`.github/workflows/ragas-environment.yml`)

Installs `requirements-ragas.txt` in a fresh environment and runs:
- `pip check`
- `import ragas`
- the offline Ragas-runner tests (fake metrics, no model)

It's **path-filtered** (pyproject, requirements-ragas, `ragas_eval.py`, its tests, the workflow
itself) and can be run manually. The trade-off: the Ragas install is about 60 packages and several
minutes, so it isn't run on every PR. A Ragas dependency change is always validated, but an
upstream change that breaks a fresh Ragas install surfaces only on the next path-matched or manual
run.

### No live-LLM workflow

A manually triggered live workflow was **deliberately not built**:
- GitHub-hosted runners can't reach a local Ollama.
- Downloading models into CI would break principle 2 and still wouldn't reproduce local results.
- A self-hosted runner on a public repository needs separate hardening.

Live evaluation stays a documented local activity (README, Phases 5 and 6). Its open defects
(D-6.1 to D-6.3) are listed in `ci/known_defects.json`, so every CI summary shows them.

## Outcomes and exit codes

`python -m ai_quality_lab.ci_check` runs one command, maps its exit code with the tool's own
convention, and compares the result with the expected outcome:

| `--codes` | PASS | BLOCKED (quality criterion failed) | ERROR (could not run) |
|---|---|---|---|
| `project` (custom engine, comparison, RAG, `llm_eval`, skip audit) | 0 | 1 | anything else (2 = setup error) |
| `pytest` | 0 | 1 (test failures) | anything else (usage error, no tests collected …) |
| `promptfoo` | 0 | 100 | anything else |
| `pip` | 0 | non-zero | command could not start |

The wrapper exits **0** if the expected outcome occurred, **1** if a different quality outcome
occurred (for example a demo that stopped blocking), and **2** on ERROR.

It always writes:
- `<name>.status.json`
- `<name>.log`
- optionally the command's stdout (used for JSON reports)

**The final gate (`ci_summary gate`) applies one rule:** PASS only if every job succeeded and every
check met its expectation. Otherwise:

| Outcome | Exit | When |
|---|---|---|
| **BLOCKED** | 1 | Every failure is explained by a check that ran and produced a quality outcome |
| **ERROR** | 2 | Any check errored; any job was cancelled or skipped; any job failed without an unmet check (for example, dependency installation failed); or the job results were missing or unreadable (never treated as "nothing failed") |

## Artifacts

Each job uploads `ci-<job>` with `if: always()`, kept for 14 days. Uploading doesn't change the
failing step's status.

| Artifact | Contents |
|---|---|
| `ci-python-tests` | `junit.xml`, `skip-audit.json`, logs, status files |
| `ci-custom-engine` | `custom-engine-report.json`, `custom-engine-fail-open-report.json`, logs |
| `ci-promptfoo` | `promptfoo-results.json`, `promptfoo-failure-demo-results.json`, `comparison-normal.json`, `comparison-failure-demo.json`, logs |
| `ci-rag-deterministic` | `rag-retrieval.json`, `rag-mock.json`, both demo reports, logs |
| `ci-dependencies` | `pip-freeze.txt`, logs, status files |
| `quality-gate-summary` | `summary.md` (also shown as the run's job summary) |

All reports contain only synthetic data:
- the fictional knowledge base
- the test questions
- deterministic mock answers

No environment values or credentials are written. The summary is built from JSON files and exit
codes, never from console text, and shows "unavailable" for anything it can't read.

## Running the same checks locally (Windows PowerShell, `.venv` active)

```powershell
$env:AIQL_EMBED_PROVIDER = "hashing"
python -m pytest -q --junitxml=ci-artifacts/python-tests/junit.xml
python -m ai_quality_lab.ci_summary skips --junit ci-artifacts/python-tests/junit.xml --out ci-artifacts/python-tests
python -m ai_quality_lab.runner; python -m ai_quality_lab.runner --simulate-failure guardrail_fail_open   # expect 0, then 1
npm run eval:promptfoo; npm run eval:promptfoo:failure-demo                                                # expect 0, then 100
python -m ai_quality_lab.engine_comparison reports/promptfoo/results.json
python -m ai_quality_lab.rag.evaluate --mode retrieval --embedder hashing
python -m ai_quality_lab.rag.evaluate --mode rag --llm mock-gullible --embedder hashing
python -m ai_quality_lab.rag.evaluate --mode rag --llm mock-gullible --embedder hashing --simulate-failure defenses_disabled   # expect 1
python -m ai_quality_lab.rag.evaluate --mode retrieval --embedder hashing --simulate-failure retrieval_degraded               # expect 1
python -m pip check
```

Wrap any of these as CI does to get status files:

```powershell
python -m ai_quality_lab.ci_check --name <name> --codes project --expect pass --out ci-artifacts/<job> -- <command>
```

## Maintaining the pipeline

- **New checks** go through `ci_check` with an explicit `--expect`, and write into
  `ci-artifacts/<job>`. `tests/test_ci_workflow.py` enforces both.
- **Never lower a threshold, edit expected results, add `continue-on-error`, or skip tests to make
  CI pass.** Fix the cause, or record a known defect with evidence.
- **Update `ci/known_defects.json` only from a new live run**, and keep the old evidence in the
  phase reports.
- **Action versions** are pinned to major versions (`@v4`/`@v5`). Pinning to commit SHAs is a
  stricter option if repository policy requires it.
