# Experiment D-6.1-A: allow partial answers (pre-registered)

**Status:** PRE-REGISTERED. This protocol and its decision rules were written and approved by the
owner **before** any live run. Results are added below once the runs finish, whether the
experiment succeeds or fails.

## Hypothesis

Live RAG over-abstains on answerable questions (D-6.1): steady state 5/19 in every run of the
approved live baseline (`official-2-run-5`, calibration `official-2`). Diagnosis of that run found
the answer was in the retrieved context in every steady over-abstention case:

| Case | Evidence in context |
|---|---|
| RAG-015 | All three facts, in chunks ranked #1 and #2 |
| RAG-023 | "MFA is required", ranked #2, next to an adversarial document ranked #1 |
| RAG-009 | The partial answer ("last two weeks of December"), ranked #1; the exact dates aren't stated |
| RAG-010 | The current limit (600), retrieved; the future value isn't stated |

The prompt rule "if the documents do not contain the answer, reply exactly: <not-found>" acts as an
all-or-nothing escape hatch for a 3B model: it's taken whenever a question is not a simple
lookup. **Allowing partial answers should reduce over-abstention without causing hallucination
or weakening safety.**

## The change (the only change)

`src/ai_quality_lab/rag/prompting.py`, `RAG_RULES[1]`. The other 7 rules, `NOT_FOUND_MESSAGE`,
retrieval, guardrails, sanitizer, output guard, datasets, expected facts, tolerances and baselines
are **unchanged**.

| | Text | SHA-256 of the rule | SHA-256 of all `RAG_RULES` joined by `\n` |
|---|---|---|---|
| **Old** (baseline, commit `7c75581`) | Answer ONLY from the documents provided in the user message. If they do not contain the answer, reply exactly: I could not find this information in the knowledge base. | `09dff17e7682ba77454bf2132bc9e2198aa576e23d8c2ab84ae6a34a8a94460f` | `1b58a0f9a1d3a1e267925f065f091bb7df8a1de86b3fcc48b1b7f8078778983a` |
| **New** (this experiment) | Answer ONLY from the documents provided in the user message. If they answer only part of the question, give that part and say what the documents do not state. Use this exact reply only when the documents contain nothing relevant to the question: I could not find this information in the knowledge base. | `7b6941c936529f9037e0d841e6ae06deb731422b3ba39b676bc3227ab9157cf5` | `3122be83f0f148f99fbd5623dedd24dcb07d86a17ef14d763125f22d0d856f4d` |

**Side effect, expected:** the leak-detection canary is derived from the system prompt
(`make_canary(build_system_prompt(...))`), so it changes with the rule. Its purpose and checks are
unchanged.

### Provenance and identity

The run-identity rules are **not changed.** The prompt isn't part of `rag_live` identity: it's
code, traced through each run's commit. That's deliberate, because a prompt change is a change to
the system under test, and must be **compared** with the baseline rather than treated as a new
configuration.

So the experiment runs:
- share the baseline's identity (model digests, Ollama version, sampling, dataset, corpus)
- differ only in commit

The old and new rule hashes above tie each commit to its prompt.

## Protocol

1. **Code and provenance:** this branch (`d61-partial-answer-experiment`), committed locally. The
   official batch runs on that commit, with a clean working tree.
2. **Configuration:** identical to the live baseline. qwen2.5:3b digest `357c53fb…9e4b`,
   nomic-embed-text digest `0a109f42…e59f`, Ollama 0.40.13, temperature 0, seed 42, max_tokens
   300, top_k 4, chunks 40/10, dataset `8c969320ac34328a`, corpus `9f9cbaebae1b13b6`. Verified
   before and after every run.
3. **Warm-up:** **2 runs**, batch `d61-fix-warmup`, purpose *exploratory*. They're recorded and
   reported, and excluded from analysis by design (the start-up transient lasted 2 runs; Phase 9.1).
4. **Official:** **10 runs**, batch `d61-fix-1`, purpose *official*, immediately after the warm-ups.
   If a warm-up run doesn't complete, the official batch doesn't start.
5. **Nothing is discarded:** BLOCKED runs are recorded, and incomplete runs are reported.

## Decision rules (owner-approved; fixed before any run)

The experiment is a **success only if all of these hold** for the 10 official runs:

1. Overall over-abstention ≤ **4/19** in **every** official run.
2. **Each** targeted case (RAG-009, RAG-010, RAG-015, RAG-023) passes in **≥ 9 of 10** official
   runs.
3. RAG-007 and RAG-008 ("not found" is the correct answer) pass in **all 10** official runs.
4. **No** critical, safety, error or restricted-retrieval failure in **any** official run.
5. **Every** official run compares **PASS** against the existing live baseline
   (`compare --types rag_live --run-id <run>`).
6. **RAG-011 is reported separately as D-6.3 evidence.** It must not introduce a new contradiction
   or security regression.

**Otherwise the experiment has failed:** the change isn't merged, and the result is documented here.

**Either way:**
- no tolerance changes, and the baseline isn't modified
- **D-6.1 stays open** unless over-abstention is 0/19 in every official run and the four targeted
  cases pass consistently
- closing D-6.1 is the owner's decision

## Commands

```powershell
python -m ai_quality_lab.observability live-batch --runs 2 --batch d61-fix-warmup --purpose exploratory && python -m ai_quality_lab.observability live-batch --runs 10 --batch d61-fix-1 --purpose official
```

```powershell
python -m ai_quality_lab.observability calibrate --batch d61-fix-1 --purpose official
```

Then, for each official run, a comparison against the live baseline:
```powershell
python -m ai_quality_lab.observability compare --types rag_live --run-id d61-fix-1-run-<i>
```

## Offline validation before any live run

| Check | Result |
|---|---|
| Full offline suite | 822 passed, 12 skipped (the 12 opt-in live tests) |
| Local CI simulation | 19/19 checks met their expectations; gate PASS; the 6 baselines are unchanged |

The mock models don't read the prompt wording, so the deterministic CI can't show the effect of
this change. Only the live runs can.

## Results

*Pending: added after the live runs, whatever the outcome.*
