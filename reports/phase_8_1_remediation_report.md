# Phase 8.1 Remediation Report: Independent Audit Findings D-8.1 to D-8.5

**Date:** 2026-11-01. **Branch:** `main` at `2d28111`, with the uncommitted Phase 8 and 8.1 work.
Nothing was committed, pushed or tagged, and no baseline was approved, regenerated or modified.

Every result below was executed in this session. GitHub Actions and Ubuntu validation is **NOT
RUN**.

---

## 1. Outcome: READY WITH CONDITIONS

All five audit defects are fixed. Each has regression tests, and each was demonstrated by
execution. The governance improvements requested alongside them are in, except P11 and P12.

**Conditions** (section 8):
1. the first GitHub Actions run
2. re-approval of all baselines by you, from a committed state
3. **an urgent refresh of the pytest baseline:** until then, CI only notices removed tests below
   701, while the suite now has 768
4. renaming the audit file so Git can track it

---

## 2. Defects and resolutions

| Defect | Resolution | Proven by |
|---|---|---|
| **D-8.1:** a report's status was trusted. A READY report with a critical failure became a PASS record and could be a first baseline. | **(a) Record builder:** the release policy is recomputed from the record's metrics (`blocking_reasons`):<br>• custom engine: zero cases, any critical FAIL or any ERROR<br>• RAG: the same, plus hit rate < 0.9, recall < 0.8, or restricted retrieval<br>A report claiming READY with any of these, or BLOCKED with none, is **refused** (`RecordError` → exit 2 → CI ERROR). Promptfoo's status is already computed by the builder.<br>**(b) Approval:** `quality_blockers()` re-checks content **independently of `status` and of the builder**:<br>• critical cases not passing and errored cases<br>• critical and error counts, RAG gates, restricted retrieval<br>• pytest failures, unexpected skips, zero executed tests | Tests:<br>• READY with a critical failure<br>• READY with an error<br>• BLOCKED with nothing blocking<br>• consistent READY and BLOCKED<br>• READY RAG with a failed recall gate<br>• an inconsistent record as a first baseline, and against an existing one (rejected even with `--accept-regression`)<br>• the quality blockers for errored, RAG-gate and pytest records |
| **D-8.2:** `test_pass_rate` counted expected skips. Adding an opt-in live test gave a false BLOCKED. | The pytest record now has `test_count`, `executed_tests`, `passed_tests`, `tests_failed`, `expected_skips`, `unexpected_skips`, and **`executed_pass_rate` = passed / executed**.<br>• **Zero executed tests:** the rate is unmeasured and the status is ERROR.<br>• **Inconsistent skip audits are refused.**<br>• **Rules:** the `test_pass_rate` rule was removed; `executed_tests` and `executed_pass_rate` were added (block, tolerance 0). | Tests:<br>• +1 expected skip → **PASS**<br>• +1 unexpected skip → BLOCKED<br>• +1 failed test → BLOCKED<br>• a test that starts skipping → BLOCKED via `executed_tests`<br>• zero executed → ERROR and unmeasured<br>• the legacy committed pytest baseline compares with no false findings |
| **D-8.3:** a UTF-8 byte-order-mark report (PowerShell 5.1 `>`) was rejected. | Reports are read with `utf-8-sig` (in `records._load_report` and `engine_comparison.load_promptfoo_results`). Malformed JSON, UTF-16 and invalid bytes are still refused.<br>New `runner --output FILE` writes UTF-8 directly; the docs now use it instead of `>`. | Tests: a BOM report records identically to the plain one (same metrics, same `report_sha256`); a BOM with malformed JSON, UTF-16 and invalid bytes are still refused; `runner --output` writes strict UTF-8.<br>**PowerShell execution:** a real `>` redirect (first bytes `239,187,191`) now records, exit 0; `runner --output` writes no BOM. |
| **D-8.4:** `compare` failed on a local history with several runs of a type. | `compare --run-id ID` (repeatable or comma-separated, one per type) and `compare --latest` (newest per type; a timestamp tie is ERROR, never resolved by file order). The selected run is printed under "Runs selected" and stored in `regression.json` (`selection`).<br>The default is unchanged: exactly one record per type, else ERROR. CI uses the default. Comparability is still checked after selection. | Tests: several runs with no selection → ERROR; `--run-id` → selected; `--latest` → the newer run, named; unknown ID, two IDs of one type, a repeated ID, an unrequested type, `--run-id` together with `--latest`, and a tie → ERROR; a selected incompatible run → ERROR.<br>**PowerShell execution:** with no selection, exit 2; `--run-id run-a`, exit 0; `--latest` names run-b, exit 0. |
| **D-8.5:** an integer value skipped the rate check (`pass_rate = 1` with 1/4 passed). | A **metric catalog** (`records.METRIC_CATALOG`) fixes each name's unit and kind:<br>• **count:** an integer; a numerator, if given, equals the value; 0 of 0 is valid<br>• **rate:** numerator and denominator required, denominator > 0, the value equals numerator/denominator and is in [0, 1]<br>• **score:** a mean in [0, 1], no numerator, denominator = items scored<br>`ragas_*` scores must be advisory; everything else deterministic. Unknown names are rejected. The unit comes from the name, never from the Python type. | 13 rejection cases: an integer rate, a missing numerator, a missing denominator, denominator 0, numerator > denominator, a float count, a count/numerator mismatch, a score with a numerator, a score without a denominator, a score > 1, a wrong kind both ways, and an unknown name. 8 acceptance cases, including the legacy `test_pass_rate` found in existing baselines. |

### Governance improvements

1. **Provenance (done).** Approval rejects a record with an unknown commit or
   `uncommitted_changes: true`, unless `--allow-uncommitted` is given, which is logged as
   `uncommitted_source`.
   - `record --commit X` sets `uncommitted_changes` to unknown (`null`), because the report came
     from commit X, not from this tree.
   - Tests cover both rejections, the override, and the audit entry.
2. **Overrides are explicit and logged (done).** Every flag goes into the audit `flags`.
3. **Identity changes are shown (done).** The rejection lists the old → new identity fields and
   every metric that differs. On acceptance, `identity_change` is stored in the audit log and
   printed. They're never approved silently: the flag is still required. (Test.)
4. **Manifest and audit-log consistency (done).**
   - `audit_problems()` checks that every manifest entry matches the latest approval for its type.
   - `baseline verify` combines this with hash and schema checks.
   - Tests cover a clean directory, a missing audit line and the committed baselines (0
     problems).
5. **Duplicate reports (done, warning only).** `source.report_sha256` is the content hash, and
   `record` warns when an identical report of that type is already in the history. It isn't
   refused, because a repeated deterministic run (for example the pytest skip audit, which has no
   timings) can legitimately be identical. (Test.)

**Not done:**
- **P11:** case-level detection for high severity
- **P12:** commit and timestamp plausibility checks

Neither was in the required list. They remain recommendations.

## 3. Files changed in Phase 8.1

| File | Change |
|---|---|
| `src/ai_quality_lab/observability/records.py` | metric catalog and unit validation; `blocking_reasons` / `_consistent_status`; `utf-8-sig`; pytest metrics over executed tests; `report_sha256` |
| `src/ai_quality_lab/observability/regression.py` | `select_records`; `RunComparison.selection` |
| `src/ai_quality_lab/observability/baselines.py` | `quality_blockers`, provenance check, identity-change details, `audit_problems` |
| `src/ai_quality_lab/observability/cli.py` | `compare --run-id/--latest`, the duplicate warning, `record --commit` clears `uncommitted_changes`, `approve --allow-uncommitted`, `baseline verify`, help text |
| `src/ai_quality_lab/reporting.py` | "Runs selected" section |
| `src/ai_quality_lab/runner.py` | `--output FILE` (UTF-8) |
| `src/ai_quality_lab/engine_comparison.py` | BOM-tolerant read of Promptfoo results |
| `ci/regression_rules.json` | `test_pass_rate` rule removed; `executed_tests` and `executed_pass_rate` added, each with a reason |
| `docs/quality-observability.md` | metric units, D-8.1 refusal, pytest metrics, rules change, approval rules, `baseline verify`, provisional-baseline status, run selection, Windows commands |
| `docs/ci-pipeline.md`, `CLAUDE.md` | CI keeps the default selection; the catalog and consistency rules |
| Tests | `test_observability_{records,regression,baselines,cli}.py`, `test_runner.py` |

**Not changed:**
- `reports/baselines/` (byte-identical: SHA-256 of all 7 files compared before and after)
- `.github/workflows/ci.yml` in Phase 8.1
- datasets, guardrails and release thresholds
- every Phase 7 test

## 4. Tests

| Run | Result |
|---|---|
| `python -m pytest -q` (full offline suite) | **756 passed, 12 skipped** (768 collected) |
| Before Phase 8.1 | 691 passed, 12 skipped |
| Difference | **+65 tests, all new:** records +33, regression +15, baselines +11, CLI +5, runner +1. The skips are unchanged: the 12 opt-in live tests (9 `test_llm_live`, 3 `test_rag_live`), 0 unexpected. |
| Flakiness check | The CLI and runner tests ran 3 times: 46 passed each time. One timing-dependent assertion (a same-second `--latest` tie) was removed, because the tie is covered deterministically by a unit test. |

**Changes to existing Phase 8 tests: fixture data only, no assertions changed or removed.** Each
used data that the stricter rules now correctly reject:

| Test | Old data | New data |
|---|---|---|
| `test_pytest_record_statuses` | A skip audit with 2 skips, listed as 2 expected + 1 unexpected | 1 expected + 1 unexpected |
| `test_invalid_records_are_rejected` (3 cases) | Metric name `x` | Catalog names (`critical_failures`, `pass_rate`); same expected messages |
| `test_live_tolerance_boundary`, `test_deterministic_runs_have_zero_tolerance`, `test_metric_kind_change_is_an_error` | Rates with no numerator or denominator | Explicit numerator/denominator (e.g. 7600/10000) |
| The `approved` CLI fixture | Recorded with the checkout's own (dirty) state | Passes `--commit abc1234`, so the test doesn't depend on whether the working tree is clean |

No Phase 7 test was modified.

## 5. Validation (executed)

| Check | Result |
|---|---|
| Phase 7 CI tests (`test_ci_check`, `test_ci_summary`, `test_ci_workflow`) | Pass (inside the full run) |
| Local CI simulation: every `run:` step of `ci.yml` | **19/19 checks met their expectations; gate PASS** |
| Regression vs the provisional baselines | All 5 types PASS and comparable, 0 findings. pytest compares the legacy baseline with no false finding (D-8.2). |
| 3 seeded regression demos | All BLOCKED, `report_problem: null` |
| Failure paths | healthy PASS (0) · fail-open as normal BLOCKED (1) · test count 700 vs baseline 701 BLOCKED (1) · missing artifact ERROR (2) · malformed history ERROR (2) · missing manifest ERROR (2) · edited baseline ERROR (2) |
| Multi-run local history (PowerShell) | No selection → 2; `--run-id run-a` → 0, run named; `--latest` → 0, run-b named "latest of 2" |
| BOM ingestion (PowerShell) | A real `>` redirect (BOM bytes `239,187,191`) records, exit 0 |
| `baseline verify` on the committed baselines | "5 baseline(s) checked: OK", exit 0 |
| Baseline integrity | All 7 files under `reports/baselines/` are byte-identical before and after |
| GitHub Actions and Ubuntu | **NOT RUN** |

**About one failure-path scenario:** the earlier "fewer tests (−5)" scenario now PASSes, correctly.
The suite grew to 768 tests, so 5 fewer is still above the provisional baseline's 701. It was
replaced by "700 vs baseline 701", which is BLOCKED. This exposes the condition in section 8, item
3.

## 6. Backward compatibility

- **Record schema:** still version 1, with no migration. Every Phase 8 record, and all committed
  baselines, satisfy the catalog. Records with **unknown metric names are now rejected**.
- **`source` fields:** gains `report_sha256`, which is optional when reading. **Audit entries**
  gain `identity_change`, which older entries don't have.
- **Pytest metrics:**
  - New records no longer contain `test_pass_rate`. It stays in the catalog (legacy) so the
    provisional baseline still loads.
  - It has no rule, so it's never compared.
  - The new metrics aren't compared until the pytest baseline is refreshed.
- **Approval is stricter:**
  - Records from uncommitted or unknown-commit states need `--allow-uncommitted`. The current
    provisional baselines would need it to be re-approved as they are, and they shouldn't be.
  - Reports with an inconsistent status are now refused at `record`.
- **CLI:** new options only: `runner --output`, `compare --run-id/--latest`,
  `approve --allow-uncommitted`, `baseline verify`. CI commands are unchanged.

## 7. Unresolved issues

- **P11:** high-severity case swaps aren't detected case by case.
- **P12:** commit and timestamp formats aren't validated.
- **Live-model tolerances are uncalibrated** (one live run). There's no `rag_live` baseline.
- **ERROR categories** are distinguished only by message text.
- **There's no persistent CI history** (14-day artifacts).
- **Phase 7 risks:** timeout classification, SHA pinning, admin bypass.
- **Open known defects:** D-6.1 to D-6.4 remain open and visible.
- **The audit file is git-ignored** (resolved in Phase 8.2 by renaming it to
  `reports/phase_8_independent_audit_report.md`): `reports/phase_8_independent_audit.md` doesn't match the
  `phase_*_report.md` exception, so it needs renaming before it can be committed. This file's name
  matches and is tracked.

## 8. Readiness and next steps

**Phase 8: READY WITH CONDITIONS.** The defects are fixed and proven locally. The remaining
conditions are external or require your authority:

1. **Rename the audit file:** `reports/phase_8_independent_audit.md` →
   `reports/phase_8_independent_audit_report.md`. (Done in Phase 8.2.)
2. **Review the diff and commit:** Phase 7 review, Phase 8, Phase 8.1, and the provisional
   baselines, so that CI has baselines to compare against. Push, preferably through a pull
   request.
3. **Check the first GitHub run** with the checklist in `phase_8_independent_audit_report.md` §7, with
   one update: the pytest comparison now shows `test_count` 768 vs 701, and `executed_tests` /
   `executed_pass_rate` as newly measured (no finding).
4. **Refresh the pytest baseline right after the commit.** Until then, removing up to 67 tests
   wouldn't be noticed by CI.
   - Record a clean run on the committed state (`uncommitted_changes: false`).
   - Run `compare --types pytest --latest`; it should show only improvements.
   - Approve it with your name.
5. **Re-approve the other four baselines the same way** (audit §5), then run `baseline verify` and
   commit `reports/baselines/` through a reviewed pull request.
