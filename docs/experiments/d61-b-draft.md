# Experiment D-6.1-B: DRAFT, not approved

**Outcome (2026-11-03):**
- **The owner selected B1.** It was pre-registered, run (2 warm-ups + 10 official runs), and
  **FAILED**: over-abstention 6/19 in every run, BLOCKED ×10 against `official-3-run-5`.
- **The owner formally rejected it.** The record and all evidence are on branch
  `d61-b-split-not-found` (`docs/experiments/d61-b-split-not-found.md`).
- **D-6.1 prompt tuning is paused.** B2 and B3 were not implemented and need separate approval.

The original draft follows, unchanged.

**Status: DRAFT for owner review.** Nothing here has been implemented. It becomes pre-registered
only when the owner approves it, **after** the rubric hardening
([rag-rubric-hardening.md](rag-rubric-hardening.md)) is merged and a new `rag_live` baseline is
approved. All comparisons are against that new baseline.

## What D-6.1-A taught us

- **Over-abstention got worse.** The partial-answer rule raised it from 5/19 to 8/19, and the
  4 targeted cases didn't change.
- **The new failures were the bare not-found sentence,** given to simple lookups (RAG-001, RAG-006,
  RAG-025) whose answers were in the context.
- **One hypothesis (unconfirmed):** the rule ended with the exact not-found sentence, which made it
  the most prominent instruction for a 3B model.

## Candidate changes (owner chooses one; one variable per experiment)

| Option | Change | Pros | Risks |
|---|---|---|---|
| **B1 (recommended)** | Prompt only. Split rule 2 in two: first "Answer ONLY from the documents… When a document states the answer, give it and cite the document." Then a **separate, later** rule: "If no document is relevant to the question, reply exactly: <not-found>." | Smallest change. Directly tests the "exact sentence dominates" hypothesis. Easy to revert | Could repeat D-6.1-A's result. RAG-007 and RAG-008 must keep answering not-found |
| B2 | Prompt only. Ask the model to answer from the most relevant document first, then mention what isn't stated | Targets the partial-answer cases (RAG-009, RAG-010) | A larger wording change (more than one variable). Invites invented details (R3) |
| B3 | Code. If the reply is exactly the not-found sentence, re-ask once using only the top-2 retrieved chunks | Doesn't depend on prompt wording | New behaviour and latency. Must not use thresholds tuned to these results. Bigger review |

## Protocol (carried over from D-6.1-A, plus one addition)

- **Batches:** 2 warm-ups (exploratory, excluded) plus 10 official runs, one clean commit, the same
  verified configuration.
- **Decision rules:** the D-6.1-A rules 1–6, unchanged, compared against the **new** baseline.
- **New, rule 7 (manual review):** before the verdict, the owner reads the answer text for RAG-009,
  RAG-010, RAG-011, RAG-015, RAG-021, RAG-023 and RAG-025 in one official run (all runs if answers
  vary). Any invented date, value or policy claim fails the experiment, even if every automated
  check passed. This covers what substring checks can't (R3).
- **Either way:** no merge, baseline or tolerance change without the owner. D-6.1 closes only by
  owner decision on the existing closure condition.
