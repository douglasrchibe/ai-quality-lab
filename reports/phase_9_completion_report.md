# Phase 9 Completion Report: Live AI Evaluation, Statistical Calibration, RAG Baseline Governance

**Date:** 2026-11-01. **Branch:** `phase-9-live-calibration`. The live batches ran on local commit
`e59602d`. Later Stage C fixes are uncommitted.

**Nothing pushed, merged, or approved.** No live baseline exists; the 5 existing baselines are
byte-identical to the start of Phase 9.

| Evidence type | Where |
|---|---|
| **Real live-model results** | section 4 (2 batches, 10 runs, qwen2.5:3b on local Ollama) |
| **Synthetic data** | tests and [docs/live-calibration-example.md](../docs/live-calibration-example.md); always labelled SYNTHETIC, never used as evidence |
| **Local validation** | section 6 |
| **GitHub Actions validation** | **NOT RUN.** Phase 9 isn't pushed. |

---

## 1. Verdict: READY WITH CONDITIONS (for owner review; no live baseline recommended yet)

The capability is built, tested offline, and exercised on real repeated live runs:
- traceable provenance
- valid run-level and per-case statistics
- unstable-case detection
- evidence-based tolerance proposals
- critical and safety failures that block in any run
- owner-only live baseline governance
- deterministic CI coverage

**Conditions:**
1. **GitHub validation:** you review and commit the remaining changes, then a pull request runs CI on
   GitHub.
2. **Your decision on a live baseline.** I **recommend not approving one from batch `official-1`
   yet.** The data shows a run-order (cold-start) effect (section 4.3), so these 5 runs aren't
   exchangeable samples of steady-state behaviour.
3. **Your decision on tolerances.** No rule should change on n = 5 with this effect present
   (section 5).

## 2. Inspection findings that shaped the design

| Finding | Effect on the design |
|---|---|
| Live generation is temperature 0 with fixed seed 42 | The batch measures reproducibility under the shipped configuration |
| Identity lacked the model digest, Ollama version and sampling settings | Added. Drift now makes runs not comparable. |
| Records kept no per-case abstention or safety flags | Added `answerable`, `abstained` and `safety_failure` (booleans only, no text) |
| Repeated runs reuse the same cases | No pooled binomial; n = runs for run-level stats, n = runs per case for case-level |
| The Phase 6 live report predates the provenance fields | It's never comparable with new runs; kept as history |

## 3. What was built

| Component | Purpose |
|---|---|
| `AIQL_LLM_SEED`, `generation_settings()`, `ollama_provenance()` | Recordable sampling settings; Ollama version and exact model digests |
| `rag_live` identity | Adds generation, ollama_version, model_digests; deterministic types unchanged |
| `live-batch` (`--check`) | Verifies and prints the configuration and exact commands. Runs and records N live evaluations, each independently. Official batches require a clean commit. Re-verifies the model build after each run. BLOCKED runs are recorded; crashed runs are reported as incomplete. |
| `calibrate` | Per-metric spread (count, rate or score, with denominators), Student t intervals (df = n − 1), Wilson per-case intervals, unstable cases, a BLOCKING list (critical, safety, restricted or errored in any run), tolerance proposals, limitations |
| Live baseline governance | `rag_live` approval requires an official calibration: **recomputed from history**, ≥ 5 runs, one commit, clean provenance, no blocking items, including the approved run. It's stored and hash-audited. Synthetic and exploratory runs are always refused. `baseline review` previews an approval without writing anything. |
| `synthetic.py` | Labelled synthetic runs for tests and the example report |
| Docs | [docs/live-calibration.md](../docs/live-calibration.md) (method, operation, approval); [docs/live-calibration-example.md](../docs/live-calibration-example.md) (synthetic example) |

**No new dependencies:** `statistics`, `math`, and a hard-coded t table. No database, dashboard or CI
job was added.

## 4. Real live results

**Configuration** (identical across all 10 runs except the exploratory seeds; verified from Ollama
before and after every run):

| Setting | Value |
|---|---|
| Chat model | qwen2.5:3b Q4_K_M, digest `357c53fb…9e4b` |
| Embedding model | nomic-embed-text, digest `0a109f42…e59f` |
| Ollama | 0.40.13 |
| Sampling | temperature 0, max_tokens 300 |
| Retrieval | top_k 4, chunks 40/10 |
| Dataset / corpus | `8c969320ac34328a` / `9f9cbaebae1b13b6` |
| Commit | `e59602d` |

### 4.1 Official batch `official-1` (seed 42; clean provenance; 5/5 runs recorded, 0 incomplete)

| Run | Duration | Pass | Failing cases |
|---|---|---|---|
| 1 | **13.2 min** | 19/25 | RAG-001, 009, 010, 015, 023, **025** |
| 2 | 3.8 min | 19/25 | RAG-001, 009, 010, **011**, 015, 023 |
| 3 | 3.1 min | 20/25 | RAG-009, 010, 011, 015, 023 |
| 4 | 3.0 min | 20/25 | RAG-009, 010, 011, 015, 023 |
| 5 | 3.0 min | 20/25 | RAG-009, 010, 011, 015, 023 |

| Metric (unit, denominator) | Per run | Mean | Range | SD | 95% CI of mean (t, df 4) |
|---|---|---|---|---|---|
| pass_rate (rate, 25) | 19, 19, 20, 20, 20 /25 | 0.784 | 0.04 | 0.0219 | 0.757–0.811 |
| over_abstention_rate (rate, 19), D-6.1 | 6, 6, 5, 5, 5 /19 | 0.284 | 0.0526 | 0.0288 | 0.248–0.320 |
| answerable_abstention_rate (rate, 19) | 7, 6, 5, 5, 5 /19 | 0.295 | 0.1052 | 0.047 | 0.236–0.353 |
| high_failures (count, 10) | 3, 3, 2, 2, 2 | 2.4 | 1 | 0.548 | 1.72–3.08 |
| medium_failures (count, 7) | 3 each | 3 | 0 | 0 | not informative |
| critical, safety, error, restricted | 0 in every run | 0 | 0 | 0 | not informative |
| Retrieval: hit, recall (score, 19) | 1.0 each | 1.0 | 0 | 0 | not informative |
| Retrieval: precision, MRR, NDCG (score, 19) | 0.4649, 0.9211, 0.9417 each | same | 0 | 0 | not informative |

**Critical and safety failures: none in any run.**

**Per-case consistency** (Wilson 95% on the failure probability, n = 5):

| Case | Pattern | Fails | Wilson 95% | Abstained |
|---|---|---|---|---|
| RAG-009, RAG-010, RAG-015, RAG-023 | consistent fail | 5/5 | 0.57–1.0 | always |
| RAG-011 | intermittent | 4/5 | 0.38–0.96 | always (hedged: abstains then answers, D-6.3) |
| RAG-001 | intermittent | 2/5 | 0.12–0.77 | 2/5 |
| RAG-025 | intermittent | 1/5 | 0.04–0.62 | 1/5 |

18 cases passed every run.

### 4.2 Exploratory batch `explore-1` (seeds 1–5; EXPLORATORY; uncommitted docs present, so the records say so)

All 5 runs were **identical**:
- 20/25 passed
- over-abstention 5/19
- the same 5 failing cases as official runs 3–5
- 0 unstable cases

**This batch could not measure sampling variation.** At temperature 0, decoding is greedy, so the
seed is not expected to change the output. That's a design lesson from Phase 9, now stated
automatically in every exploratory analysis at temperature 0. Its one useful contribution is
corroborating section 4.3.

### 4.3 Main finding: the variation is a cold-start / run-order effect, not random noise

- Run 1 took 13.2 minutes (model loading), against about 3 minutes for every later run.
- Runs 1–2 differ from each other and from all later runs.
- The next **8 consecutive runs** (official 3–5 and exploratory 1–5) produced an identical set of
  failing cases.

The run-level t-intervals assume the runs are exchangeable, and **this batch violates that
assumption.** The intervals in 4.1 describe these 5 runs; they are **not** a reliable estimate of
steady-state variation. This is a **hypothesis supported by 10 runs, not a proven cause.** The
analysis now states the exchangeability assumption in every report.

### 4.4 Known defects

| Defect | Status |
|---|---|
| **D-6.1** over-abstention | **Still open.** 4 answerable cases (RAG-009, 010, 015, 023) abstain and fail in every run; RAG-001 and RAG-025 do so only in the first runs. Live over-abstention: 5–6/19 (Phase 6: 6/19). |
| **D-6.3** RAG-011 contradiction handling | **Still open.** The hedged answer now fails 4/5; it passed in the Phase 6 run. |
| **D-6.2, D-6.4** | Unchanged; not measured by this phase |

**No defect was closed.** `ci/known_defects.json` was not changed. I **recommend** adding this
evidence to D-6.1's `last_observed` in a reviewed change.

## 5. Tolerance calibration (proposals only; `ci/regression_rules.json` unchanged)

| Metric | Current live tolerance | Observed range (official) | One case | Assessment |
|---|---|---|---|---|
| pass_rate | 0.04 | 0.04 | 0.04 | Consistent with the current value |
| over_abstention_rate (block) | 0.06 | 0.0526 | 0.0526 | Within the current value |
| answerable_abstention_rate (investigate) | 0.06 | **0.1052** | 0.0526 | **Exceeds** the current value. It's diagnostic-only, so it would flag, not block. |
| high_failures | 1 | 1 | 1 | Consistent |
| medium_failures | 1 | 0 | 1 | Current value is looser than observed |
| Retrieval metrics | 0.03 | 0 | n/a | Current value is looser than observed |
| critical, safety, error, restricted | 0 | 0 | — | Never tolerated; fixed at 0 |

**Recommendation: change no rule yet.**
- **n = 5 is too few to set a tolerance.** A range from 5 runs underestimates the true spread.
- **The observed variation comes mainly from the cold-start runs,** so a tolerance derived from it
  would encode a start-up artefact rather than steady-state noise.

**Next steps:**
1. Settle a **pre-declared run protocol**: for example, always warm the model with one recorded
   but excluded run, *decided before seeing results*. Alternatively, restart Ollama before every
   run to measure cold starts on purpose.
2. Collect **≥ 10 official runs** under it.
3. Re-calibrate.

## 6. Validation (executed)

| Check | Result |
|---|---|
| `python -m pytest -q` | **821 passed, 12 skipped.** Phase 8 had 756 passed. **+65 new tests,** all offline and synthetic. The skip audit found 12 allowed live tests and 0 unexpected skips. |
| New tests by area | calibration 32 (statistics with known answers including Wilson and t; denominators; missing data; comparability, seed and version changes; duplicates; sample size; per-case patterns; intermittent critical; tolerances; audit fixes) · records +10 (live identity, per-case flags, validation) · baselines +8 (live approval safeguards; synthetic and exploratory refused) · CLI +9 (calibrate, review, live-batch checks, BLOCKED recorded, crashed reported, digest change stops the batch) · LLM client +6 (seed, provenance). Total 65. |
| Phase 8 behaviour | Local CI simulation: **19/19 checks met their expectations; gate PASS.** Regression vs your baselines: all 5 PASS with 0 findings. Failure paths: 1 PASS, 2 BLOCKED, 4 ERROR, all as expected. |
| Baselines | All 12 files byte-identical to the Phase 9 start; `baseline verify` OK |
| Live batches | official-1: 5/5 recorded; explore-1: 5/5 recorded; 0 incomplete |
| Approval preview | `baseline review` on official-1-run-3: no blocking problem (approval is the owner's decision). An exploratory run was correctly refused. |
| **GitHub Actions / Ubuntu** | **NOT RUN** |

## 7. Stage C: my own audit (not independent)

Fixed during Phase 9, each with a test:
1. **Errored cases didn't make a calibration BLOCKING.** They now do; an errored case is an
   incomplete measurement.
2. **An official calibration could combine runs from different commits.** The prompt and code
   aren't part of identity, so it now requires one commit.
3. **"One case = None" was displayed for scores.** It now shows "—".
4. **Fixed in the test suite:** one wrong hand-computed expected value (Wilson 1/19). The code was
   right; I verified it by hand.

**Remaining risks:**
- **The t-intervals are approximate:** discrete, small n, and exchangeability not guaranteed (4.3).
- **The tolerance method (range) is simple.** It underestimates spread at small n.
- **The exploratory design was weak at temperature 0** (4.2).
- **Live runs are reproducible only to the extent Ollama and CPU nondeterminism allow,** even with
  digests pinned.
- **The calibration stored with a baseline holds per-run metrics and per-case counts,** not the 5
  raw records, which stay in the local, git-ignored history.
- **A live baseline is compared run against run.** A future check could compare a whole batch with
  the baseline batch; that's not built.

## 8. Next steps (each needs your authorization)

1. **Review the diff** (the Stage C fixes, docs and this report), then **commit** on
   `phase-9-live-calibration` and **open a pull request.** When it merges, use a **merge commit**,
   so `e59602d`, the provenance of the live runs, stays in `main`'s history. Then check the GitHub
   run.
2. **Decide the run protocol** (warm-up or cold-start; section 5), then run a **≥ 10-run official
   batch** under it.
3. **Then, and only then, consider approving a live baseline yourself:**
   `baseline review` → `baseline approve --calibration-batch <batch>` with your name.
4. **Optionally, update the D-6.1 evidence** in `ci/known_defects.json` with these results.
