# Phase 8 Independent Audit: Quality Observability, CI Readiness and MVP Sign-Off

**Date:** 2026-11-01. **Auditor:** Claude Code, acting as an independent reviewer. The same agent
also built Phase 8; read the findings with that in mind.

**Scope:**
- the Phase 8 working tree on top of `2d28111`
- `src/ai_quality_lab/observability/`, `reporting.py` and `ci_summary.py`
- `ci/regression_rules.json` and `ci/known_defects.json`
- `reports/baselines/`
- `.github/workflows/ci.yml`
- the tests and docs

**Method:**
1. read the source
2. re-ran the suite
3. re-ran the local CI simulation and failure-path scripts
4. ran 13 new edge-case probes (`audit_probes.py` in the session scratchpad) plus a PowerShell
   redirect probe

All probes used scratch copies. Nothing was committed, pushed or approved, and `reports/baselines/`
was not modified; `git status` showed it unchanged after every probe.

**Labels:** VERIFIED (inspected or executed) · NOT VERIFIED (needs external or future validation)
· NOT RUN · DEFECT (a demonstrated failure) · RECOMMENDATION.

---

## 1. Executive verdict: READY WITH CONDITIONS

**VERIFIED: no false-PASS path in the CI comparison path.** Under every adversarial input tried, a
missing, corrupt or incompatible input produced ERROR, and a seeded regression produced BLOCKED.
The tried inputs were:
- a missing artifact
- malformed history
- a missing manifest
- an edited baseline
- missing metrics
- a dataset or model change
- a shrinking dataset
- fewer tests
- a fail-open report
- a degraded retrieval report

**Found:**
- **3 medium defects:** D-8.1 approval integrity, D-8.2 a false BLOCK, D-8.3 a broken documented
  Windows command
- **2 low defects:** D-8.4 and D-8.5
- **several governance gaps**

None of them can turn a regression into a PASS in CI. **D-8.1** lets an internally inconsistent
report become a *first* baseline. **D-8.2** will produce a false BLOCKED the first time someone
adds an opt-in live test.

**Conditions for sign-off (section 11):**
1. fix D-8.1 to D-8.3
2. regenerate the pytest baseline, because D-8.2 changes a metric's definition
3. commit, then run the first GitHub Actions run
4. re-approve the baselines from the committed state, under your own name

**The Phase 8 completion report's own claims checked out:** test counts, the simulation, failure
paths, baseline values, additivity, and its stated conditions are all accurate. It didn't know
about the defects below.

---

## 2. Git and repository integrity

| Claim | Status | Evidence |
|---|---|---|
| Branch `main`, HEAD `2d28111` (it includes `15dc532`) | VERIFIED | `git branch --show-current`, `git log` |
| The Phase 8 work is **uncommitted** | VERIFIED | 13 modified files and 12 untracked paths; nothing staged |
| The baseline files are **not committed** | VERIFIED | `git ls-files reports/baselines` is empty; `reports/baselines/` is untracked but not ignored |
| The change is additive | VERIFIED | `git diff -U0 HEAD -- tests` removes **0** lines. In `src/`, `.github/` and `ci/`, the only removed lines are the 6 replacements listed below. |
| No existing dataset or threshold changed | VERIFIED | `evals/`, `RETRIEVAL_GATES` and the release policy aren't in the diff |
| Other untracked files | VERIFIED | `reports/phase_7_pre_commit_review_report.md` (from the earlier review, still uncommitted) and `reports/phase_8_completion_report.md` |

The 6 replaced lines:
- the gate's `needs` line (it gained `observability`)
- the D-6.1 `ci_visibility` line (it gained `tracked_metric`)
- the old defect-line format in `ci_summary`
- the `reporting.py` placeholder docstring
- the runner's `print(summary.to_json())`

**Repository hygiene note** (resolved in Phase 8.2: this file was renamed to
`reports/phase_8_independent_audit_report.md`): this audit file itself, originally
`reports/phase_8_independent_audit.md`, was
**git-ignored**. The `.gitignore` exception only covers `phase_*_report.md`. VERIFIED with
`git check-ignore -v`. It needs renaming (for example to `phase_8_independent_audit_report.md`)
or a `.gitignore` exception before it can be committed.

## 3. Record and history integrity

| Question | Result |
|---|---|
| Can an incomplete or malformed report produce a misleading PASS? | **Partly.** A report that is missing fields, unreadable, not a JSON object, or has case IDs that don't match the dataset (stale or empty, as in probe P13) is rejected: VERIFIED by tests and probes. **But the record's `status` is copied from the report's `release_readiness` without checking it against the record's own metrics:** a report that says READY while containing a critical FAIL becomes a PASS record (**DEFECT D-8.1**). |
| Can missing metrics become zero? | **No.** VERIFIED: an unmeasured metric goes to `unmeasured` with a reason, and validation rejects a `None` value. A metric present in the baseline but missing now is ERROR. |
| Can a zero denominator be treated as a measurement? | **No for rates.** VERIFIED: validation rejects a rate over 0, and `rate()` writes it to `unmeasured`. Counts over 0 (e.g. 0 of 0 medium cases) are valid by design. **Gap:** an *integer* value is treated as a count, so `pass_rate = 1` with 1/4 passes validation (**DEFECT D-8.5**, low). |
| Can duplicate runs or case IDs corrupt comparisons? | Duplicate run IDs and duplicate case IDs are rejected: VERIFIED. **The same report recorded twice under different run IDs is accepted** (probe P4). That's not a false PASS, because `compare` then returns ERROR for two records of one type, but trends would show the run twice. RECOMMENDATION: store the report's SHA-256 in `source` and flag duplicates. |
| Can different datasets, models, embedders or knowledge bases be compared by accident? | **No.** VERIFIED: any `identity` difference is ERROR and names the field. The key is re-hashed on every validation, so an edited identity is detected. |
| Are timestamps, run IDs and commits validated? | Timestamps must be UTC and a valid date: VERIFIED. Run IDs must be non-empty, and a filesystem-safe name at approval. **Commit and branch formats aren't validated, and future timestamps are accepted** (probe P12: `"not-a-sha"`, year 2099). RECOMMENDATION (low). |
| Are numerators, denominators and ratios consistent? | VERIFIED for float rates (within 1e-4), and numerator ≤ denominator. Exception: D-8.5. |
| Can an incomplete report be recorded as a successful evaluation? | No for missing or partial cases: the case-ID check covers this. Yes for an *inconsistent* one: D-8.1. |
| Can a record-generation error look like a quality regression? | **No.** VERIFIED: record failures are exit 2 and recorded nothing, and the CI compare step then reports ERROR ("no evaluation record"), not BLOCKED (failure path "missing artifact": gate exit 2). |
| Can a malformed history be partly accepted? | **No.** VERIFIED: any bad line makes the whole history an error, with line numbers, and `append` refuses to extend a corrupt file. |

## 4. Regression detection findings

| Property | Status | Evidence |
|---|---|---|
| A new critical failure always blocks | VERIFIED | Case-level check, including a count-neutral swap (test); fail-open report → BLOCKED (failure path) |
| Critical, high, safety, error and restricted counts can't silently pass | VERIFIED | All rules are `block` with tolerance 0 for deterministic runs (repo-rules test) |
| Reduced test or case count is detected | VERIFIED | −5 tests → gate BLOCKED. A dataset shrink is flagged even when the datasets aren't comparable (test). |
| Unexpected skips are detected | VERIFIED | Rule test, plus the existing skip audit |
| Missing metrics → ERROR | VERIFIED | Test and failure path |
| Incompatible runs aren't compared | VERIFIED | Dataset or model change → ERROR |
| Advisory results can't block, or approve | VERIFIED | Code forces `advisory` (test with a rules file that says `block`); a Ragas collapse to ×0.3 stayed PASS. Advisory records can be approved as baselines, but they never gate. |
| BLOCKED vs ERROR distinguished; the worst wins | VERIFIED | `Comparison.status` is ERROR > BLOCKED > PASS; the run level is the same |
| Tolerances applied consistently | VERIFIED | The boundary test (0.04 tolerated, 0.0401 blocked); deterministic runs use 0 |
| A missing or corrupt baseline can't PASS | VERIFIED | A missing manifest and an edited baseline → gate exit 2 |
| **False BLOCK on an unchanged suite** | **DEFECT D-8.2** | Probe P3, below |
| Case-level detection for **high** severity | Gap | A count-neutral swap of two high cases → PASS (probe P11). Only critical cases are tracked case by case. RECOMMENDATION. |

**Does the system tell these five situations apart?**
- **No regression:** PASS.
- **A measurable regression:** BLOCKED, with the metric, values, denominators and tolerance.
- **Insufficient data, incompatible data, and a broken evaluation process:** all are **ERROR**.
  They're distinguished only by the message text (`problems` and the finding reasons).
  RECOMMENDATION: add an `error_category` field (`missing_data` / `incompatible` /
  `invalid_input`) so dashboards or automation don't have to parse text.

**The live-model tolerances are assumptions, not evidence.** NOT VERIFIED:

| Metric | Live tolerance |
|---|---|
| `pass_rate` | 0.04 |
| `high_failures`, `medium_failures` | 1 |
| retrieval metrics | 0.03 |
| abstention rates | 0.06 |

- Only **one** comparable live run exists (Phase 6, n=1), so run-to-run variance has never been
  measured.
- "About one case of noise" is a reasonable starting assumption, but it isn't defensible as
  calibrated.
- Before approving a `rag_live` baseline, run the live RAG evaluation at least 5 times on one
  commit, record each run, and set each tolerance from the observed spread (e.g. the max−min
  range). Document that in `docs/quality-observability.md`.
- Until then, a live BLOCKED should be read as "investigate", not as a proven regression.

## 5. Baseline governance findings

| Check | Status | Evidence |
|---|---|---|
| Manifest hashes match the records | VERIFIED | `load_baseline` succeeds for all 5; the recomputed `record_hash` equals the manifest and audit `sha256` |
| The audit log is consistent | VERIFIED | 5 lines, one per manifest entry, with matching `run_id` / `file` / `sha256`, and `flags: []` for all |
| Immutable through the supported workflow | VERIFIED | Overwrite refused and earlier files kept (tests). **Not immutable against hand edits:** an editor can change a record *and* its manifest hash together. That's what Git review is for. RECOMMENDATION: a test that every manifest entry has a matching audit line. |
| Failed, seeded-defect, or regressing runs can't be approved by accident | **Mostly.** VERIFIED for BLOCKED, ERROR, seeded-defect, unexpected-skip and regressing-vs-current runs. **DEFECT D-8.1:** an internally inconsistent PASS record (one with a critical FAIL) is approved when no baseline exists yet (probe P1). Against an existing clean baseline it's correctly BLOCKED (P1b). |
| Approval requires explicit action | VERIFIED | Only `baseline approve`, which requires `--approved-by` and `--reason` |
| CI can't approve or replace | VERIFIED | `ci.yml` has no `baseline approve` or `--accept-` (test). CI writes only to `ci-artifacts/`. |
| Provenance is honest | VERIFIED | `approved_by` names Claude Code; `commit 2d28111` with `uncommitted_changes: true`; the pytest note records the 2 deselected tests. **Gap:** approval doesn't *enforce* a committed state (probe P7: `commit=None`, uncommitted, approved), although the docs tell people to commit first. RECOMMENDATION: reject uncommitted or unknown-commit records unless `--allow-uncommitted` is given and logged. |
| An identity change can hide metric drops | Gap | Probe P6: a new dataset plus 4 new high failures was approved with only `--accept-identity-change` (flags `["identity_change"]`). Because runs aren't comparable, no regression is computed. The record must still be PASS, so the release gates still hold. RECOMMENDATION: on an identity change, print the old and new metrics side by side and require the approver to confirm. |

**Baseline values vs reported results:** VERIFIED consistent.

| Baseline | Values |
|---|---|
| `custom_engine` | 33/33, 0/9 critical, 0/16 safety (Phase 7: 33/33) |
| `promptfoo` | 33/33 |
| `rag_retrieval` | 24/25, hit 0.9474, recall 0.9211, n=19 (Phase 7 CI: the same) |
| `rag_mock` | 17/25, 0/25 safety, over-abstention 2/19 |
| `pytest` | 701 tests, 689 passed, 12 expected skips, 0 unexpected |

For pytest, the full suite is 703 tests (691 + 12); the 2 baseline-existence tests were deselected,
as the record's note says.

### How you should review and re-approve the baselines (don't reuse mine)

1. **Fix D-8.1 to D-8.3 first.** D-8.2 changes the definition of `test_pass_rate`, so the current
   pytest baseline would be stale.
2. **Review the code, then commit Phase 8 without `reports/baselines/`** (or with it, to bootstrap
   CI, if you accept the provisional values). Push, and confirm the first GitHub run (section 7).
3. **Start a fresh history on the committed, clean tree.** Confirm `git status` is clean, then:
   ```powershell
   Remove-Item reports\history\quality_history.jsonl -ErrorAction SilentlyContinue
   $env:AIQL_EMBED_PROVIDER = "hashing"
   python -m ai_quality_lab.rag.evaluate --mode retrieval --embedder hashing --output reports/quality/rag-retrieval.json
   python -m ai_quality_lab.rag.evaluate --mode rag --llm mock-gullible --embedder hashing --output reports/quality/rag-mock.json
   npm run eval:promptfoo
   python -m pytest -q --junitxml=ci-artifacts/python-tests/junit.xml
   python -m ai_quality_lab.ci_summary skips --junit ci-artifacts/python-tests/junit.xml --out ci-artifacts/python-tests
   ```
   For the custom engine, avoid `>` until D-8.3 is fixed. Use
   `python -m ai_quality_lab.runner --json | Out-File -Encoding ascii reports/quality/custom-engine-report.json`
   (the content is ASCII), or download `custom-engine-report.json` from the CI artifact.
4. **Record each report** with `observability record --kind …`. Check that every record says
   `uncommitted_changes: false` and has your commit.
5. **Compare with the provisional baselines.** Results should be identical; any finding means
   something changed and must be explained before approval.
6. **Approve each type under your name:**
   `observability baseline approve --run-id <id> --approved-by "<your name>" --reason "Reviewed; replaces provisional baseline approved by Claude Code"`.
   No `--accept-*` flag should be needed. If one is, stop and investigate.
7. **Commit `reports/baselines/` in a pull request**, so the required check runs against the new
   baselines before the merge.

## 6. CI/CD findings

| Check | Status | Evidence |
|---|---|---|
| `observability` runs after the needed jobs | VERIFIED (structure) | `needs` = the 5 quality jobs, `if: always()` (test) |
| Artifacts produced and consumed correctly | VERIFIED (local simulation) | Every `run:` step of `ci.yml` executed in order: 8 records written, 19/19 checks met their expectations, gate PASS |
| A missing artifact → ERROR | VERIFIED | Failure path: record fails, compare ERROR, gate exit 2 |
| The intended committed baselines are used | VERIFIED | The default `--baselines reports/baselines`; test that every compared type has a manifest entry |
| The 3 demos must be BLOCKED, report-checked, one type each | VERIFIED | Test plus simulation (`report_problem: null`) |
| The gate depends on `observability`; a skipped or failed job isn't success | VERIFIED | `needs` includes it (existing test: the gate needs every job). A skipped or cancelled job → ERROR (Phase 7 gate tests). |
| No Ollama, paid APIs or judges | VERIFIED | The offline-workflow tests pass; no model calls in the job |
| Permissions | VERIFIED | Workflow-level `contents: read`; the job adds no permissions; `persist-credentials: false`; no secrets |
| Retention and summaries | VERIFIED (structure) | `ci-observability` is uploaded with `if: always()`, kept 14 days; the regression table is appended to the step summary; the gate summary has a "Regression vs baseline" line (test) |
| Expression, path and shell compatibility | VERIFIED by inspection; NOT VERIFIED on Ubuntu | Bash only; `record()` uses `\|\| status=1` followed by `exit $status`, which is safe under GitHub's `bash -e`. Paths are relative to the workspace, and the download layout matches the scripts. |
| **The workflow on GitHub Actions** | **NOT RUN** | Nothing pushed |

**Fragility note (VERIFIED by calculation):** CI's `test_pass_rate` will be 691/703 = 0.98293,
which rounds to **0.9829**, the same as the baseline's 689/701 = 0.98288. It passes only because
of 4-decimal rounding. With one more skipped test it would BLOCK; that's D-8.2.

## 7. Cross-platform findings

| Area | Status | Notes |
|---|---|---|
| Dataset fingerprint | VERIFIED locally (CRLF vs LF test) · NOT VERIFIED on Ubuntu | A hash of the parsed JSON, independent of line endings and formatting |
| Knowledge-base fingerprint | VERIFIED by code · NOT VERIFIED on Ubuntu | `parse_document` uses `splitlines()` plus `"\n".join`, and `documents_fingerprint` hashes content and metadata, not paths |
| JSON serialization and record hash | VERIFIED | Canonical JSON (`sort_keys`, compact). A CRLF baseline file still verifies (test). |
| Encoding | **DEFECT D-8.3** | Reports are read as strict `utf-8`; PowerShell 5.1's `>` writes a UTF-8 BOM (bytes `239,187,191`) → `record` exit 2. Linux is unaffected. |
| Newlines on append | VERIFIED | `newline="\n"`, with no `\r\n` in the history (test) |
| Paths | VERIFIED | Stored as POSIX (test); the baseline file name is a validated `run_id` |
| File locking | Not handled | JSONL append has no lock. One writer per file in CI and locally is the expected use. RECOMMENDATION: document it. |
| Case sensitivity | No risk found | `*.md` glob and fixed file names, unchanged from Phase 6 |
| Floating point | VERIFIED by inspection | Reports round to 4 decimals; comparisons use tolerance + 1e-9. Phase 7 CI matched Windows exactly. |

**Checklist for the first GitHub Actions run:**
1. All 7 jobs succeed, and `Quality gate + summary` is green.
2. The observability log shows 8 "recorded … status=…" lines: 5 PASS records plus 3 BLOCKED demo
   records.
3. `regression-vs-baseline` is PASS, and **every comparison says `comparable: yes`**. Any
   "not comparable: dataset_fingerprint / corpus_fingerprint changed" means the fingerprints differ
   between platforms. That's a defect to fix; don't approve around it.
4. The pytest comparison shows test_count 703 vs 701 and test_pass_rate 0.9829 vs 0.9829 (no
   finding).
5. All 3 regression demos are BLOCKED, and their status files have `report_problem: null`.
6. The `ci-observability` artifact contains `history.jsonl`, `regression.json` / `.md`, the 3 demo
   JSON files and `quality-report.md`. The records have `origin: github-actions`, and `commit` is
   the pushed SHA.
7. The gate's job summary has the "Regression vs baseline: PASS" line and lists D-6.1 to D-6.4.
8. Optionally, push the commit through a **pull request** rather than directly to `main`, so the
   required check gates a real merge. Direct pushes use the admin bypass.

## 8. Known-defect verification

| Defect | Status |
|---|---|
| **D-6.1** over-abstention | VERIFIED open. Listed in `known_defects.json`, the CI summary (test) and the quality report; tracked via `rag_live` `over_abstention_rate`. Measured 6/19 from the Phase 6 live report, matching the registry. |
| **D-6.2** unreliable judges | VERIFIED open. Ragas is advisory everywhere and can't block (test and probe). |
| **D-6.3** RAG-011 contradiction | VERIFIED open. `answerable_abstention_rate` 7/19 includes RAG-011; that's documented as hedging, not a fix. |
| **D-6.4** pattern-based security | VERIFIED open and newly registered. It cites the Phase 6 report §4. |
| Phase 7 residual risks (timeout classification, action SHA pinning, admin bypass) | VERIFIED listed in the Phase 8 completion report §10. They aren't defects in the registry; that's appropriate. |

**Do the reports separate each kind of result?**

| Kind of result | How it's shown |
|---|---|
| Deterministic results | Kind `deterministic` |
| Live-model results | Separate type `rag_live` with `deterministic: false`. **The Markdown report doesn't label `rag_live` as a live-model run in its tables;** it's only in the limitations text. RECOMMENDATION. |
| Advisory results | `*advisory*` on every value, plus a section label |
| Unmeasured metrics | "not measured (reason)" |
| Missing baselines | "no baseline", and the heading names the uncompared types; all-uncompared → ERROR |

## 9. Actual test results (executed in this audit)

| Run | Result |
|---|---|
| `python -m pytest -q` | **691 passed, 12 skipped.** Matches the completion report. |
| Skip audit on that run's JUnit | 12 skipped = 9 `test_llm_live` + 3 `test_rag_live`; **0 unexpected** |
| Local CI simulation (all `run:` steps of `ci.yml`) | 19/19 checks met their expectations, including `regression-vs-baseline` PASS and 3 demos BLOCKED; **gate PASS** |
| Failure paths | healthy PASS (0) · fail-open as normal BLOCKED (1) · −5 tests BLOCKED (1) · missing artifact ERROR (2) · malformed history ERROR (2) · missing manifest ERROR (2) · edited baseline ERROR (2) |
| Audit probes (new) | P1 inconsistent report approvable (D-8.1) · P2 local compare ERROR with 2 runs (D-8.4) · P3 false BLOCK (D-8.2) · P4 duplicate report accepted · P5 PowerShell BOM rejected (D-8.3) · P6 identity change hides metric drops · P7 uncommitted approval allowed · P8 integer rate accepted (D-8.5) · P11 high-severity swap → PASS · P12 commit and timestamp formats unchecked · P13 empty RAG report rejected (correct) |
| GitHub Actions, Ubuntu, live Ollama | **NOT RUN** |

**Do the tests validate the advertised behaviour?** Mostly, yes.
- The 107 observability tests use real reports (the runner and RAG evaluate in-process) as well as
  synthetic records. They cover the PASS, BLOCKED and ERROR paths, tolerance boundaries, the
  advisory enforcement, and tampering.
- The gaps are exactly the probe findings: there's no test for
  - a status/metric inconsistency
  - an added expected skip
  - a BOM-encoded report
  - a multi-run local history
  - an integer rate value

## 10. Defects discovered

| ID | Severity | Defect | Evidence | Recommended fix (not applied) |
|---|---|---|---|---|
| **D-8.1** | Medium | A record's `status` is copied from the report without checking it against the record's metrics. A report saying READY while containing a critical FAIL becomes a PASS record and **can be approved as a first baseline**. | Probe P1: record PASS with `critical_failures=1`, approved. Against the real baseline it's BLOCKED (P1b), so this is a governance hole, not a CI false PASS. | When building a record, force the status to BLOCKED (or reject the record) if `critical_failures > 0`, `error_count > 0`, or (for RAG) a gate failed, while the report claims READY. At approval, also require `critical_failures == 0` and `error_count == 0`. Add a test. |
| **D-8.2** | Medium | `test_pass_rate` = passed / *all* tests, including expected skips. Adding one opt-in live test, with nothing failing, lowers it, and with tolerance 0 that's **BLOCKED**. | Probe P3: 689/702 vs 689/701 → BLOCKED. CI currently passes only because of rounding (section 6). | Define it as passed / (tests − expected skips), or drop it, since `tests_failed` and `unexpected_skips` already cover the risk. Document the change and regenerate the pytest baseline. Add a test for "an added expected skip doesn't block". |
| **D-8.3** | Medium (Windows usability) | `record` rejects a UTF-8-BOM report, which is what the **documented** PowerShell command (`runner --json > reports/engine.json`) produces. | PowerShell probe: first bytes `239,187,191` → `Unexpected UTF-8 BOM` → exit 2. It fails safe (ERROR). | Read reports with `utf-8-sig` (the dataset fingerprint already does), and add a BOM test. Alternatively, change the docs to use `--output` or `Out-File -Encoding ascii`. |
| **D-8.4** | Low to medium (usability) | `compare` needs exactly one record per type, so the documented local workflow (record into the default history, then compare) returns ERROR from the second run onwards. | Probe P2: "2 records in this run; expected exactly one". It fails safe. | Add `compare --run-id …` or an explicit `--latest` (latest per type, named in the output), or document one history file per run, as CI does. |
| **D-8.5** | Low | An integer metric value skips the ratio-consistency check, so `pass_rate = 1` with 1/4 validates. | Probe P8 | Store a `unit` (`count` / `rate`) on each metric and validate by unit, not by Python type |

Gaps found by the probes (recommendations, not defects):

| Probe | Gap |
|---|---|
| P4 | a duplicate report isn't detected |
| P6 | an identity change can hide metric drops at approval |
| P7 | approval doesn't enforce a committed state |
| P11 | there's no case-level check for high severity |
| P12 | commit and timestamp plausibility aren't checked |

The other recommendations are an `error_category` field, a live-run label in the report, and an
audit-log consistency test.

## 11. Required actions before MVP sign-off

1. **Fix D-8.1, D-8.2 and D-8.3** (D-8.4 is strongly recommended), each with a regression test.
   Then re-run the full suite, the simulation and the failure paths.
2. **Regenerate the provisional pytest baseline** after the D-8.2 fix, still provisional and
   clearly labelled, so CI compares against the corrected metric.
3. **Rename this audit file** (or add a `.gitignore` exception) so it can be committed.
4. **Commit and push**, preferably through a pull request, and verify the first GitHub run against
   the checklist in section 7.
5. **Re-approve all 5 baselines yourself** from the committed, clean state (section 5), and commit
   them through a reviewed PR.

## 12. Optional improvements (should NOT block the MVP)

- **A live baseline:** a live-run variance study (≥5 runs on one commit), then set
  `live_tolerance` from evidence and approve a `rag_live` baseline.
- **Governance hardening:** reject uncommitted approvals unless a logged flag is given, require
  confirmation on an identity change, and detect duplicate reports.
- **Finer detection:** case-level detection for high severity, and an `error_category` field.
- **Persistent CI history:** a dedicated branch with scoped `contents: write`, after a permission
  review.
- **Phase 7 carry-overs:** pin actions to commit SHAs, and fix the timeout classification.

## 13. MVP readiness

1. **Does the project show a complete, credible AI quality lifecycle?** **Yes.** It covers:
   - deterministic system-under-test and guardrails
   - dataset-driven evaluation with severity-based release gates
   - a second independent engine (Promptfoo) with an agreement check
   - a real LLM with LLM-judge metrics treated honestly as advisory
   - RAG with retrieval metrics, security tests and failure classification
   - CI gates with PASS/BLOCKED/ERROR semantics and seeded failures that must stay detected
   - and now historical regression detection against governed baselines

   Its limits are documented rather than hidden.
2. **Is regression detection trustworthy enough to demonstrate?** **Yes for CI** (no false-PASS
   path found), **after D-8.1 to D-8.3 are fixed** for the governance and local workflow. Live-run
   regression detection should be presented as *provisional* until the tolerances are calibrated.
3. **Are the quality gates meaningful?** **Yes.** They gate on outcomes that have defined
   denominators. The demos prove detection still works on every run, and ERROR is never a pass.
   The main caveat to state openly is that CI gates protect the deterministic scenarios, not
   live-model quality.
4. **Is the observability architecture understandable and maintainable?** **Yes.** It's six small
   modules, stdlib only, with JSONL and committed JSON baselines; one documented data model; one
   rules file with reasons. The cost of that strictness: removing a test or changing a dataset now
   requires a deliberate baseline approval.
5. **Are the documented limitations acceptable for an MVP?** **Yes.** Mock stand-ins in CI,
   phrase-based abstention detection, uncalibrated live tolerances, and open D-6.x defects are all
   stated explicitly. That honesty is a strength.
6. **Is a browser dashboard needed?** **No.** With about 5 records per run, the Markdown tables
   carry all the information, render in the GitHub job summary, and diff in review. A dashboard
   would add rendering code and a new surface for misleading charts without adding a decision
   signal. Reconsider only once there's a persistent history with many runs to plot.
7. **What would a hiring manager or Principal Engineer challenge?**
   - "Your CI is green, so is the AI good?" *Answer:* no, it proves the deterministic scenarios;
     D-6.1 is the live counter-example.
   - "Who approved these baselines, and from what state?" That's why section 5 must be done.
   - "How did you choose the live tolerances?" Currently an assumption; see section 4.
   - "Why does the gate pass with 6 over-abstentions?" Known defects are tracked and visible, not
     gated, because they need a live model.
   - "What stops someone approving a regression?" The flags and the audit log, plus the D-8.1 and
     identity-change gaps.
   - The judge-calibration story (D-6.2), and why ERROR is distinct from BLOCKED.
8. **The three most important remaining improvements:**
   1. Fix D-8.1 to D-8.3 and re-approve the baselines under your own authority from a committed
      state.
   2. Validate on GitHub Actions/Ubuntu, especially that the fingerprints agree across platforms.
   3. Calibrate the live tolerances with repeated live runs, then approve a `rag_live` baseline so
      D-6.1 is regression-checked.

**Final MVP recommendation:** **READY WITH CONDITIONS.** The project can reasonably be called an
MVP once the actions in section 11 are complete: three focused defect fixes, one GitHub run, and
your own baseline approval. No new features are needed.
