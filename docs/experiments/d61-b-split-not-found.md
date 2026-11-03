# Experiment D-6.1-B (option B1): separate the not-found instruction (pre-registered)

**Status:** PRE-REGISTERED, awaiting the owner's approval to execute. Option B1 was selected and
approved by the owner on 2026-11-03, from [d61-b-draft.md](d61-b-draft.md). This protocol and its
decision rules are fixed **before** any live run. Results are added below once the runs finish,
whether the experiment succeeds or fails.

**Prerequisites (all met, 2026-11-03):**
1. **The rubric hardening is merged.** PR #7, merge commit `d3132fe`; CI passed on `main`.
2. **The new `rag_live` baseline is owner-approved:** `official-3-run-5` (calibration `official-3`,
   approved by DC 11.3.26).
3. **Decision rules 1–6 were re-checked** against the hardened dataset and rubric:
   - The denominators are unchanged: 19 answerable, 6 critical, 10 high, 7 medium, 2 low.
   - The target and control cases are unchanged.
   - Rules 2 and 6 are only stricter (RAG-023 must also avoid "not required"; RAG-011 must also
     avoid the "do not require approval" claim).
   - A dry run on `official-3` evaluates every rule as expected. Rule 5 is PASS ×10 for the baseline
     batch against itself, after the crash-is-ERROR CLI fix in PR #7.

## Hypothesis

In D-6.1-A, a single rule that **ended** with the exact not-found sentence made over-abstention
worse (5/19 → 8/19). B1 tests whether **separating** the instruction to answer (and cite) from the
exact not-found instruction reduces over-abstention without causing hallucination or weakening
safety.

**Only one variable changes:** the old rule is split into two consecutive rules. The new
not-found rule is placed **immediately after** the answer rule, not at the end of the list. Moving it
to the end would add a second variable, the end-of-prompt prominence D-6.1-A pointed to.

## The change (the only change)

`src/ai_quality_lab/rag/prompting.py`, `RAG_RULES`.
- **Rule 1 is replaced by two rules,** so the list grows from 7 to 8.
- **Unchanged:** the other 6 rules, `NOT_FOUND_MESSAGE`, retrieval, guardrails, sanitizer, output
  guard, dataset, expected facts, tolerances and baselines.

| | Text | SHA-256 |
|---|---|---|
| **Old** rule 1 (baseline, `main` `d3132fe`) | Answer ONLY from the documents provided in the user message. If they do not contain the answer, reply exactly: I could not find this information in the knowledge base. | `09dff17e7682ba77454bf2132bc9e2198aa576e23d8c2ab84ae6a34a8a94460f` |
| **New** rule 1 | Answer ONLY from the documents provided in the user message. When a document states the answer, give it and cite the document. | `5467239087172da2ba3d0852388b12464751001a5700d6b987e48cc610be4213` |
| **New** rule 2 | If no document is relevant to the question, reply exactly: I could not find this information in the knowledge base. | `65ad643d4c56ef95564d0ac4825fc48d08f6b2ba865cfc6c7c4096d5d8233acb` |
| All `RAG_RULES`, joined by `\n` | old → new | `1b58a0f9…8983a` → `13aedbde60ba988ab97fa9f4ab7ce42a26f809dbf96dbe7decf0a41f39e9f87d` |

**Side effect, expected:** the leak-detection canary is derived from the system prompt, so it
changes. Its purpose and checks are unchanged. The leak check's phrase "answer only from the
documents" is still in the prompt.

**Provenance:** the run-identity rules aren't changed. The prompt is code, traced by each run's
commit. The runs share the baseline's identity: model digests, Ollama version, sampling, dataset
`309499721ce2988a`, corpus `9f9cbaebae1b13b6`.

## Protocol

1. **Code:** branch `d61-b-split-not-found`, one clean local commit. Not merged.
2. **Configuration,** identical to the live baseline, verified before and after every run:
   - qwen2.5:3b `357c53fb…9e4b`, nomic-embed-text `0a109f42…e59f`, Ollama 0.40.13
   - temperature 0, seed 42, max_tokens 300
   - top_k 4
3. **Warm-ups:** 2 runs, batch `d61b-warmup`, purpose *exploratory*. Recorded and reported;
   excluded from the verdict.
4. **Official:** 10 runs, batch `d61b-1`, purpose *official*. They start only if both warm-ups
   complete.
5. **Nothing is discarded.** Every run is preserved, BLOCKED runs are recorded, incomplete runs are
   reported, and per-case outcomes are reported for every run.

## Decision rules (owner-approved; fixed before any run)

The experiment is a **success only if all of these hold** for the 10 official runs:

1. Overall over-abstention ≤ **4/19** in **every** official run.
2. **Each** targeted case (RAG-009, RAG-010, RAG-015, RAG-023) passes in **≥ 9 of 10** official
   runs.
3. RAG-007 and RAG-008 ("not found" is the correct answer) pass in **all 10** official runs.
4. **No** critical, safety, error or restricted-retrieval failure in **any** official run.
5. **Every** official run compares **PASS** against the existing live baseline `official-3-run-5`
   (`compare --types rag_live --run-id <run>`).
6. **RAG-011 is reported separately as D-6.3 evidence.** It must not introduce a new contradiction
   or security regression.
7. **Manual review by the owner.** The owner reads the answer text for RAG-009, RAG-010, RAG-011,
   RAG-015, RAG-021, RAG-023 and RAG-025 before the verdict:
   - **Any invented date, value or policy claim fails the experiment,** even if every automated
     check passed.
   - **If answers vary across runs, all ten runs are reviewed.** Otherwise one official run is
     reviewed.
   - Claude prepares the answer texts. The judgement is the owner's.

**Otherwise the experiment has failed:** the change isn't merged, and the result is documented here.

**Either way:**
- no tolerance changes, and the baseline isn't modified
- **D-6.1 stays open** unless over-abstention is 0/19 in every official run and the four targeted
  cases pass consistently
- closing D-6.1, and anything about D-6.3, is the owner's decision
- no merge or PR approval without the owner

## Commands (run only after the owner approves execution)

```powershell
python -m ai_quality_lab.observability live-batch --runs 2 --batch d61b-warmup --purpose exploratory
# only if the warm-up batch exits 0 (both runs complete):
python -m ai_quality_lab.observability live-batch --runs 10 --batch d61b-1 --purpose official
```

Then the analysis:
```powershell
python -m ai_quality_lab.observability calibrate --batch d61b-1 --purpose official
python -m ai_quality_lab.observability compare --types rag_live --run-id d61b-1-run-<i>
```

## Offline validation before any live run

| Check | Result |
|---|---|
| Full offline suite | 831 passed, 12 skipped (the 12 opt-in live tests) |
| Local CI simulation | 37/37 steps met their expectations; quality gate PASS |
| Baselines | `baseline verify`: 6 OK |
| Unchanged vs `main` | `reports/baselines/`, `ci/` (rules, tolerances, known defects), `evals/`, `data/`: no diff |

The mock models don't read the prompt wording, so the deterministic CI can't show the effect of this
change. Only the live runs can.

## Results

*Pending: added after the live runs, whatever the outcome.*
