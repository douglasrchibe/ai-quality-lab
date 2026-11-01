# Phase 7 Pre-Commit Review: CI/CD Quality Gates

**Date:** 2026-11-01.
**Reviewed:** the full Phase 7 change set, `9e3ce0e..97502a4`. That's 18 files:
- workflows
- `ci_check`, `ci_summary`
- known defects, lock files
- tests and docs

**Fixes:** `15dc532`, with the CI run result recorded in `2d28111`.

**About the timing:** the review was requested as a pre-commit review, but it ran after Phase 7 had
been committed and pushed. So the defects found here were fixed in a follow-up commit, not before
the first commit.

**Method:**
- read the implementation, not the completion report
- ran deliberately bad inputs through the CI tools
- compared behaviour with the first two GitHub Actions runs

Every finding below was reproduced, and every fix ships with a regression test built from the
input that exposed it.

---

## 1. Verdict

**No path was found for the overall quality gate to report a false PASS.**

The review found three defects in how outcomes were classified or reported. All three are fixed
and verified on GitHub Actions. One of them (F1) could, in a narrow case, have made a failure
demonstration look successful **at the check level**. The pytest suite was the only backstop.

| Severity | Count | Status |
|---|---|---|
| Medium | 2 (F1, F2) | Fixed |
| Low to medium | 1 (F3) | Fixed |
| Low (hardening) | 2 (F4) | Fixed |
| Low (accepted risks) | 4 (section 5) | Documented, not changed |

---

## 2. Findings

### F1 (medium): a crashing failure demonstration counted as "BLOCKED as expected"

**What was wrong:**
- Project tools exit 1 for BLOCKED, but Python also exits 1 on an unhandled exception.
- A run in which every case errored is also BLOCKED (exit 1), because the release policy blocks on
  any ERROR.
- The demo checks used exit codes only, so they couldn't tell "the seeded failure was detected"
  from "the tool broke".

**Reproduction:**

```
ci_check --codes project --expect blocked -- python -c "raise RuntimeError('simulated crash')"
→ wrapper exit 0 (expectation met)
```

**Why it wasn't a false PASS in practice:**
- pytest re-runs every demo with strict assertions, in the same CI run:
  - The fail-open demo requires `errored == 0`, and the failed cases must be exactly the
    expected-block cases.
  - The RAG demos require RAG-016 to RAG-019 to be critical failures and the recall gate to fail.
- The Promptfoo demo is also cross-checked by the engine-agreement check.
- The remaining gap was a crash that happens only in a CI job's smaller (`-e .`) install.

**Fix:**
- `ci_check --report <json>`: PASS or BLOCKED stands only if the tool's JSON report:
  - exists and is a JSON object
  - has `total > 0`
  - has `errored == 0`
  - has a `release_readiness` that matches the outcome (READY or BLOCKED)
- Otherwise the outcome is **ERROR**, and the reason is written to the status file
  (`report_problem`) and shown in the gate output.
- All 6 custom-engine and RAG checks use it. `--report` is rejected with any `--codes` other than
  `project`.

**Tests:**
- a crash with no report
- all cases errored
- zero cases
- a report that disagrees with the exit code
- a report that isn't a JSON object
- `--report` used with non-project codes
- a real fail-open demo confirmed by its report
- a workflow test that every engine and RAG check passes `--report`, pointing at the file it writes

### F2 (medium): a malformed artifact crashed the summary, and the gate step was then skipped

**What was wrong:**
- The summary assumed report fields existed.
- A crash in the "Write job summary" step meant that "Enforce quality gate" (default
  `if: success()`) was skipped.
- The job still failed, so this wasn't a false PASS. But the run lost its PASS/BLOCKED/ERROR
  verdict, contradicting the module's own "must never crash" design rule.

**Reproduction:** `skip-audit.json` containing `{"skipped": 0}` → `summary` exit 1, `KeyError: 'tests'`.
The same pattern applied to:
- the custom engine report
- the Promptfoo statistics
- `known_defects.json` entries

**Fix:**
- Every results line, and the known-defects list, falls back to "report unreadable (\<error type\>)".
- The gate step runs with `if: ${{ !cancelled() }}`.

**Tests:**
- malformed pytest, engine and known-defect inputs (the summary must still exit 0)
- a workflow test checking the gate step's condition

### F3 (low to medium): a status file that isn't a JSON object crashed the gate with the BLOCKED exit code

**Reproduction:** a status file containing `[]` → `gate` exit 1 (`TypeError`). An unreadable status
should be ERROR (exit 2).

**Fix:** anything that isn't a JSON object is treated as an unreadable status, which is already
classified as ERROR.

**Test:** `gate` with a `[]` status file exits 2.

### F4 (low): hardening

| Item | Change | Test |
|---|---|---|
| `actions/checkout` left the read-only `GITHUB_TOKEN` in `.git/config` for later steps | `persist-credentials: false` on every checkout, in both workflows | Every checkout step is checked |
| The Ragas workflow's path filter omitted modules imported by `ragas_eval.py` | Added `rag/embeddings.py` and `llm_client.py` | The test derives the module list from `ragas_eval.py`'s actual imports, so the filter can't drift |

---

## 3. Review checklist results

### A. False PASS conditions

| Condition | Result | Evidence |
|---|---|---|
| A failed command interpreted as PASS | Not possible | `classify()` maps only the documented success code (0) to PASS. Unknown codes and "could not start" are ERROR. pytest exit 5 ("no tests collected") is ERROR. |
| Missing, malformed, empty or unreadable status files | ERROR | Unreadable statuses are ERROR. Fixed in F3: valid JSON that isn't an object. |
| Missing, empty or malformed job results (`needs`) | ERROR | `parse_needs` raises, and the gate exits 2. Found and fixed during Phase 7, when PowerShell's quote stripping produced a false PASS locally; regression tests exist. |
| A job that never completed treated as successful | Not possible | Cancelled or skipped jobs, and failed jobs with no unmet check, are ERROR. |
| An expected-BLOCKED demo passing for the wrong reason | **Was possible at check level (F1)**; fixed | The report cross-check; pytest's demo assertions remain as a second layer |
| An unexpected BLOCKED overlooked | Not possible | An unmet expectation fails the step, and so the job and the gate |
| The gate passing when a required job failed, was skipped or was cancelled | Not possible | `overall_outcome` requires every job to be `success`. The gate job uses `needs` for all 5 jobs with `if: always()`. |

### B. GitHub Actions reliability

- **YAML and expressions:** both workflows parse; 21 structural tests run against the real files.
  The first remote run passed.
- **`needs`, `always()`, `!cancelled()`:** check steps after the first one run even after an
  earlier failure. Uploads use `always()`. Verified on GitHub: artifacts were present for every job.
- **Artifacts:** `download-artifact` with `pattern: ci-*` puts each job's artifact in its own folder.
  The gate derives the job name from that folder. Missing artifacts show as "unavailable" and never
  stand in for a job result.
- **Windows to Linux:**
  - `npm ci` worked with the lockfile generated on Windows.
  - Promptfoo used the runner's Python via `PROMPTFOO_PYTHON`.
  - Exit codes propagated (Promptfoo returned 100 on Ubuntu).
  - No PowerShell syntax appears in the workflow scripts.
- **Dependency installation:** every pip install uses a Linux lock file (`constraints/`). On GitHub,
  `pip freeze` matched the lock files exactly.
- **Note:** `cp … || true` in the Promptfoo collection step is intentional. It only copies
  reports, and a missing report shows as "unavailable". It's outside any check command.

### C. Security

- `permissions: contents: read` in both workflows; no job overrides it.
- No `secrets.*` references and no `pull_request_target`. Pull requests from forks get a read-only
  token and no secrets.
- No event data is interpolated into a script. The only `github.event` value used is the numeric
  PR number, in the concurrency group. Job results reach scripts through an environment variable
  (`NEEDS_JSON`).
- Only GitHub-owned `actions/*` are used, pinned to major versions (not commit SHAs; documented).
- Telemetry opt-outs are set (DeepEval, Ragas; Promptfoo through `run.mjs`).
- There are no model calls: `AIQL_EMBED_PROVIDER=hashing`, mock models only, and a test forbids
  `ollama`, `--llm ollama`, `-m live_llm` and `AIQL_RUN_LIVE_TESTS` in CI.

### D. Quality gate integrity

- **Nothing was weakened.** Since Phase 6, the only changes under `src/` are the two new CI modules
  and the additive `--output` option in `engine_comparison`. `evals/`, the Promptfoo configs and the
  thresholds are unchanged.
- **Promptfoo and the custom engine agree on 33/33 cases**, in both normal and failure-demo modes, on
  GitHub.
- **The seeded failure demos block for the right reason.** All 4 stay BLOCKED. The 2 custom-engine
  and RAG demos are now also confirmed by their JSON reports (F1). The Promptfoo demo is confirmed
  by the engine-agreement check.
- **The RAG checks execute.** Retrieval and mock end-to-end both produce reports on GitHub: hit rate
  0.9474, recall 0.9211, 0 critical failures.
- **The known defects stay visible.** D-6.1, D-6.2 and D-6.3 are `open` in `ci/known_defects.json`,
  appear in every run summary, and a test pins the six D-6.1 cases.
- **Advisory tests can't become required.** Live tests run only when the opt-in variable is set,
  and the skip audit allows skips only in `test_llm_live` and `test_rag_live`. Judge scores aren't
  computed in CI.

### E. Dependency and repository hygiene

- `pip check` is clean in both environments, and Ragas and instructor are absent from the primary
  environment (a CI check enforces this).
- Lock files are generated from CI `pip freeze`. Tests require that every install uses them and
  that they agree with the exact pins in `pyproject.toml`.
- The generated folders (`ci-artifacts/` and `downloaded/`) are git-ignored, and the working tree
  is clean.
- One new dev dependency, `pyyaml>=6`, was declared in `pyproject.toml` and justified in the Phase 7
  report.

---

## 4. Verification

| Check | Result |
|---|---|
| `python -m pytest -q` (local) | **577 passed, 12 skipped** (the 12 opt-in live tests). That's 560 before the review plus 17 new regression tests. |
| The 6 report-checked commands, run locally exactly as in the workflow | All met their expectations (3 PASS, 3 BLOCKED) |
| GitHub Actions: [CI 41269262995](https://github.com/douglasrchibe/ai-quality-lab/actions/runs/41269262995) (`15dc532`) | Gate **PASS**, 15/15 checks met, 577 passed, 0 unexpected skips. All 6 report checks confirmed (no `report_problem`). |
| GitHub Actions: [Ragas 41269262990](https://github.com/douglasrchibe/ai-quality-lab/actions/runs/41269262990) | Passed |

---

## 5. Remaining risks (accepted, documented)

1. **Timeout classification.** A job that times out after a check already failed is reported as
   BLOCKED, not ERROR. The job still fails.
2. **Test-count regression.** A shrinking test suite isn't detected. Planned for Phase 8 baselines.
3. **Action pinning.** Actions are pinned to major versions, not commit SHAs. They also target the
   deprecated Node 20 runtime (GitHub currently forces Node 24; a warning, not a failure).
4. **Admin bypass.** `Quality gate + summary` is a required check on `main`, but admins can bypass
   it. Pull requests are gated; the owner's direct pushes aren't.

**Scope reminder:** a green run is evidence for the tested scenarios under deterministic stand-ins.
It says nothing about the live model, whose defects (D-6.1 to D-6.3) remain open.
