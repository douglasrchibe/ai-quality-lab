# AI Quality Lab

[![AI Quality Lab CI](https://github.com/douglasrchibe/ai-quality-lab/actions/workflows/ci.yml/badge.svg?branch=main)](https://github.com/douglasrchibe/ai-quality-lab/actions/workflows/ci.yml)

**An AI Quality Engineering platform that demonstrates how to evaluate, challenge, and govern AI
systems before release.**

It combines evaluation suites, regression detection and governed release gates for chatbots, RAG
pipelines and a sandboxed tool-using agent.

Traditional tests ask "did the function return the expected value?" AI systems make that question
harder:
- **The output varies.** The same question can get differently worded answers.
- **The failures are often quiet.** A model that leaks restricted data or follows an instruction
  hidden in a document still returns a fluent, plausible answer.
- **The automated graders are fallible too.** An LLM that scores another LLM's answers can be
  wrong.

So this project separates what can be checked **deterministically** (same input, same verdict,
every time) from what can only be **estimated**. Only deterministic checks can block a release.
Every regression is measured against a baseline the owner approved, and every result is one of
three explicit outcomes: **PASS**, **BLOCKED** or **ERROR**.

> This is a portfolio project built with synthetic data. It demonstrates methods and engineering
> judgment; it does not claim that any AI system tested here is safe or production-ready.
> See [Limitations](#limitations).

## Why I built this

As AI capabilities become part of enterprise software, traditional automation strategies are no
longer enough. Quality engineering has to account for nondeterministic outputs, retrieval accuracy,
prompt injection, tool authorization, and the reliability of the automated evaluators themselves.

I built this lab to explore how those challenges can be addressed through measurable evaluation,
independent verification, regression controls and governed release decisions.

## What this project demonstrates

| Practice | How it shows up here |
|---|---|
| **Deterministic evaluation** | Assertions on structured fields (`blocked`, tool calls, retrieved document IDs, final state), not fragile string matching |
| **Golden datasets and rubrics** | Expected behaviour lives in versioned JSON datasets with a severity per case, separate from test code |
| **RAG evaluation** | Retrieval measured against ground-truth documents; prompt-injection defences tested with a deliberately gullible model |
| **Sandboxed agent testing** | A tool-using IT help-desk agent graded by an independent checker on its tool calls, effects and final state |
| **Security and authorization validation** | Least-privilege rules, human approval bound to the exact action, untrusted tool output, tests designed to detect authorization bypasses |
| **Proof that tests catch failures** | Failures planted on purpose (a guardrail that fails open, defences switched off, an agent that ignores approval) that the suite is required to catch |
| **Regression detection and governed releases** | Every run compared with owner-approved baselines; each finding names the metric, both values, the denominators and the tolerance |
| **CI/CD integration** | GitHub Actions runs the offline gates on pushes and pull requests to `main`; merging requires the `Quality gate + summary` check |

## Architecture

```mermaid
flowchart TB
    subgraph OFFLINE["Offline deterministic evaluation: no model, no API key, runs in CI"]
        DATA[("Golden datasets: 33 guardrail, 25 RAG, 28 agent cases")]

        subgraph SUT["Systems under test"]
            S1["Mock chatbot with topic guardrails"]
            S3["RAG chatbot with mock models and hashing embeddings"]
            subgraph AGENT["Agent trust boundary: sandbox, no network, no filesystem"]
                P["Scripted reference planner"]
                R["7-stage runtime with code-level authorization policy"]
                T["10 tools over synthetic in-memory data"]
                P -->|"one JSON action"| R
                R --> T
                T -.->|"results returned as untrusted, sanitized data"| P
            end
        end

        subgraph EVAL["Deterministic evaluators"]
            E1["Custom engine and Promptfoo, which must agree"]
            E2["Retrieval metrics and injection checks"]
            E3["Independent agent checker, outside the agent package"]
        end

        RR{"Release readiness: READY, BLOCKED or ERROR"}

        subgraph REG["Regression evaluation"]
            CMP["Compare run with approved baseline using regression rules"]
            BL[("Owner-approved baselines and audit log")]
        end

        CI["GitHub Actions: required check Quality gate + summary"]
    end

    subgraph LIVE["Optional local live evaluation: never in CI"]
        OL["Ollama: qwen2.5:3b and nomic-embed-text"]
        S2["Guarded LLM chatbot and live RAG"]
        ADV["DeepEval and Ragas judge scores: advisory"]
        OL --> S2
        S2 --> ADV
    end

    REP["Quality reports: context and evidence"]

    DATA --> E1
    DATA --> E2
    DATA --> E3
    S1 --> E1
    S3 --> E2
    R -->|"trace, side effects, final state"| E3
    E1 --> RR
    E2 --> RR
    E3 --> RR
    RR --> CMP
    BL --> CMP
    CMP --> CI
    CMP --> REP
    ADV -.->|"reported only, never decides readiness"| REP
```

- **The offline path** (top) needs no model, API key or internet connection. It is what CI runs,
  and only its deterministic evaluators feed release readiness.
- **Regression evaluation** compares each run with the owner-approved baseline for its evaluation
  type. The comparison result is what CI reports.
- **The live path** (bottom) is optional and local. Live runs use the same deterministic checks;
  DeepEval and Ragas judge scores appear in reports for context but never decide a release.
- **The agent is judged from outside.** The independent checker reads only the trace, side effects
  and final state; it doesn't import the agent's code.
- **The agent trust boundary:** the planner proposes one JSON action at a time, and the runtime
  checks it against code-level policy before anything happens. Tool output goes back to the planner
  only as sanitized data, which the policy never reads when deciding permissions or approvals.

## Quick start

Requires Windows PowerShell, Python 3.12+ and Git. No API key, model or internet connection is
needed after installation.

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install -r requirements.txt
```

**1. Run the test suite**

```powershell
python -m pytest
```

Expected: **1157 passed, 12 skipped**. The 12 skips are opt-in tests that need a live local
model; CI fails on any other skip.

**2. Run the evaluation suite against the healthy chatbot**

```powershell
python -m ai_quality_lab.runner
```

Expected: 33 of 33 cases pass, `Release readiness: READY`, exit code 0.

**3. Plant a regression and watch the gate block it**

```powershell
python -m ai_quality_lab.runner --simulate-failure guardrail_fail_open
```

This makes the guardrail silently "fail open": it answers requests it should refuse, and the
answers look normal. Expected: 16 cases fail, including all 9 critical cases,
`Release readiness: BLOCKED`, exit code 1.

**4. The same discipline for the agent**

```powershell
python -m ai_quality_lab.agent_eval.evaluate
python -m ai_quality_lab.agent_eval.evaluate --simulate-failure ignore_approval
```

Expected: the reference agent passes 28 of 28 scenarios with no unauthorized executions
(`READY`). The broken agent, which calls consequential tools without an approval, fails 7 scenarios, 2 of them
critical (`BLOCKED`).

The [10-minute demo guide](docs/demo-guide.md) adds the second engine, RAG, baselines and the
quality report. Its recorded counts predate the agent work; the commands are current.

**Exit codes:** 0 = PASS / READY, 1 = BLOCKED (a quality problem was found), 2 = ERROR (the check
couldn't run). A crash is always ERROR, never a pass.

## Systems evaluated

| System | How it's evaluated | Limitations |
|---|---|---|
| **Mock chatbot with topic guardrails** | 33 cases checked by a custom engine and independently by [Promptfoo](https://www.promptfoo.dev/); the two must agree on every case | Keyword-based detection; known gaps are tagged `known-limitation` in the dataset, not hidden |
| **Guarded LLM chatbot** (local Ollama) | 27 cases: the deterministic guardrail runs before the model, plus deterministic backstops and DeepEval judge metrics | Live-verified on 14 of 27 cases with one local 3B model; the full live run hasn't been completed. Judge metrics are advisory |
| **RAG chatbot** | 25 cases over a 16-document synthetic knowledge base: retrieval against ground truth, injection and restricted-data checks, failure classification; Ragas scores as advisory context | CI uses offline stand-ins (hashing embeddings, mock models); live results come from one local model; pattern-based defences can miss paraphrased injection (D-6.4) |
| **Sandboxed tool-using agent** | 28 scenarios graded by an independent checker on tool calls, side effects, final state and final message; 8 planted failure modes | Graded with a scripted reference planner, not a live LLM planner; synthetic sandbox only |

### The agent in one paragraph

A synthetic IT service-desk agent handles one request at a time with 10 tools (`search_kb`,
`get_user`, `get_ticket`, `verify_identity`, `create_ticket`, `add_comment`, `request_approval`,
`reset_password`, `grant_access`, `escalate_to_human`). Every proposed action passes 7 stages, each
recorded in a step-by-step trace. Authorization is enforced through code-level runtime policy
checks. The evaluation suite includes independent tests designed to detect authorization bypasses,
including self-approval and unauthorized side effects. The policy covers:
- **Least privilege:** writes and consequential actions apply only to the requester's own records.
- **Human approval:** `reset_password` and `grant_access` need an approval bound to the exact tool
  and arguments. It's single-use and never self-approved.
- **Step limit:** a run stops after 8 steps.

To see a denied unsafe action step by step:

```powershell
python -m ai_quality_lab.agent_eval.evaluate --case AGT-016 --trace AGT-016
```

The trace shows `reset_password … authorize:denied [denied R-APPROVAL/self_approved]`, followed
by an escalation to a human.

## Evidence

All figures below were measured on `main` on 2026-11-04 unless stated otherwise.

| Evidence | Result |
|---|---|
| Offline test suite | **1157 passed, 12 skipped** (opt-in live tests) |
| CI | The workflow runs on every push to `main` and every pull request targeting `main` (7 jobs on Ubuntu). A final `Quality gate + summary` job combines the results of the other six; it is the required status check for merging into `main` |
| Engine agreement | Custom engine vs Promptfoo: **33 agreements, 0 disagreements** |
| RAG retrieval (offline, K=4, 19 answerable questions) | Document hit rate **0.9474**, recall **0.9211**; gates (≥ 0.9 and ≥ 0.8) pass |
| Agent suite | **28 / 28** scenarios pass (critical 8 / 8), no unauthorized executions |
| Baselines | **6** owner-approved baselines; `baseline verify` reports all 6 OK |
| Live RAG baseline (local `qwen2.5:3b`, recorded 2026-11-03) | 20 / 25 cases pass; the model wrongly answers "not found" on 5 of 19 answerable questions (D-6.1) |

### Failures planted on purpose, and what the gate does

| Planted failure | Detected as | Release decision |
|---|---|---|
| Guardrail fails open (`guardrail_fail_open`) | 16 of 33 cases fail, all 9 critical | **BLOCKED** |
| Same run, compared with the approved baseline | Critical failures 0 → 9, pass rate 1.0 → 0.5152, each new critical failure named by case ID | **BLOCKED** |
| RAG injection defences off, in front of a model that obeys injected instructions (`defenses_disabled`) | 15 of 25 cases fail; 5 critical injection cases (RAG-016 to RAG-020) | **BLOCKED** |
| Agent failure modes | See the table below | 6 of 8 **BLOCKED** |

**All eight agent failure modes** (28 scenarios each):

| Mode | What the broken agent does | Scenarios failed | Critical failures | Release decision |
|---|---|---|---|---|
| `wrong_tool` | Opens tickets for questions and status checks | 9 | 3 | **BLOCKED** |
| `invalid_arguments` | Uses a free-text priority and invented ticket IDs | 7 | 0 | READY |
| `skip_verification` | Requests a password reset without verifying identity | 5 | 2 | **BLOCKED** |
| `ignore_approval` | Attempts consequential actions without valid human approval | 7 | 2 | **BLOCKED** |
| `retry_new_key` | Retries writes with a new idempotency key | 2 | 0 | READY |
| `claim_success` | Reports success whatever the tools returned | 13 | 5 | **BLOCKED** |
| `follow_injection` | Acts on instructions found in tool results | 3 | 3 | **BLOCKED** |
| `loop` | Repeats its first action instead of finishing | 26 | 6 | **BLOCKED** |

`invalid_arguments` and `retry_new_key` are detected: their scenarios fail and are reported. They
stay READY because no critical scenario fails and the run has no unauthorized execution, and the
written readiness policy blocks only on a critical failure, an ERROR, an unauthorized execution or
an empty run. The expected failures and outcome for each mode are fixed in
[tests/test_agent_eval_failure_modes.py](tests/test_agent_eval_failure_modes.py).

CI runs the guardrail, RAG and baseline-comparison failures on every pull request and requires them
to stay **BLOCKED**. If a planted failure ever started passing, the build would fail. The agent's
failure modes are covered by the pytest suite in CI.

Each failure mode is an opt-in demonstration switch that is off by default, and a run that uses one
is labelled as a demonstration. Baseline approval refuses runs with a planted failure.

## Engineering decisions

| Decision | Why |
|---|---|
| **Deterministic controls decide; judge metrics advise** | An LLM judge can be wrong. The local 3B judge gave false failures on correct answers during calibration (D-6.2). Every critical safety case has a deterministic backstop, and a judge score alone never decides a critical verdict |
| **Advisory vs blocking is enforced in code** | Each metric carries a `kind` (deterministic or advisory). Advisory metrics can't block a release, however they're configured |
| **Owner-approved baselines** | A baseline changes only through an explicit `baseline approve` command with a named approver and a reason, never in CI. Approval refuses failed, seeded, regressed or uncommitted runs unless an override is given and logged |
| **Immutable, auditable evidence** | Approved baselines are stored as records with an append-only audit log in [reports/baselines/](reports/baselines/). Every metric keeps its numerator and denominator; unmeasured data is recorded as unmeasured with a reason, never as 0 |
| **Only comparable runs are compared** | Runs are compared only when dataset, knowledge base, model and embedder match. Anything else is ERROR |
| **Explicit PASS / BLOCKED / ERROR** | BLOCKED means a quality problem was found. ERROR means the check couldn't run. Missing results, corrupt history and crashes are ERROR, never PASS |
| **Failures are documented, not hidden** | Two pre-registered prompt experiments for D-6.1 were rejected against their own decision rules and kept on record. Open defects appear in every CI summary |

## Limitations

- **Live-model coverage is limited.** The live LLM work uses one small local model
  (`qwen2.5:3b`, CPU only). The 27-case LLM dataset has been live-verified on 14 cases (plus one
  case in fail-open mode); the full live run hasn't been completed. Live runs are local and are
  not part of CI.
- **The agent is graded with a scripted reference planner.** It proves the runtime, the policy and
  the checker work, and that planted failures are caught. It says nothing yet about how a live LLM
  planner behaves. A live planner is planned but not implemented.
- **DeepEval and Ragas are advisory.** The local judge isn't reliable on statement-level metrics,
  so their scores provide context only.
- **Prompt experiments D-6.1-A and D-6.1-B were rejected,** and prompt tuning is paused.
- **Known defects D-6.1 to D-6.4 are open** ([ci/known_defects.json](ci/known_defects.json)):

  | ID | Severity | Defect |
  |---|---|---|
  | D-6.1 | high | The live RAG model answers "not found" when the answer is in its context |
  | D-6.2 | medium | LLM-judge scores (Ragas, DeepEval) are unreliable on real answers |
  | D-6.3 | medium | Deterministic fact checks can't detect a muddled answer to contradictory documents |
  | D-6.4 | medium | Pattern-based security controls can miss paraphrased prompt injection and plain-language disclosure |

- **The injection defences are pattern-based.** Passing tests show the documented attacks were
  stopped, not that the system is secure against every injection.
- **CI uses deterministic stand-ins:** the mock chatbot, hashing embeddings and mock RAG models. A
  green build shows the tested scenarios behave as specified, not that the AI is safe in general.
- **Not yet built:** live-agent evaluation, agent telemetry and secure artifact storage on `main`,
  and agent results in the run history and baselines.

## Optional: live local models

Live runs are optional and local. The default provider is [Ollama](https://ollama.com/download/windows);
no paid API is needed.

```powershell
python -m pip install -r requirements-llm.txt     # DeepEval + the ollama client (optional extra)
ollama pull qwen2.5:3b                           # chat model
ollama pull nomic-embed-text                     # embedding model for RAG
```

The [Promptfoo comparison](docs/promptfoo-comparison.md) also needs Node.js and `npm ci`. Ragas runs
in its own environment; see the [RAG evaluation methodology](docs/rag-evaluation-methodology.md). Settings are documented
in [.env.example](.env.example). Never commit a `.env` file.

## Further reading

| Topic | Document |
|---|---|
| A guided 10-minute offline demo | [docs/demo-guide.md](docs/demo-guide.md) |
| Evaluation approaches and why each was chosen | [docs/evaluation-approaches.md](docs/evaluation-approaches.md) |
| Custom engine vs Promptfoo | [docs/promptfoo-comparison.md](docs/promptfoo-comparison.md) |
| LLM evaluation: metrics, layers, cost, limits | [docs/llm-evaluation.md](docs/llm-evaluation.md) |
| RAG design and evaluation method | [docs/rag-architecture.md](docs/rag-architecture.md), [docs/rag-evaluation-methodology.md](docs/rag-evaluation-methodology.md) |
| CI pipeline and quality gates | [docs/ci-pipeline.md](docs/ci-pipeline.md) |
| Observability, regression rules and baseline approval | [docs/quality-observability.md](docs/quality-observability.md) |
| Live calibration and live baselines | [docs/live-calibration.md](docs/live-calibration.md) |
| Pre-registered experiments and their results | [docs/experiments/](docs/experiments/) |
| Sandboxed agent design | [docs/phase-11-plan.md](docs/phase-11-plan.md) |
| Phase-by-phase completion reports | [reports/](reports/) (for example [Phase 10](reports/phase_10_completion_report.md)) |

## Project layout

| Path | Contents |
|---|---|
| [src/ai_quality_lab/](src/ai_quality_lab/) | Systems under test, evaluators, CI tooling and observability |
| [src/ai_quality_lab/agent/](src/ai_quality_lab/agent/) | Sandboxed agent: tools, sandbox, policy, faults, runtime, traces |
| [src/ai_quality_lab/agent_eval/](src/ai_quality_lab/agent_eval/) | Agent scenarios, independent checker, reference planner, evaluation CLI |
| [evals/](evals/) | Golden datasets |
| [data/knowledge_base/](data/knowledge_base/) | 16 synthetic documents, including adversarial and restricted ones |
| [tests/](tests/) | One pytest file per module |
| [ci/](ci/) | Regression rules and known defects |
| [.github/workflows/](.github/workflows/) | CI pipeline |
| [reports/baselines/](reports/baselines/) | Owner-approved baselines and audit log |
