# Phase 8.2 Finalization Report: Ready for Human Review and GitHub Validation

**Date:** 2026-11-01. **Branch:** `main`. **HEAD:** `2d28111f15e9709bfc1ca11422d8c7d824a195e5`
(Phase 7). The Phase 7 review, Phase 8, 8.1 and 8.2 work is uncommitted, and nothing is staged.

Nothing was committed, pushed, branched, staged or approved. No baseline was modified.
`--allow-uncommitted` was not used.

**GitHub Actions and Ubuntu validation remain NOT RUN.** Every result below is from local
execution on Windows 11 / Python 3.12.1.

---

## 1. Verdict: READY FOR HUMAN REVIEW

All Phase 8.2 completion criteria are met locally:

| Criterion | Result |
|---|---|
| Audit report renamed and trackable | ✅ |
| Baselines unchanged | ✅ |
| `baseline verify` | ✅ OK |
| Full suite | ✅ 756 passed / 12 skipped |
| Phase 7 tests | ✅ 88 passed |
| Local CI simulation | ✅ 19/19 checks met their expectations; gate PASS |
| Regression demos | ✅ BLOCKED |
| Unrelated changes introduced | None |

The project isn't yet **released** as Phase 8 complete. That needs three steps that only you can
take (section 8):
1. the first GitHub Actions run
2. a refresh of the pytest baseline. It's a **hard condition**: a test that silently starts
   skipping isn't detected against the current legacy baseline (section 5).
3. re-approval of all baselines from a committed state, under your own name

## 2. Changes made in this task

1. **Renamed** `reports/phase_8_independent_audit.md` → `reports/phase_8_independent_audit_report.md`,
   using `mv`, so the content is unchanged apart from item 2. It now matches the `.gitignore`
   exception `!reports/phase_*_report.md`: `git check-ignore -v` reports the negation rule, and
   `git status` lists it as `??`.
2. **Updated references** to the old file name:
   - `reports/phase_8_1_remediation_report.md`: 3 places; historical notes marked "resolved in
     Phase 8.2"
   - the audit report's own hygiene note, marked resolved, with the original name kept for history

   No other file referenced it.
3. **Created this report.**

**No source, test, workflow, rule, dataset, threshold, known-defect or baseline file was changed
in Phase 8.2.**

## 3. Test results (executed)

| Run | Result |
|---|---|
| `python -m pytest -q` | **756 passed, 12 skipped**. Matches Phase 8.1 exactly. |
| `pytest --collect-only` | **768 collected** |
| Skip audit (JUnit from this run) | 12 skipped, 12 allowed opt-in live tests, **0 unexpected** |
| Phase 7 CI tests (`test_ci_check`, `test_ci_summary`, `test_ci_workflow`) | **88 passed** |

## 4. Local CI simulation (executed)

Every `run:` step of `.github/workflows/ci.yml` was executed in job order. Install steps were
skipped because the venv is already provisioned; GitHub-only variables were stubbed. Then the
failure-path script and a new pytest/advisory scenario script were run.

| Check | Result |
|---|---|
| All expected CI steps execute | ✅ 7 jobs, all `success`; **19/19 checks met their expectations** (the 15 Phase 7 checks + 4 observability checks) |
| All five record types produced | ✅ pytest, custom_engine, promptfoo, rag_retrieval, rag_mock (all PASS); plus 3 seeded-demo records (BLOCKED) |
| Default strict selection | ✅ `regression.json` `selection` is empty, so the default one-record-per-type comparison was used. All 5 comparisons are comparable, with 0 findings, PASS. |
| Gate depends on `observability` | ✅ `quality-gate.needs` includes `observability`; `observability` needs the 5 quality jobs, `if: always()` |
| Missing artifact → ERROR | ✅ rag-mock report removed: record fails, compare ERROR, gate exit 2 |
| Malformed artifacts or inputs → ERROR | ✅ malformed history, missing manifest, edited baseline: each gate exit 2 |
| Critical regression → BLOCKED | ✅ fail-open report as the normal custom-engine result: gate exit 1 |
| Expected skips don't lower the pass rate | ✅ +1 opt-in live test: PASS against both the committed (legacy) baseline and a scratch current-definition baseline |
| Unexpected skip blocks | ✅ BLOCKED (`unexpected_skips`) against both |
| Test failure blocks | ✅ BLOCKED (`tests_failed`; also `executed_pass_rate` against the current-definition baseline) |
| Reduced executed tests block | ⚠️ **Only against a current-definition baseline.** A test turning into a skip: BLOCKED (`executed_tests`) against the scratch baseline, but **PASS against the committed legacy pytest baseline**, which has no `executed_tests`. This is the known Phase 8.1 condition, and it's why the pytest refresh is required. |
| Reduced total test count | ✅ BLOCKED only below the legacy baseline's 701 (700 vs 701 → gate 1). The suite has 768, so up to 67 removals wouldn't be caught until the refresh. |
| Three seeded regression demos | ✅ all BLOCKED, `expectation_met: true`, `report_problem: null` |
| Advisory Ragas can't block | ✅ all four judge scores ×0.3 against a scratch advisory baseline: findings `advisory` only, PASS (exit 0) |
| No live model or secret needed | ✅ No Ollama, `--llm ollama`, API key or `secrets.` in the workflow (the only "Ollama" is a comment); the simulation ran without any |

The scratch baselines lived only under the session scratch directory. They were built with an
explicit commit and `uncommitted_changes: null`, and `--allow-uncommitted` wasn't used.
`reports/baselines/` stayed byte-identical (section 5).

## 5. Baseline integrity (executed)

- `python -m ai_quality_lab.observability baseline verify`: **"5 baseline(s) checked: OK"**,
  exit 0.
- **All five types are present and valid under the new metric catalog:** custom_engine,
  promptfoo, pytest, rag_mock, rag_retrieval. Every status is PASS. The pytest baseline's legacy
  `test_pass_rate` is accepted as a catalogued legacy rate.
- **Audit log:** 5 lines; `audit_problems()` returns none (each manifest entry matches the latest
  approval for its type).
- **No approval or refresh happened:** the byte hashes are identical at the start of this task, at
  the end, and versus the pre-Phase 8.1 snapshot.

SHA-256 of all 7 files (working copy, LF line endings):

```
7a62bc38504e5372f71c0c67ee39c90d866673809a3fecf0512ad2b5f605395e  reports/baselines/audit.jsonl
072ced33743feca98019457bb5c8e9027e186094be949b698a6d4055e2fa687b  reports/baselines/custom_engine/initial-custom-engine.json
6f2c66a195468fd50c05ff418d952485dd3da328c0d180fa026fdf5d5eef89bb  reports/baselines/manifest.json
91cfdbfa4320bdff36a90231d0b579e94b5ccd0293037cf2cee4d71d95276d03  reports/baselines/promptfoo/initial-promptfoo.json
3aa11286e036414e9513ce121d7ee3ea33cf57d26847c0929b8087457268fb6f  reports/baselines/pytest/initial-pytest.json
ce8fb2c1e94c3ab57472b156400946e673415c1173263cb20a0f49cbfd75ca5f  reports/baselines/rag_mock/initial-rag-mock.json
6b4753cdf0551cfb8f735ac88660557acaf7fe1b8e996ef7019bf1623dc6d397  reports/baselines/rag_retrieval/initial-rag-retrieval.json
```

**Byte hashes will change after a commit and a fresh Windows checkout.** `.gitattributes` has
`* text=auto`, so Windows checkouts get CRLF. That's expected and harmless: integrity is checked
with the **canonical-JSON** hash stored in the manifest, which ignores line endings (tested with a
CRLF baseline). Use `baseline verify`, not file byte hashes, after checkout.

## 6. GitHub Actions portability review

**Verified by inspection or local execution:**
- **Python and dependencies:** 3.12; every install uses `-c constraints/ci-primary.txt`. The
  observability code is standard library only (imports checked), so the lock files don't need to
  change.
- **Paths:** POSIX relative paths only. The scan of the workflows found no `\` (other than Bash
  line continuations), `.exe`, `Scripts/`, `$env:` or `C:`. Records store POSIX paths.
- **Shell:** Bash only. The record step uses `|| status=1`, then `exit $status`, which is safe
  under GitHub's `bash -e -o pipefail`.
- **Artifacts:** `download-artifact@v4` with `pattern: ci-*` gives `downloaded/ci-<job>/…`,
  which matches the paths in the record step (the local simulation used the same layout). Uploads
  use `if: always()`. A missing artifact → ERROR (executed).
- **Dependencies and gate:** verified (section 4).
- **Permissions and credentials:** `contents: read` at workflow level; `persist-credentials:
  false` on all 7 checkouts; no `secrets.` references.
- **Committed files only:** the workflow reads the checkout and its own artifacts. Every file it
  needs is **not git-ignored** (checked with `git check-ignore`):
  - `reports/baselines/**`
  - `ci/regression_rules.json`
  - `src/ai_quality_lab/observability/**`
  - the tests

  **They are currently untracked, so they must be committed in the same push, or CI will ERROR
  for missing baselines or modules.**
- **Baseline verification is deterministic:** the canonical-JSON hash and schema checks run
  whenever `compare` loads a baseline. CI doesn't run `baseline verify` (the manifest-vs-audit
  check). RECOMMENDATION: not added here, because it's out of scope.
- **No Ollama or API credentials** (verified).

**Risks only a real GitHub run can settle:**
1. **Fingerprints on an LF Linux checkout** must equal the Windows-recorded baselines. If they
   don't, the run says "not comparable: dataset_fingerprint / corpus_fingerprint changed" and is
   ERROR, not a false PASS. Don't approve around it.
2. **pytest on Ubuntu** must collect the same 768 tests with the same 12 skips. A platform-specific
   skip would be an unexpected skip → BLOCKED.
3. **Promptfoo record:** `promptfoo-results.json` produced on Linux must parse into 33 cases.
4. **Artifact layout and job-summary rendering** on the real runner, and job timing within the
   10-minute observability timeout.
5. **Carry-over annotations:** Node 20 deprecation and the Ubuntu 26 migration warnings (not
   failures).

## 7. Outstanding risks

1. **Pytest baseline is legacy (hard condition):**
   - silently skipped tests aren't detected (`executed_tests` isn't compared)
   - up to 67 removed tests aren't detected (test_count 701 vs 768)
   - fixed only by your refresh after the commit
2. **Baseline provenance:** all five baselines were approved by Claude Code from an uncommitted
   tree. They're provisional until you re-approve them.
3. **GitHub and Ubuntu: NOT RUN.** See section 6, items 1–5.
4. **Not fixed** (documented in the audit and remediation reports):
   - P11: high-severity case-level detection
   - P12: commit and timestamp format validation
   - uncalibrated live tolerances, and no `rag_live` baseline
   - text-only ERROR categories
   - no persistent CI history
   - Phase 7 risks: timeout classification, SHA pinning, admin bypass
5. **Known defects D-6.1 to D-6.4:** open, unchanged, listed in every summary.

## 8. Proposed commit grouping (not staged)

**Phase 8 and 8.1 can't be split cleanly by file.** Several new files contain both:
- `records.py`, `regression.py`, `baselines.py`, `cli.py`
- `ci/regression_rules.json`
- the observability tests and the docs

Splitting them by phase would need `git add -p` hunk selection, which only you can do. The
practical grouping below separates them by *purpose*, with the phase noted:

| # | Commit | Files | Phase |
|---|---|---|---|
| 1 | Phase 7 review report | `reports/phase_7_pre_commit_review_report.md` | 7 (pending since Phase 7) |
| 2 | Observability implementation and CI | `src/ai_quality_lab/observability/` (8 + 8.1), `src/ai_quality_lab/reporting.py` (8 + 8.1), `src/ai_quality_lab/runner.py` (8 + 8.1), `src/ai_quality_lab/engine_comparison.py` (8.1), `src/ai_quality_lab/rag/failures.py` (8), `src/ai_quality_lab/ci_summary.py` (8), `ci/regression_rules.json` (8 + 8.1), `ci/known_defects.json` (8: D-6.4, tracked metric), `.github/workflows/ci.yml` (8) | 8 + 8.1 |
| 3 | Tests | `tests/test_observability_*.py` (5 files, 8 + 8.1), `tests/conftest.py` (8), `tests/test_runner.py` (8 + 8.1), `tests/test_ci_summary.py` (8), `tests/test_ci_workflow.py` (8) | 8 + 8.1 |
| 4 | Documentation and reports | `README.md`, `CLAUDE.md`, `docs/ci-pipeline.md`, `docs/quality-observability.md`, `reports/phase_8_completion_report.md`, `reports/phase_8_independent_audit_report.md`, `reports/phase_8_1_remediation_report.md`, `reports/phase_8_2_finalization_report.md` | 8, 8.1, 8.2 |
| 5 | Provisional baselines | `reports/baselines/` (7 files) | 8 (provisional, Claude-approved) |

**Unexpected or unrelated changes:** none.
- The Phase 7 review report is related, but belongs to Phase 7, hence its own commit.
- `.gitignore`, the datasets, `pyproject.toml`, the lock files, `package*.json` and the Ragas
  workflow are unchanged.
- Generated files (`reports/history/`, `reports/quality/`, `ci-artifacts/`, `downloaded/`) are
  git-ignored and were left alone.

**Order and pull request:**
- Commits 2 and 3 must land together with 5: without the baselines, `regression-vs-baseline` is
  ERROR.
- **Use one branch and one pull request** containing commits 1 to 5. That way:
  - CI runs on the PR head with everything present
  - the required `Quality gate + summary` check gates the merge, rather than being bypassed by an
    admin push
  - the provisional baselines are reviewed visibly as their own commit
- Keep commit 5 last, so the baseline change is easy to see and later superseded.

## 9. Exact next commands (for you to run)

**A. Commit and open a pull request** (PowerShell, from the repository root):

```powershell
git switch -c phase-8-observability
git add reports/phase_7_pre_commit_review_report.md
git commit -m "Phase 7: pre-commit review report"
git add src/ai_quality_lab/observability src/ai_quality_lab/reporting.py src/ai_quality_lab/runner.py src/ai_quality_lab/engine_comparison.py src/ai_quality_lab/rag/failures.py src/ai_quality_lab/ci_summary.py ci/regression_rules.json ci/known_defects.json .github/workflows/ci.yml
git commit -m "Phase 8/8.1: quality observability, regression detection, baseline governance, CI job"
git add tests/test_observability_records.py tests/test_observability_history.py tests/test_observability_regression.py tests/test_observability_baselines.py tests/test_observability_cli.py tests/conftest.py tests/test_runner.py tests/test_ci_summary.py tests/test_ci_workflow.py
git commit -m "Phase 8/8.1: observability and remediation tests"
git add README.md CLAUDE.md docs/ci-pipeline.md docs/quality-observability.md reports/phase_8_completion_report.md reports/phase_8_independent_audit_report.md reports/phase_8_1_remediation_report.md reports/phase_8_2_finalization_report.md
git commit -m "Phase 8/8.1/8.2: documentation, audit and completion reports"
git add reports/baselines
git commit -m "Phase 8: provisional baselines (approved by Claude Code; to be re-approved by owner)"
git status            # must be clean
git push -u origin phase-8-observability
gh pr create --base main --title "Phase 8: quality observability and regression detection" --body "See reports/phase_8_2_finalization_report.md"
gh pr checks --watch
```

**B. Check the first GitHub run** against the audit report's §7 checklist, with these Phase 8.1
updates:
- `regression-vs-baseline` is PASS, and all five comparisons say **comparable: yes**
- pytest shows test_count 768 vs 701, with `executed_tests` / `executed_pass_rate` newly measured
  and no finding
- the three regression demos are BLOCKED with `report_problem: null`
- the gate summary shows "Regression vs baseline: PASS" and D-6.1 to D-6.4

If anything is "not comparable", stop: that's a fingerprint portability defect, not something to
approve.

**C. After merging, re-approve the baselines from the committed state** (your authority; pytest
first):

```powershell
git switch main; git pull; git status          # must be clean
$env:AIQL_EMBED_PROVIDER = "hashing"
Remove-Item reports\history\quality_history.jsonl -ErrorAction SilentlyContinue
python -m pytest -q -p no:cacheprovider --junitxml=ci-artifacts/python-tests/junit.xml
python -m ai_quality_lab.ci_summary skips --junit ci-artifacts/python-tests/junit.xml --out ci-artifacts/python-tests
python -m ai_quality_lab.runner --output reports/quality/custom-engine-report.json
npm run eval:promptfoo
python -m ai_quality_lab.rag.evaluate --mode retrieval --embedder hashing --output reports/quality/rag-retrieval.json
python -m ai_quality_lab.rag.evaluate --mode rag --llm mock-gullible --embedder hashing --output reports/quality/rag-mock.json
python -m ai_quality_lab.observability record --kind pytest --report ci-artifacts/python-tests/skip-audit.json
python -m ai_quality_lab.observability record --kind custom_engine --report reports/quality/custom-engine-report.json
python -m ai_quality_lab.observability record --kind promptfoo --report reports/promptfoo/results.json
python -m ai_quality_lab.observability record --kind rag --report reports/quality/rag-retrieval.json
python -m ai_quality_lab.observability record --kind rag --report reports/quality/rag-mock.json
python -m ai_quality_lab.observability compare --types custom_engine,promptfoo,rag_retrieval,rag_mock,pytest --latest
```

1. **Read every finding.** Expect PASS; the only differences should be pytest improvements.
2. **Approve each type.** Take each run ID from the "Runs selected" lines, and don't pass any
   `--accept-*` or `--allow-uncommitted` flag. If one seems necessary, stop and investigate.
   ```powershell
   python -m ai_quality_lab.observability baseline approve --run-id <id> --approved-by "<your name>" --reason "Owner review: replaces provisional Phase 8 baseline approved by Claude Code"
   ```
3. **Verify:**
   ```powershell
   python -m ai_quality_lab.observability baseline verify
   ```
4. **Commit `reports/baselines/` on a new branch and open a pull request.**

---

**Statement:** GitHub Actions and Ubuntu validation of Phase 8, 8.1 and 8.2 are **NOT RUN**. No
result in this report comes from a GitHub-hosted runner.
