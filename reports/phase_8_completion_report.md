# Phase 8 Completion Report: AI Quality Observability and Regression Detection

**Date:** 2026-11-01. **Branch:** `main`. **Base commit:** `2d28111` (Phase 7 HEAD, which
includes `15dc532`; verified with `git log`). Nothing was committed, pushed or tagged.

**Environment:** Windows 11, Python 3.12.1, Node 24. Every result below comes from a command run
in this session. Anything not executed is marked **NOT RUN**.

---

## 1. Outcome: READY WITH CONDITIONS

Implemented, all checks below were run locally, and every scenario gave the expected result.

**Conditions:**
1. **Not yet run on GitHub Actions.** In particular:
   - the new `observability` job
   - cross-platform agreement of the dataset and corpus fingerprints, which were designed to be
     line-ending independent and tested with CRLF/LF files, but not yet confirmed on Ubuntu
2. **The initial baselines were approved by Claude Code** (`approved_by` says so), from the
   uncommitted Phase 8 working tree. They take effect only if you review and commit
   `reports/baselines/`.
3. **No `rag_live` baseline exists.** Live-model regressions (D-6.1) can be trended locally, but
   there's no approved live baseline to compare against.

---

## 2. What was implemented

- **Historical records** (`observability/records.py`):
  - one validated JSON record per evaluation run, built from the reports the existing tools
    already write: custom engine, Promptfoo, RAG retrieval, mock RAG, live RAG, pytest, advisory
    Ragas
  - each record carries the UTC time, run ID, commit, branch, origin, CI run ID and
    `uncommitted_changes`
  - also: dataset and knowledge-base fingerprints, model and embedder identity, parameters,
    thresholds, status, metrics with numerator/denominator/kind, `unmeasured` with reasons, and
    per-case outcomes
- **History store** (`observability/history.py`): an append-only JSONL file. Reading is strict:
  - a malformed, invalid or duplicate line is an error, reported with its line number
  - nothing is ever silently skipped
  - it refuses to append to a corrupt file
- **Trend analysis** (`trend`, plus the report's trend section):
  - only comparable runs (same comparability key) are tabulated
  - latest vs previous comparable run
  - runs with another dataset or model are counted as "not combined"
- **Regression detection** (`observability/regression.py`, `ci/regression_rules.json`):
  - configurable direction, tolerance (deterministic and live) and action per metric
  - fixed in code: new critical failures always block, advisory metrics never block, missing data
    is an error, and ERROR wins over BLOCKED
- **Baseline management** (`observability/baselines.py`):
  - explicit approval only
  - immutable record files, a manifest with a hash per baseline, and an append-only audit log
  - guards against non-PASS, seeded-defect, regressing or non-comparable runs
- **Reporting** (`reporting.py`, replacing the Phase 1 placeholder): a Markdown quality report
  covering:
  - overall status, naming any evaluation type that wasn't compared
  - latest results, baseline comparisons and trends
  - findings with denominators
  - advisory metrics labelled
  - known defects with their tracked metric
  - limitations

  It renders in the GitHub job summary, so no HTML dashboard was built (rationale in
  `docs/quality-observability.md`).
- **CI:** a new `observability` job:
  - a regression check that must PASS against the committed baselines
  - three seeded-regression demonstrations that must stay BLOCKED
  - all four are cross-checked with `ci_check --report`
  - the final gate depends on the job, and its summary gains a "Regression vs baseline" line

## 3. Files

**Created:**
- `src/ai_quality_lab/observability/` (`__init__`, `__main__`, `records`, `history`,
  `regression`, `baselines`, `cli`)
- `ci/regression_rules.json`
- `docs/quality-observability.md`
- `reports/baselines/`: `manifest.json`, `audit.jsonl`, and 5 record files
- tests: `tests/test_observability_{records,history,regression,baselines,cli}.py`
- this report

**Modified (all additive):**

| File | Change | Why |
|---|---|---|
| `src/ai_quality_lab/runner.py` | `--json` output gains `simulated_failure` | Without it, a fail-open report couldn't be told apart from a normal run (and a record builder would have to guess) |
| `src/ai_quality_lab/rag/failures.py` | Public `is_abstention()` (wraps the existing `NOT_FOUND_SIGNALS` check) | Abstention is measured with exactly the evaluator's rule |
| `src/ai_quality_lab/reporting.py` | Placeholder → Markdown report rendering | The module planned for this phase |
| `src/ai_quality_lab/ci_summary.py` | "Regression vs baseline" result line; a defect without cases renders cleanly | CI summary visibility |
| `ci/known_defects.json` | D-6.1 gains `tracked_metric` (rag_live `over_abstention_rate`); **new D-6.4** (pattern-based security can miss paraphrased injection and plain-language disclosure; evidence in the Phase 6 report §4) | The Phase 8 prompt requires all four issues to stay visible; D-6.4 wasn't yet in the registry |
| `.github/workflows/ci.yml` | `observability` job; the gate `needs` it | CI integration |
| `tests/conftest.py` | `make_record` fixture | Shared synthetic records |
| `tests/test_ci_summary.py`, `tests/test_ci_workflow.py`, `tests/test_runner.py` | New tests only | No existing test was changed or removed |
| `README.md`, `CLAUDE.md`, `docs/ci-pipeline.md` | Phase 8 sections, layout, rules, milestone | Documentation |

**Unchanged:**
- every dataset (`evals/*.json`)
- every release threshold
- the guardrail and RAG logic
- the Phase 7 checks and demos

## 4. Architecture and design decisions

- **The existing reports are the source of truth.** Records are extracted from them, never
  re-scored, so observability can't disagree with the gates. Where an equivalent metric existed, it
  was reused: `failures_by_severity`, `critical_failures`, the retrieval metrics, the
  `no_restricted_retrieval` gate and `NOT_FOUND_SIGNALS`.
- **Identity versus parameters.** Changes to the dataset, knowledge base, model or embedder make
  runs **not comparable** (ERROR). Changes to `top_k`, chunking or `simulated_failure` are reported
  as parameter changes and *compared*. That's how a configuration change, or a seeded defect,
  shows up as a regression.
- **Platform-independent fingerprints and hashes:**
  - dataset fingerprint = a hash of the parsed JSON
  - corpus fingerprint = the existing `documents_fingerprint`
  - baseline hash = canonical JSON

  So a CRLF Windows checkout and an LF Linux checkout agree. Tested with CRLF/LF files;
  confirmation on Ubuntu is pending.
- **JSONL, no database.** The data volume is small, and the readers need ordered scans, not
  queries.
- **Durable comparison comes from committed baselines, not CI history.** CI history lives 14 days
  in an artifact, while the baseline that gates a release is reviewed in Git and never expires.
  Persistent CI history is proposed in the docs (a dedicated branch with scoped write permission)
  but not implemented, because it needs a permission change.
- **Two abstention metrics** (a measurement finding, not an assumption). In the Phase 6 live run, 7
  answers to 19 answerable questions contain a "not found" signal, but D-6.1 counts 6. The seventh
  is RAG-011: it abstains and then answers anyway (the D-6.3 muddle), so its checks pass. Hence:
  - `over_abstention_rate` = abstained **and** failed: 6/19, matches D-6.1, blocks
  - `answerable_abstention_rate` = any abstention signal: 7/19, investigate only

## 5. Metric definitions and denominators

These are defined in full in `docs/quality-observability.md`.

| Metric | Denominator |
|---|---|
| `pass_rate` | executed cases |
| `critical_failures` / `high_failures` / `medium_failures` | cases of that severity |
| `error_count` | executed cases |
| `safety_failures` | must-block guardrail cases; RAG: executed cases. Not measured for retrieval-only runs. |
| `restricted_retrievals` | executed cases |
| `doc_*` retrieval metrics | applicable questions (19) |
| `answerable_abstention_rate`, `over_abstention_rate` | answerable questions (19) |
| `test_count`, `tests_failed`, `expected_skips`, `unexpected_skips`, `test_pass_rate` | tests |
| `ragas_*` | cases scored. **Advisory.** |

**Missing data is never zero:**
- a retrieval-only run lists its safety and abstention metrics as `unmeasured`, with a reason
- a rate whose denominator is 0 goes into `unmeasured`, never a stored value

## 6. Regression thresholds

Every rule is in `ci/regression_rules.json`, each with its reason.
- **Deterministic runs:** tolerance 0 everywhere. They're exactly reproducible; Phase 7 CI matched
  local results exactly.
- **Live runs:** tolerances allow about one case of noise:

| Metric | Live tolerance |
|---|---|
| `pass_rate` | 0.04 (1 of 25) |
| `high_failures`, `medium_failures` | 1 |
| retrieval metrics | 0.03 |
| abstention rates | 0.06 (≈1 of 19) |

**Block:**
- case and test count
- pass rate
- critical, high, safety, error and restricted counts
- unexpected skips and test failures
- document hit rate and recall (the existing gates)
- over-abstention
- any new critical failure

**Investigate:**
- medium failures
- precision, MRR and NDCG
- hedged abstention

**Advisory, never blocking:** Ragas.

## 7. Baseline management behaviour

**Initial baselines** (`reports/baselines/manifest.json`):

| Type | Run ID | Key values |
|---|---|---|
| `custom_engine` | `initial-custom-engine` | 33/33 pass, 0/9 critical, 0/16 safety |
| `promptfoo` | `initial-promptfoo` | 33/33 pass |
| `rag_retrieval` | `initial-rag-retrieval` | 24/25 pass, hit 0.9474, recall 0.9211 (n=19) |
| `rag_mock` | `initial-rag-mock` | 17/25 pass, 0/25 safety, over-abstention 2/19 |
| `pytest` | `initial-pytest` | 701 tests: 689 passed, 12 expected skips, 0 unexpected |

All five were approved with the `baseline approve` command. Each one's reason states:
- that it comes from the uncommitted Phase 8 working tree on top of `2d28111`
- that its deterministic results are identical to Phase 7 CI run 41269262995
- that it includes the open defects

**About the pytest baseline:** it was recorded with the 2 baseline-existence tests deselected,
because they can't pass until the baseline exists. Its `test_count` is therefore 701, two below
the full suite's 703, and the baseline record says so. That only makes the rule more lenient by
those 2 tests, never stricter.

**Behaviour verified by tests:**
- explicit approval only
- earlier baselines preserved, with an audit trail
- rejected runs: BLOCKED, seeded-defect, unexpected skips, duplicate, unsafe run ID
- an incompatible run needs `--accept-identity-change`; a regressing run needs
  `--accept-regression`, and both flags are written to the audit log
- an edited, missing or corrupt baseline is ERROR
- a corrupt current baseline blocks a new approval
- a CRLF baseline still verifies
- CI never approves (tested)

## 8. Validation (all run in this session)

| Check | Result |
|---|---|
| `python -m pytest -q` | **691 passed, 12 skipped** (the 12 opt-in live tests). Phase 7 had 577 passed and 12 skipped. 114 new tests. |
| New tests by file | records 35, history 8, regression 31, baselines 18, CLI and reporting 15, ci_summary +2, ci_workflow +3, runner +2 |
| Existing tests | None changed or removed. All 577 still pass. |
| Full CI workflow simulated locally (every `run:` step of `ci.yml`, in job order) | All 19 checks met their expectations: the 15 Phase 7 checks plus `regression-vs-baseline` PASS and 3 regression demos BLOCKED. Gate **PASS**. |
| Phase 7 behaviour | pytest, skip audit, custom engine 33/33, fail-open demo BLOCKED, Promptfoo 33/33, Promptfoo demo exit 100, engine agreement in both modes, RAG retrieval and mock PASS, both RAG demos BLOCKED, `pip check`, Ragas isolation: all as before |
| Deliberate critical regression (fail-open report as the normal custom-engine result) | regression check **BLOCKED** → gate exit **1** |
| Fewer tests (−5) | **BLOCKED** → gate 1 |
| Missing artifact (rag-mock report) | record fails, compare **ERROR** → gate exit **2** |
| Malformed history | **ERROR** → gate 2 |
| Missing baseline manifest (copy) | **ERROR** → gate 2 |
| Edited baseline (copy; hash mismatch) | **ERROR** → gate 2 |
| Advisory judge collapse (Ragas means ×0.3 vs a scratch baseline) | 4 findings marked `advisory`, status **PASS** (exit 0) |
| Phase 6 live report recorded locally | `over_abstention_rate` 6/19 (= D-6.1) and `answerable_abstention_rate` 7/19 (adds RAG-011) |
| **GitHub Actions run** | **NOT RUN.** Nothing was pushed. |
| HTML dashboard | Not built (Markdown report inspected instead) |

Every failure scenario used copies in the scratch directory. `reports/baselines/` was not modified
by any of them; `git status` confirmed this afterwards.

## 9. GitHub Actions changes

- **New `observability` job:** `needs` the five quality jobs, `if: always()`, read-only, no
  secrets, `persist-credentials: false`. Its steps:
  1. installs the project (locked)
  2. downloads the artifacts
  3. records each report independently
  4. `regression-vs-baseline` must PASS
  5. three demos must stay BLOCKED: `regression-demo-fail-open`,
     `regression-demo-defenses-disabled`, `regression-demo-retrieval-degraded`
  6. writes `quality-report.md` and appends the regression table to the job summary
  7. uploads `ci-observability`, even after failures
- **`quality-gate`** now also `needs` `observability`. The existing tests enforce that the gate
  needs every job.
- **No live tests, no models, no judges** were added. The workflow tests forbid them.

## 10. Known defects still open

Shown in every CI summary and quality report:
- **D-6.1 (high):** live RAG over-abstention, 6/19. Now tracked as `rag_live`
  `over_abstention_rate`.
- **D-6.2 (medium):** unreliable local judge scores; DeepEval and Ragas stay advisory.
- **D-6.3 (medium):** contradictory-policy muddling in RAG-011 is invisible to the deterministic
  checks. It's partly visible in `answerable_abstention_rate`.
- **D-6.4 (medium, newly registered):** pattern-based security controls can miss paraphrased
  injection and plain-language disclosure.

Phase 7 risks carried forward: timeout classification, action SHA pinning, and the admin bypass.

## 11. Remaining risks

1. **Unverified on GitHub/Ubuntu.** That covers the fingerprints, baseline hash verification on an
   LF checkout, and the job wiring. A fingerprint mismatch would show as ERROR (not comparable),
   never as a false PASS.
2. **Baseline provenance.** The initial baselines come from an uncommitted tree and were approved
   by Claude Code. Re-approving them on the committed state, under your own name, would make the
   audit trail cleaner.
3. **Abstention detection is phrase-based.** Paraphrased abstentions are missed.
4. **Live tolerances are judgements** (about one case). Live noise could exceed them; the docs say
   to investigate before approving.
5. **CI history expires after 14 days.** Long-term trends are local only; a persistent store is
   proposed but not built.
6. **`test_count` in the pytest baseline is 2 below the full suite** (explained in section 7).
7. **Deleting a test** now blocks CI until the pytest baseline is deliberately re-approved. That's
   intended, but a new maintenance step.

## 12. Recommended next steps

1. **Review the diff.** In particular, check `reports/baselines/` against this report.
2. **Commit, push and check the first run** of the `observability` job on GitHub.
3. **After the commit:**
   - optionally re-approve all five baselines from the committed state, under your own name
     (`--accept-regression` isn't needed when results are identical)
   - record the Phase 6 live report (`--commit unknown`) and approve a `rag_live` baseline
     deliberately, so D-6.1 is regression-checked on future live runs
4. **Then decide** whether to add persistent CI history (it needs a permission review).
