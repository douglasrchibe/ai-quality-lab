# Phase 10 completion report: RAG rubric hardening and the D-6.1 prompt experiments

**Date:** 2026-11-03. **Status:** done, by owner decision.
- **Kept open:** D-6.1 to D-6.4 (the four known defects).
- **Paused:** D-6.1 prompt tuning.
- **Why this report exists:** Phase 10 was recorded after the fact, by owner decision, to give
  this work a phase of its own.

All live results below are real local-Ollama runs (qwen2.5:3b, nomic-embed-text, Ollama 0.40.13,
temperature 0, seed 42). Nothing is synthetic.

## 1. What Phase 10 delivered

| Item | Where | State |
|---|---|---|
| Pre-registered experiment **D-6.1-A** (partial-answer rule) | branch `d61-partial-answer-experiment` | **Failed, rejected,** not merged |
| Case-level comparison and diagnosis: prompt vs rubric | `docs/experiments/d61-partial-answer-case-comparison.md` | On `main` |
| **Rubric hardening** R1, R2, R4, R5 | PR #7 (`b608a0d`) | Merged |
| **Recalibration** on the hardened rubric (`official-3`) and new owner-approved baselines | PR #7 (`974b399`) | Merged; approved by DC 11.3.26 |
| **CLI fix:** a crash is ERROR (2), never BLOCKED (1) | PR #7 (`83f4146`) | Merged |
| Pre-registered experiment **D-6.1-B** (B1, separate not-found rule) | branch `d61-b-split-not-found` | **Failed, rejected,** not merged |

## 2. Results

| Run set (10 official runs each) | Prompt | Rubric | Passed | Over-abstention | vs baseline |
|---|---|---|---|---|---|
| `official-2` (earlier baseline) | original | original | 20/25 (19 in run 1) | 5/19 (6 in run 1) | baseline |
| `d61-fix-1` (D-6.1-A) | partial-answer rule | original | 17/25 | **8/19** | BLOCKED ×10 |
| `official-3` (current baseline) | original | hardened | 20/25 | 5/19 | baseline (`official-3-run-5`) |
| `d61b-1` (D-6.1-B) | split not-found rule | hardened | 18/25 | **6/19** | BLOCKED ×10 |

Each experiment had 2 recorded warm-ups (exploratory, excluded) and 0 incomplete runs. Both used a
clean commit, configuration verified before and after every run, and decision rules fixed before
any run.

## 3. Findings

1. **Prompt wording didn't fix D-6.1.**
   - Neither experiment changed the four targeted cases (RAG-009, 010, 015, 023). All still
     abstain, even though the answer is in the retrieved context.
   - Both experiments changed *which* simple lookups abstain: D-6.1-A broke RAG-001, 006 and 025;
     D-6.1-B broke RAG-025.
   - **Owner decision:** pause prompt tuning, and look at the model, the context, or the
     application layer next.
2. **The rubric had real false passes, now closed.**
   - Live runs had scored PASS on RAG-011 with a false policy claim, and on RAG-021 while listing
     superseded password rules.
   - The hardened rubric caught the same RAG-021 failure live in D-6.1-B, where the old rubric
     would have passed it.
3. **A tool bug could turn a crash into BLOCKED.** On Windows pipes, `observability compare`
   crashed on its own output symbols and exited 1.
   - It was found when the approved baseline "BLOCKED" against itself.
   - It's fixed, with tests that fail on the old code.
   - The D-6.1-A verdict was re-checked with correct output and still holds.
4. **The start-up transient and determinism.**
   - In steady state, answers were identical across runs, except one citation tag in RAG-003
     (1 of 10 runs, still PASS).
   - Two declared warm-ups were enough in every batch this phase.

## 4. Governance followed

- Every live batch ran only after the owner approved the verified configuration and commands.
- Every experiment was pre-registered. No result was discarded or re-scored.
- **Baselines** were approved only by the owner (DC 11.3.26, with `--accept-identity-change`
  recorded in the audit log). A read-only provenance review then passed 62 of 62 checks.
- **No tolerance or regression rule was changed.**
- **Evidence is preserved.** Each experiment branch has all 12 run reports, the calibration, the
  history records, the log and SHA256SUMS, under `docs/experiments/evidence/` (`d61a/`, `d61b/`).

## 5. Open items

- **D-6.1** (over-abstention, 5/19 at baseline) is open; prompt tuning is paused.
- **D-6.2, D-6.3 and D-6.4** are open. D-6.3 is mitigated for RAG-011's specific claim, not fixed.
- **Next:** Phase 11 (a sandboxed agent) is planned in `docs/phase-11-plan.md`, pending owner
  review.
