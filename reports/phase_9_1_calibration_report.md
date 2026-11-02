# Phase 9.1 Calibration Report: Warm-up + 10 Official Live Runs

**Date:** 2026-11-02. **Code:** `main` at `7c75581` (merged Phase 9), clean working tree.

**What changed:** nothing. No tolerances, baselines, rules or code were changed. This file and the
git-ignored run artifacts under `reports/live/` are the only outputs.

**All results below are real live-model runs on local Ollama.** Nothing here is synthetic.

## 1. Protocol (owner-approved, declared before execution)

- **1 warm-up run,** batch `official-2-warmup`, purpose *exploratory*. It's recorded, and excluded
  from analysis by design.
- **Then immediately 10 official runs,** batch `official-2`, one command with no idle gap.
- **The configuration was verified before and after every run:**
  - qwen2.5:3b, digest `357c53fb…9e4b`; nomic-embed-text, digest `0a109f42…e59f`
  - Ollama 0.40.13
  - temperature 0, seed 42, max_tokens 300
  - top_k 4, chunks 40/10
  - dataset `8c969320ac34328a`, corpus `9f9cbaebae1b13b6`
- **Execution:** warm-up 1/1 recorded; official 10/10 recorded; **0 incomplete, 0 stopped.** No
  critical, safety, error or restricted-retrieval failure in any run.

## 2. Results, every run

| Run | Duration | Passed | Failing cases |
|---|---|---|---|
| Warm-up (excluded) | 8.5 min | 19/25 | RAG-001, 009, 010, 015, 023, **025** |
| Official 1 | 4.4 min | 19/25 | RAG-001, 009, 010, **011**, 015, 023 |
| Official 2–10 (9 runs) | 3.1–3.9 min each | 20/25 each | RAG-009, 010, 011, 015, 023, **identical in all 9** |

## 3. Pre-declared analysis: the 10 official runs

`calibrate --batch official-2 --purpose official`:

| Metric (unit, denominator) | Per run | Mean | Range | 95% CI of mean (t, df 9) |
|---|---|---|---|---|
| pass_rate (rate, 25) | 19, then 20 ×9 /25 | 0.796 | 0.04 | 0.787–0.805 |
| over_abstention_rate (rate, 19), D-6.1 | 6, then 5 ×9 /19 | 0.2685 | 0.0526 | 0.257–0.280 |
| answerable_abstention_rate (rate, 19) | 6, then 5 ×9 /19 | 0.2685 | 0.0526 | 0.257–0.280 |
| high_failures (count, 10) | 3, then 2 ×9 | 2.1 | 1 | 1.87–2.33 |
| medium_failures (count, 7) | 3 each | 3 | 0 | not informative |
| critical, safety, error, restricted (counts) | 0 each | 0 | 0 | not informative |
| Retrieval (hit, recall, precision, MRR, NDCG; scores, 19) | unchanged | 1.0, 1.0, 0.4649, 0.9211, 0.9417 | 0 | not informative |

**Per case:**
- **Unstable: 1 of 25.** RAG-001 failed 1/10 (Wilson 95%: 0.02–0.40), in official run 1 only.
- **Consistent failures, 10/10, abstaining every time:** RAG-009, 010, 011, 015, 023.
- **19 cases passed every run.**

**Tolerance proposals** (observed range over 10 runs; **rules unchanged**):

| Metric | Proposed | Current live tolerance |
|---|---|---|
| pass_rate | 0.04 | 0.04 |
| over_abstention_rate | 0.0526 | 0.06 |
| answerable_abstention_rate | 0.0526 | 0.06 |
| high_failures | 1 | 1 |
| medium_failures | 0 | 1 |
| retrieval metrics | 0 | 0.03 |
| critical, safety, error, restricted | 0 | 0 (never tolerated) |

**All of the observed variation comes from official run 1.**

## 4. Main finding: a reproducible two-run start-up transient

Across both experiments, a run's outcome depends on its **position after the model loads**. The
pattern repeated exactly:

| Position after model load | official-1 (2026-11-01) | official-2 (2026-11-02) | Failing set |
|---|---|---|---|
| 1st run | run 1 (13.2 min) | warm-up (8.5 min) | 001, 009, 010, 015, 023, 025 (19/25) **identical** |
| 2nd run | run 2 | official run 1 | 001, 009, 010, 011, 015, 023 (19/25) **identical** |
| 3rd run onward | runs 3–5 | official runs 2–10 | 009, 010, 011, 015, 023 (20/25) **identical** |

The 5 exploratory runs (seeds 1–5), recorded after official-1's runs, also match the 3rd-run-onward
set. **That's 17 consecutive steady-state runs with the same failing cases.**

**Conclusions:**
- The run-to-run variation is **not random sampling noise.** It's a start-up transient covering the
  first **two** runs after a model load, and it reproduced across two separate loads and two days.
- **The one-run warm-up was not enough.** The pre-declared analysis keeps official run 1 anyway,
  because removing it now, after seeing the results, would be a post-hoc choice.
- **The cause isn't established.** It's plausibly server-side state after loading (for example,
  caches warming up). Confirming it would take a controlled experiment, such as restarting Ollama
  between runs.

### Post-hoc view (official runs 2–10): descriptive only, NOT used for any decision

- **Zero variation in every metric**, and no unstable cases.
- **For each case**, 0 changes in 9 runs bounds the chance of a change per run at ≤ 0.30 (Wilson
  95%).
- **Informally across all 17 steady-state runs** (they come from two commits, so the tool correctly
  refuses to combine them officially), ≤ 0.18.

## 5. Implications and recommendations (owner decisions; nothing applied)

1. **Run protocol.** Pre-declare **two** warm-up runs for future live batches, since the transient
   lasted two runs both times. Separately, decide whether live regression checks should measure
   steady state (with warm-up) or a cold start (what a freshly started service does).
2. **Tolerances: keep the current live values.** They already cover the observed one-case
   start-up difference:
   - pass_rate 0.04
   - over-abstention 0.06
   - high failures 1

   Tightening to the steady-state value of 0 would make any live check that starts cold BLOCK
   falsely. Loosening isn't supported by any evidence. No rule change is recommended.
3. **A live baseline is now supportable, if you choose:**
   - official-2 meets every approval requirement: 10 runs, one clean commit, no blocking items
   - a steady-state run (for example `official-2-run-5`, pass 20/25, over-abstention 5/19) would
     represent the stable behaviour
   - this would be your decision alone: `baseline review --run-id official-2-run-5 --calibration-batch official-2`,
     then `baseline approve` under your name
4. **D-6.1 evidence update** (recommended, not applied):
   - steady-state over-abstention is **5/19**: RAG-009, 010, 015 and 023 abstain and fail in every
     run, and so does RAG-011, which also carries the D-6.3 hedging
   - RAG-001 and RAG-025 over-abstain only during the start-up transient
   - D-6.1 stays **open**

## 6. Artifacts

These are git-ignored local files:
- `reports/live/official-2-warmup/run-1.json`
- `reports/live/official-2/run-1..10.json`
- `reports/live/official-2/calibration.md` and `.json` (the pre-declared analysis)
- `reports/live/official-2/posthoc-runs-2-10.json` (labelled post-hoc)
- `reports/live-official-2.log`

The 11 records are in the local history (`reports/history/quality_history.jsonl`). The existing 5
baselines are untouched and `baseline verify` reports OK.
