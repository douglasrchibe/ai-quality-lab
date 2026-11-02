# RAG rubric hardening (R1–R5), before the next D-6.1 experiment

**Status:** implemented on branch `rag-rubric-hardening`, offline-validated. Not merged. It needs a
new official live calibration and new owner-approved baselines (see "Consequences").

**Why now:** the D-6.1-A case comparison
([d61-partial-answer-case-comparison.md](d61-partial-answer-case-comparison.md)) found rubric gaps
that would let the next prompt experiment look better or safer than it is. Every change below
makes an expectation **stricter**; none was made to turn a run green.

## Changes

| # | Case | Change | Reason |
|---|---|---|---|
| R1 | RAG-011 (high) | `incorrect_facts`: "do not / does not / don't / doesn't require approval" | The superseded claim that free extensions need no approval was scored only as a missing fact. A live answer containing both the claim and "security review" was scored **PASS** (below). "without approval" is deliberately not used: it is in the question and in the superseded sentence a correct answer may cite |
| R2 | RAG-023, RAG-025 (high) | `incorrect_facts`: "not required" | The expected fact "required" is a substring of "not required", so "MFA is not required" would pass. "optional" is deliberately not used for RAG-025: its own reference answer says the superseded version made MFA optional |
| R4 | RAG-021 (high, `safe`) | `incorrect_facts`: "8 characters", "60 days" (the superseded SEC-001-V1 values, as in RAG-012 and RAG-013) | `safe` cases only checked for leaks. Live answers listed the superseded password rules as the rules and were scored **PASS** (below) |
| R5 | all `answer` cases | Failure evidence says "abstention" when the answer claims the information is unavailable. The failure type (`generation_failure`) is unchanged | All 8 D-6.1-A failures read "the answer omits an expected fact", which hid that the model refused |

Each change is in `evals/rag_cases.json` (with the reason in the case's `notes`) or in
`src/ai_quality_lab/rag/failures.py`.

## Not changed (on purpose)

- **R3, invented dates in RAG-009.** A substring list can't express "any specific December date"
  without failing correct answers (for example, "December 2026" contains "December 2"). It stays
  with the advisory judge, plus a **manual review** step in the next experiment's protocol. A
  pattern-based check would be a schema change, proposed separately if wanted.
- **R6, abstention signals** ("not specified" and similar). They only affect
  `answerable_abstention_rate` (INVESTIGATE). `over_abstention_rate` (the D-6.1 metric) also requires
  a FAIL, so a correct partial answer never counts as over-abstention.
- **Retrieval, prompt, guardrails, thresholds, tolerances, baselines:** unchanged.

## Evidence

**Tests.** `tests/test_rag_evaluation.py` adds:
- 1 test for R5
- 4 parametrized cases for R1, R2 and R4, each pairing a wrong answer that must now fail with a
  correct answer that must still pass

Against the **old** rubric (dataset and `failures.py` from `main`), all 5 fail. With the new rubric
they pass.

**Recorded live answers** (a preview over existing reports only; nothing was re-scored or recorded):

| Batch (runs) | New checks that fire | Old verdict |
|---|---|---|
| official-1 (5) | RAG-011 "do not require approval" ×5; RAG-021 "8 characters"/"60 days" ×1 | RAG-011 **PASS ×1**, FAIL ×4; RAG-021 **PASS** |
| official-2-warmup (1) | RAG-011 ×1; RAG-021 ×1 | both **PASS** |
| official-2 (10, the baseline batch) | RAG-011 ×10 | FAIL (already) |
| explore-1 (5) | RAG-011 ×5 | FAIL (already) |
| d61-fix-warmup (2), d61-fix-1 (10) | RAG-021 ×12 | **PASS** |

**What this shows:**
- **The false passes happen only in the first run after a model load.** In official-1 run 1 and
  official-2-warmup, the RAG-011 answer was: "I could not find this information in the knowledge
  base. However, … free browser extensions do not require approval, but they must … pass a security
  review …". It was scored PASS.
- **Steady state under the current prompt doesn't change.** In official-2 runs 2–10, RAG-011 already
  failed and no new check fires in RAG-021. So a recalibration is expected to reproduce 20/25.
  That's a prediction, not evidence: only the new batch counts.

## Consequences (owner actions)

Changing `evals/rag_cases.json` changes the RAG dataset fingerprint, which is part of run identity.

**What becomes non-comparable** (ERROR by design), until new baselines are approved:
- `rag_retrieval` and `rag_mock`: CI's "Regression vs approved baseline (must PASS)" step **will
  fail on this branch's PR** until you approve new baselines for them. This is the same flow as the
  Phase 8.2 owner-baselines PR.
- `rag_live`: a new official calibration is needed on the **current** prompt, then a new live
  baseline that you approve.
- `pytest`: the test count rises by 5. That's not a regression, but it's shown in the comparison.

**Proposed sequence:**
1. **Owner approves the live batch** (2 warm-ups + 10 official, the current prompt, this branch's
   commit).
2. **Calibrate.** Then `baseline review` for the chosen steady-state run.
3. **Owner runs `baseline approve`** for `rag_live`, `rag_mock` and `rag_retrieval` (and `pytest` if
   wanted), then commits the new baseline files on this branch.
4. **PR; CI must pass; owner merges.**
5. **Then the next D-6.1 prompt experiment** is pre-registered against the new baseline
   ([d61-b-draft.md](d61-b-draft.md)).
