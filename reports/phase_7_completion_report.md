# Phase 7 Completion Report: CI/CD Automation and Quality Gates

**Date:** 2026-10-30. **Branch:** `main` (primary branch, confirmed from the remote HEAD).
**Baseline commit:** `9e3ce0e` (Phase 6), clean working tree.

**Local environment:**
- Windows 11, Python 3.12.1, Node 24.18.0, npm 11.16.0
- No WSL distribution, Docker or actionlint available

Every number below comes from a command run during Phase 7. Anything not executed is marked
**NOT RUN**.

---

## A. Executive summary

**Outcome: READY WITH CONDITIONS.**

**What was built:**
- A GitHub Actions pipeline that enforces the project's deterministic quality controls with no
  live model, no Ollama and no credentials:
  - offline tests
  - the custom engine
  - Promptfoo and its agreement with the custom engine
  - RAG retrieval and safety gates
  - dependency integrity
- Its key properties:
  - every check maps exit codes to PASS / BLOCKED / ERROR
  - the deliberate failure demonstrations are **required to stay BLOCKED**
  - a skip audit prevents silently lost coverage
  - artifacts upload even after failures
  - a final gate fails explicitly
  - every run summary lists the open known defects, including the six D-6.1 over-abstention cases

**How it was validated:**
- **Locally:** every job's commands were run exactly as written in the workflow, the whole
  workflow was simulated end to end (gate: PASS), and 8 failure scenarios behaved correctly.

**Condition (why not READY):**
- **The workflow hasn't run on GitHub yet.** Nothing was pushed, as you instructed, and no Linux
  environment was available locally. The first remote run is the remaining proof, particularly for
  `npm ci` on Linux (the lockfile was generated on Windows), the Python provider path in Promptfoo
  on Linux, and artifact download.
- **The Phase 6 defects remain open.** They're visible in every CI summary.

---

## B. Baseline (before any change)

| Command | Exit | Result |
|---|---|---|
| `python -m pytest -q` | 0 | 493 passed, 12 skipped |
| `python -m ai_quality_lab.runner` | 0 | READY |
| `python -m ai_quality_lab.runner --simulate-failure guardrail_fail_open` | 1 | BLOCKED |
| `npm run eval:promptfoo` | 0 | 33 passed |
| `python -m ai_quality_lab.engine_comparison reports/promptfoo/results.json` | 0 | 33 agreements, 0 disagreements |
| `npm run eval:promptfoo:failure-demo` | 100 | 17 passed, 16 failed |
| `python -m ai_quality_lab.engine_comparison reports/promptfoo/failure-demo-results.json` | 0 | 0 disagreements |
| `python -m ai_quality_lab.rag.evaluate --mode retrieval --embedder hashing` | 0 | 24/25, READY |
| `python -m ai_quality_lab.rag.evaluate --mode rag --llm mock-gullible --embedder hashing` | 0 | 17/25, READY |
| … `--simulate-failure defenses_disabled` | 1 | BLOCKED |
| … `--mode retrieval --simulate-failure retrieval_degraded` | 1 | BLOCKED |
| `pip check` (`.venv`) / (`.venv-ragas`) | 0 / 0 | No broken requirements |

The baseline matched the documented Phase 6 results. There were no existing workflows
(`.github/` absent).

---

## C. Changes made

### Created

| File | Purpose |
|---|---|
| `.github/workflows/ci.yml` | `AI Quality Lab CI`: 5 parallel quality jobs plus `quality-gate` |
| `.github/workflows/ragas-environment.yml` | Path-filtered or manual validation of the separate Ragas environment |
| `src/ai_quality_lab/ci_check.py` | Runs a check and maps its exit code (per tool convention) to PASS / BLOCKED / ERROR against an expected outcome. Writes a status JSON and log even on failure. |
| `src/ai_quality_lab/ci_summary.py` | Skip audit (JUnit), job summary (from JSON and exit codes only; "unavailable" when unreadable), final gate (0 / 1 / 2) |
| `ci/known_defects.json` | Open defects only live runs can detect (D-6.1 to D-6.3); listed in every summary |
| `docs/ci-pipeline.md` | CI methodology, gates, artifacts, local equivalents, maintenance rules |
| `tests/test_ci_check.py`, `tests/test_ci_summary.py`, `tests/test_ci_workflow.py` | 62 tests: exit-code mapping, gate semantics, summary, and structural checks on the **real** workflow files |
| `reports/phase_7_completion_report.md` | This report |

### Modified

| File | Change | Why |
|---|---|---|
| `src/ai_quality_lab/engine_comparison.py` | Additive `--output` JSON option (`comparison_to_dict`) | The summary reads JSON, not console text. The existing behaviour and exit codes are unchanged. |
| `tests/test_engine_comparison.py` | +1 test for `--output` | Regression coverage |
| `pyproject.toml` | `pyyaml>=6` added to the `dev` extra | Real YAML parsing for workflow-structure tests. `importorskip` would have tripped the skip audit, and text matching would have been fragile. `pip check` stays clean. |
| `.gitignore` | `ci-artifacts/`, `downloaded/` | Local CI outputs are never committed |
| `README.md`, `CLAUDE.md` | Phase 7 sections and CI rules; milestones updated | Phase 6 limitations kept |

No dataset, threshold, gate, guardrail or existing test was changed.

---

## D. CI architecture

- **Triggers:**
  - `push` and `pull_request` to `main`, plus `workflow_dispatch`
  - concurrency cancels superseded runs per PR or ref
- **Security:**
  - `permissions: contents: read`
  - no `secrets.*` referenced
  - no `pull_request_target`
  - telemetry opt-outs set; `AIQL_EMBED_PROVIDER=hashing`
- **Runner:** `ubuntu-latest`, Python 3.12 (`actions/setup-python@v5`, pip cache keyed on
  `pyproject.toml` and `requirements*.txt`), Node 24 (`actions/setup-node@v4`, npm cache, `npm ci`
  from `package-lock.json`).
- **Jobs and checks:**

| Job | Check → expected outcome |
|---|---|
| `python-tests` | pytest (JUnit) → PASS; pytest-skip-audit → PASS |
| `custom-engine` | custom-engine → PASS; custom-engine-fail-open-demo → BLOCKED; llm-dataset-validation → PASS |
| `promptfoo` | promptfoo → PASS; promptfoo-failure-demo → BLOCKED (exit 100); engine-agreement and engine-agreement-failure-demo → PASS |
| `rag-deterministic` | rag-retrieval → PASS; rag-mock → PASS; rag-demo-defenses-disabled → BLOCKED; rag-demo-retrieval-degraded → BLOCKED |
| `dependencies` | pip-check → PASS; ragas-isolated → PASS; pip freeze recorded |
| `quality-gate` | `needs` all 5 jobs, `if: always()`; downloads `ci-*`; writes the job summary; `ci_summary gate` exits 0/1/2 |

- **Failure handling:**
  - Check steps after the first use `if: ${{ !cancelled() }}`, so every check reports.
  - Uploads use `if: always()`.
  - There's no `continue-on-error`.
  - Missing or unreadable job results count as **ERROR**.
- **Python reproducibility:** only DeepEval, ollama, Ragas and Promptfoo are pinned exactly; other
  Python dependencies use lower bounds. **Limitation:** there's no Python lockfile, but
  `pip freeze` is recorded in every run's artifacts. **Smallest improvement:** commit a
  `constraints-ci.txt` generated from a known-good freeze.
- **Optional live testing:** **not implemented as a workflow, by design.** Hosted runners can't
  reach a local Ollama; downloading models violates the offline-CI rule; and a self-hosted runner on
  a public repository needs separate hardening. Live evaluation remains a documented local
  activity, and its defects are surfaced through `ci/known_defects.json`.

---

## E. Validation evidence

| Check | Result |
|---|---|
| Full offline pytest | **556 passed, 12 skipped** (25s). Skip audit: 12 skipped, all 12 allowed live tests, 0 unexpected. |
| Custom engine (via `ci_check`) | PASS (33/33 READY); fail-open demo BLOCKED as expected; LLM dataset validation PASS (27 cases) |
| Promptfoo (via `ci_check`) | PASS (33 passed); failure demo BLOCKED (exit 100) as expected; agreement 33/33 in both modes |
| RAG (via `ci_check`) | retrieval PASS (24/25, hit 0.947, recall 0.921, gates pass); mock RAG PASS (17/25, 0 critical); both demos BLOCKED as expected |
| Security tests | Included in pytest (injection, output guard, restricted retrieval) and in the rag-mock and defences-disabled checks |
| Dependency checks | `.venv`: `pip check` clean, Ragas/instructor absent. `.venv-ragas`: `pip check` clean, `import ragas` 0.4.3, runner tests 18 passed and 1 skipped (that skip is correct in this environment) |
| YAML validation | Both workflows parse with PyYAML. 12 structural tests on the real files: triggers, permissions, no secrets, no `continue-on-error`, offline, actions pinned, timeouts, gate `needs`, per-job artifacts with `if: always()`, every check via `ci_check` with an explicit expectation, demos BLOCKED, referenced modules, npm scripts and files exist, report paths match the scripts' `-o` paths |
| End-to-end workflow simulation (Windows, same commands) | 15/15 checks met their expectations; summary generated; **gate PASS (exit 0)** |
| actionlint | **NOT RUN**: not installed, and I didn't add tooling |
| **Remote GitHub Actions run** | **NOT RUN**: nothing pushed. Linux execution is unverified. |

**Failure-path validation (temporary directories; no project configuration changed):**

| Scenario | Exit | Meaning |
|---|---|---|
| Injected failing test via the pytest check | 1 | BLOCKED |
| Custom engine BLOCKED where PASS is required | 1 | BLOCKED |
| RAG evaluation with a missing dataset | 2 | ERROR (distinct from BLOCKED) |
| A demo that no longer blocks | 1 | BLOCKED (detection loss is caught) |
| Gate: a job failed with a BLOCKED check | 1 | BLOCKED |
| Gate: a job failed before any check (e.g. an install) | 2 | ERROR |
| Gate: a cancelled job | 2 | ERROR |
| Live tests without Ollama configuration | 0 | Skipped; the standard run passes |

Artifact upload after failure isn't locally executable. It's verified structurally
(`if: always()` on every upload; tested).

**Defect found and fixed during validation:**
- **What went wrong:** the first gate implementation turned unreadable `needs` JSON into "no
  results", and still reported **PASS**. The local simulation exposed this, because PowerShell 5.1
  strips quotes from native-command arguments.
- **Why it mattered:** on GitHub, that could have hidden a job that failed before writing a status.
- **Fix:** unreadable or missing job results are now **ERROR**, a `--needs-file` option was added,
  and there are 6 regression tests.

---

## F. Historical comparison

| | Phase 6 | Phase 7 |
|---|---|---|
| Offline pytest | 493 passed, 12 skipped | 556 passed, 12 skipped |
| Custom engine | 33/33 READY | 33/33 READY (now also a CI check) |
| Promptfoo | 33 passed, 0 disagreements | Same |
| RAG retrieval (hashing) | Hit 0.947, recall 0.921 | Same |
| RAG mock end to end | 17/25, 0 critical | Same |
| Live RAG (qwen2.5:3b) | 19/25, 6 over-abstentions | **NOT RUN** in Phase 7 (out of CI scope); still listed as D-6.1 |

**About the test counts:**
- **All 63 new tests cover the CI layer.** No application behaviour changed, so the higher count
  isn't evidence of better AI quality.
- **Counting:** 556 − 493 = 63 = 62 new CI tests + 1 `engine_comparison` test.

---

## G. Remaining risks

**Phase 6 issues, unresolved and visible in every CI summary:**
1. **D-6.1:** over-abstention (6/19 answerable questions with the real model). Not detectable in
   CI.
2. **D-6.2:** local judge scores unreliable; Ragas and DeepEval stay advisory.
3. **D-6.3:** deterministic checks can't detect contradictory-policy muddling (RAG-011).
4. **Pattern-based security:** paraphrased injections can get past the sanitizer; plain-language
   disclosures aren't caught by the output guard.

**New, CI-specific:**

5. **Unverified on Linux and on GitHub.** The lockfile is Windows-generated (npm optional native
   dependencies, e.g. esbuild), and the setup-python interpreter path is used by Promptfoo. This
   resolves only after the first remote run.
6. **No Python lockfile.** An upstream release could break a future run. Mitigation: `pip freeze`
   artifacts, plus the recommended constraints file.
7. **Ragas environment validation is path-filtered.** An upstream break appears only on the next
   matching or manual run.
8. **Mocks don't reproduce live behaviour.** A green CI run says nothing about the real model.
   Mitigation: the summary's limitations and known-defects sections.
9. **Promptfoo transitive `npm audit` advisories** (5 high, since Phase 4) are unchanged. CI doesn't
   run `npm audit` as a gate.
10. **Branch protection isn't configured.** CI informs but doesn't yet enforce merges. That's a
    repository setting: require the `Quality gate + summary` check.

---

## H. Release recommendation

**Safe to rely on, once the first remote run passes:** CI as the merge gate for the
**deterministic** properties:
- tests
- guardrail and engine behaviour
- Promptfoo/engine agreement
- RAG retrieval gates
- RAG injection and restricted-content defences against a worst-case mock model
- dependency integrity
- the requirement that seeded failures stay detectable

**Advisory or out of scope:**
- live-model quality (D-6.1)
- any LLM-judge score (D-6.2)
- semantic correctness beyond the deterministic checks (D-6.3)

**Next steps:**
1. Approve a commit and push, then review the first GitHub run.
2. Mark `Quality gate + summary` as a required status check.
3. Then proceed to the next phase.

---

## I. Git status

**Branch:** `main`, up to date with `origin/main` at `9e3ce0e`. **Nothing committed, pushed or
tagged.**

| Change | Files |
|---|---|
| Modified | `.gitignore`, `CLAUDE.md`, `README.md`, `pyproject.toml`, `src/ai_quality_lab/engine_comparison.py`, `tests/test_engine_comparison.py` |
| New | `.github/workflows/ci.yml`, `.github/workflows/ragas-environment.yml`, `ci/known_defects.json`, `docs/ci-pipeline.md`, `src/ai_quality_lab/ci_check.py`, `src/ai_quality_lab/ci_summary.py`, `tests/test_ci_check.py`, `tests/test_ci_summary.py`, `tests/test_ci_workflow.py`, `reports/phase_7_completion_report.md` |

**Recommended commit message:**

```
Phase 7: GitHub Actions CI with deterministic quality gates

- ci.yml: offline CI (no models, no secrets, read-only permissions) with
  python-tests, custom-engine, promptfoo, rag-deterministic, dependencies,
  and an always-run quality-gate job
- ci_check: exit code -> PASS/BLOCKED/ERROR per tool; failure demos must
  stay BLOCKED; status JSON + logs always written
- ci_summary: skip audit (only live tests may skip), job summary from JSON
  and exit codes, final gate (0/1/2); unreadable job results are ERROR
- ci/known_defects.json: D-6.1..D-6.3 listed in every run summary
- ragas-environment.yml: path-filtered validation of the separate Ragas env
- engine_comparison --output JSON; pyyaml dev dependency for workflow tests
- docs/ci-pipeline.md, README/CLAUDE.md, Phase 7 report

Local validation: 556 passed, 12 live skipped; full workflow simulated
(15/15 checks met expectations, gate PASS); 8 failure paths verified.
Not yet run on GitHub-hosted runners.
```
