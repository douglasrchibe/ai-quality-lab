# Three evaluation approaches: custom engine, Promptfoo, DeepEval

AI Quality Lab evaluates its chatbots three ways. This document compares them as an engineering
team choosing tools would. For the detailed custom-engine vs. Promptfoo comparison, see
[promptfoo-comparison.md](promptfoo-comparison.md). For the DeepEval metric rationale, see
[llm-evaluation.md](llm-evaluation.md). Measured results are in
[reports/phase_5_completion_report.md](../reports/phase_5_completion_report.md).

## What each approach does here

| | Custom Python engine | Promptfoo 0.125.1 | DeepEval 4.3.2 |
|---|---|---|---|
| **Evaluates** | `MockChatbot` (guardrail dataset, 33 cases) | `MockChatbot`, same 33 cases generated from the same JSON | `GuardedLLMChatbot` with a real LLM (LLM dataset, 27 cases) |
| **Oracle** | Exact, deterministic assertions | Deterministic assertions (`is-json`, `icontains`, JS checks) plus a consistency invariant | LLM-as-a-judge metrics, plus the custom engine's deterministic checks |
| **Release decision** | Severity-aware: BLOCKED on a critical FAIL, any ERROR, or zero cases | None built in: exit 100 on any failure or error | Same severity-aware policy (shared `decide_release_readiness`) |
| **Install** | Python standard library + pytest | Node.js ≥22.22 + npm package (613 packages) | Optional pip extra (~60 packages) + a judge model (local Ollama or a hosted API) |
| **Run time** | Milliseconds | Seconds | Minutes per case on a CPU-only laptop |
| **Determinism** | Fully deterministic | Fully deterministic (mock provider) | Not deterministic: both the answer and the judge vary |

## Assessment by criterion

| Criterion | Custom engine | Promptfoo | DeepEval |
|---|---|---|---|
| Configuration | Strict JSON schema, all problems reported together | YAML; tests generated from the shared JSON | JSON schema with consistency rules (e.g. context metrics need context) and env-based model settings |
| Failure reporting | Expected vs. actual plus an explanation per assertion | Per-assertion reason; web viewer; named metrics | Score, threshold and the judge's reason per metric; failed checks explained |
| Error handling | ERROR by stage, never a pass | `failureReason` 2; same exit code as failures | Model or judge failure = ERROR, never a pass; actionable setup errors (exit 2) |
| Reproducibility | Exact | Exact | Approximate. Temperature 0 and a seed reduce variation, but a 3B judge's verdicts vary and are often wrong (see report) |
| CI/CD suitability | Excellent: fast, free, exit codes 0/1/2 | Good: exit 100, JUnit XML output; needs Node.js | Poor as a hard gate today (slow, non-deterministic, unreliable small judge). Suitable as a non-blocking or scheduled job with a stronger judge |
| Maintainability | Small, fully owned, unit-tested | Thin adapter; tool upgrades may change formats (the results parser is pinned to 0.125.1's shape) | API changed between major versions (e.g. metric direction, `GPTModel` → `OpenAIModel`); keep pinned |

## Comparable scenarios across tools

- **Custom engine vs. Promptfoo, on identical cases:** 33/33 agreement in normal mode and 33/33
  in fail-open mode (both fail the same 16 cases). Agreement doesn't make them equivalent:
  Promptfoo also runs an invariant check, and the two normalize phrases slightly differently.
- **DeepEval vs. deterministic checks, on the real LLM:**
  - Deterministic checks catch what they can express exactly: blocked status, leaked recipe
    terms, word limits, required facts.
  - The judge is needed for meaning: whether the model refused, admitted uncertainty, or stayed
    faithful to the context.
  - The audit found the 3B judge unreliable, so **critical safety verdicts now have a deterministic
    backstop** and don't rest on the judge alone.
- **DeepEval isn't run on the mock, and Promptfoo isn't run on the real LLM.** Both are possible
  but would add little here. Judging canned mock strings tests the judge, not the chatbot. A
  Promptfoo suite for the LLM would duplicate the DeepEval dataset.

## Strengths, weaknesses, and enterprise use

**Custom engine**
- **Strongest at:** encoding the team's own release policy (severity, errors, empty runs) with
  exact, fast, free checks. It's ideal as the **blocking CI gate** for deterministic behaviour:
  guardrail configuration, routing, contracts.
- **Weakest at:** semantics. It can't judge meaning, tone or faithfulness, and every reporting
  feature has to be built in-house.
- **Enterprise use:** contract and regression tests for deterministic components around the model
  (guardrails, policy, schemas), owned by the team.

**Promptfoo**
- **Strongest at:** breadth and visibility. It has a declarative format non-programmers can review,
  a large assertion library, side-by-side comparison of providers and prompts, a web viewer,
  history, and JUnit output.
- **Weakest at:** decision policy. It has no severity concept, and errors share the failure exit
  code. It also adds a Node.js toolchain to a Python project.
- **Enterprise use:** prompt and model comparison, red-teaming, and shared eval suites reviewed by
  product and QA. CI integration through its exit code or JUnit output.

**DeepEval**
- **Strongest at:** semantic quality, using research-backed LLM-as-a-judge metrics (relevancy,
  faithfulness, hallucination, G-Eval rubrics, safety) in Python, the same language as the
  application.
- **Weakest at:** cost and trust. Every metric is another model call; verdicts depend on the
  judge's quality and need calibration against known answers, and they aren't reproducible.
  Upgrades change APIs.
- **Enterprise use:** scheduled quality evaluation of LLM features with a strong hosted judge,
  tracked as score trends with human review of failures. Blocking only on calibrated metrics.

## Should all three coexist in this project?

**Yes, with distinct roles and no duplicated datasets:**

1. **The custom engine is the policy layer and the deterministic gate.** Its
   `decide_release_readiness` is already shared with the DeepEval runner.
2. **Promptfoo is the tooling and visibility layer** for the deterministic suite. It reads the
   same JSON dataset, and `engine_comparison` flags any drift.
3. **DeepEval is the semantic layer** for real-LLM behaviour. Its verdicts are advisory until a
   judge passes calibration (`pytest -m live_llm -k calibration`).

The cost of coexistence is three toolchains to keep current. That's acceptable for a learning and
portfolio project whose purpose is to compare them. A production team would probably keep the
custom gate plus one of the two frameworks, chosen by language (Python means DeepEval) and by
whether non-developers author tests (which favours Promptfoo).
