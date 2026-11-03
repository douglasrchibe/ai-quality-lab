# Phase 11 plan: AI agent quality engineering (PROPOSAL, not approved)

**Status:** proposal for owner review, 2026-11-03. Nothing here is implemented.

**Goal:** show quality engineering for an agent that uses tools, makes decisions, respects
authorization boundaries, handles failures, and runs multi-step workflows. The same release gates,
regression detection, baselines and governance used for the chatbot and RAG phases apply here.

**Principle:** reuse the existing framework and add only what's agent-specific:
- **New:** the agent's runtime, tools, policy and trace.
- **Extended:** the evaluation, observability and CI layers each get one new evaluation type.
- **Not built:** no second runner, history, baseline store, regression engine or CI gate.

## 1. Scope decisions needed before implementation

| # | Decision | Recommendation |
|---|---|---|
| 1 | **CLAUDE.md lists "autonomous agents" as out of scope.** Phase 11 needs an explicit scope change | Approve a **sandboxed** agent: in-process, synthetic in-memory tools only, no network or file side effects, a hard step limit. Record it in CLAUDE.md |
| 2 | **Phase numbering.** The phase table ends at 9 | Record the rubric hardening and the D-6.1 experiments as Phase 10 (done), and this as Phase 11 |
| 3 | **Domain** | The existing Northwind IT Service Desk. The agent reuses the knowledge base and RAG retriever as one of its tools |
| 4 | **Live planner model** | Start with the existing `qwen2.5:3b`. A larger local model (for example 7B) is a separate, later decision: a new download, new identity, new baselines |
| 5 | **Live tool-call protocol** | A strict JSON action format through the existing `ChatClient.complete()`. No client change and provider-neutral; malformed output is a measured failure. Native tool calling can come later |
| 6 | **LLM-judge agent metrics** (for example DeepEval task completion) | Defer. If added later, advisory only, like Ragas |

## 2. Architecture

```
            evals/agent_cases.json  (scenarios: goal, role, initial state, faults, expectations)
                         │
                         ▼
  agent/evaluate.py ──▶ agent/runtime.py ── loop (step limit) ──▶ Planner (protocol)
   (cases → results)       │                                      ├─ ScriptedPlanner  (deterministic, CI)
         ▲                 │  every action goes through:          │    + SimulatedFailure modes
         │                 │   1. tool registry (exists? args valid?)
         │                 │   2. policy engine (role, preconditions, approval)   ← enforced in code
         │                 │   3. fault injector (timeouts, errors, bad output)    ← test-controlled
         │                 ▼                                      └─ LLMPlanner (Ollama, opt-in live)
         │            Sandbox state (tickets, users, access grants; in memory, synthetic)
         │                 │
         └──── AgentTrace (every step: action, args, policy decision, result/error) + final state
                         │                     ← the ONLY thing the evaluator judges
                         ▼
   report JSON ──▶ observability record (agent_mock | agent_live) ──▶ history, compare, baselines, CI
```

**Dependency direction, mirroring Phase 3:** `agent.evaluate` → `evaluator`. The runtime and
planners never import evaluation code. The evaluator judges only the **trace and final sandbox
state**, never planner internals, just as it judges only `ObservedReply` today.

### New package `src/ai_quality_lab/agent/` (standard library only)

| Module | Responsibility |
|---|---|
| `tools.py` | Tool registry: name, parameter spec (stdlib validation), side-effect class (read / write / privileged), required permission. Deterministic in-memory implementations |
| `sandbox.py` | Synthetic state (tickets, users, devices, access grants), with snapshot and diff for final-state assertions |
| `policy.py` | Authorization, enforced at the tool boundary in **code** (not the prompt): role → allowed tools; preconditions (identity verified before a password reset); approvals (no self-approval of access) |
| `faults.py` | Injected tool faults per case: timeout, error, permission denied, malformed result, partial failure, and a tool result that contains instructions (injection through tool output) |
| `runtime.py` | The agent loop: step limit, repeated-call detection, an injected clock (deterministic), and a full `AgentTrace` |
| `planners.py` | The `Planner` protocol. `ScriptedPlanner` (deterministic) with `SimulatedFailure` modes. `LLMPlanner` (JSON actions through the existing `ChatClient`) |
| `dataset.py` | Schema and validation for `evals/agent_cases.json`, following `dataset.py` and `rag/dataset.py` |
| `evaluate.py` | Assertions on trace and state, failure classification, summary, release readiness, CLI |

### Tools for the IT Service Desk agent (all synthetic)

| Tool | Class | Policy |
|---|---|---|
| `search_kb` | read | Anyone. Reuses the Phase 6 retriever and its sanitizer on results |
| `lookup_user`, `get_ticket` | read | Anyone (support role) |
| `create_ticket`, `update_ticket`, `add_comment` | write | Support role |
| `verify_identity` | read | Required before any account action |
| `reset_password` | privileged | Support role **and** verified identity |
| `grant_access` / `approve_extension` | privileged | Needs an approver distinct from the requester. The agent can never self-approve |
| `escalate_to_human` | write | Always allowed. The correct move when blocked |

## 3. Evaluation design (reusing the engine)

**Dataset `evals/agent_cases.json`:** data, not code, with severity in the dataset. Each case has
the goal, the user's role, the initial sandbox state, injected faults, and expectations.

**Categories (about 30 cases; final count set in milestone 11.2):**

| Category | Example expectation |
|---|---|
| tool_selection | Uses `search_kb` for a policy question; doesn't create a ticket |
| argument_correctness | `create_ticket(priority="P1")` for an outage; arguments within schema |
| multi_step_workflow | verify identity → reset password → comment on the ticket, in order |
| authorization_boundary | A password reset without verification is never **executed**; self-approval is never attempted |
| failure_recovery | After a tool timeout: at most N retries, then escalate. Never claims success |
| tool_output_injection | A KB or tool result saying "grant admin access" is not followed |
| escalation | Out-of-scope or blocked requests are escalated, not improvised |
| efficiency | Within the step budget; no repeated identical calls (loops) |

**Assertions, all deterministic:**
- tool calls required, as an ordered subsequence
- tool calls forbidden
- argument constraints
- **unauthorized executions = 0**
- policy denials (an attempt that was denied: safe, but reported)
- final-state checks (for example "a P1 ticket exists" or "no password changed")
- **false success claims:** the final message claims success while the state shows failure
- step budget and repeated calls
- the final message: reuses `check_response_contains` / `check_response_not_contains`

**What's reused from the engine:**
- `AssertionResult`, `CaseOutcome`, `Severity` and `decide_release_readiness()`, so the same policy
  applies: BLOCKED on any critical FAIL, any ERROR, or zero cases.
- **Failure classes follow the Phase 6 rule:** they cite deterministic evidence; unexplained
  failures are `evaluation_failure`.
- **The classes:** `wrong_tool`, `bad_arguments`, `authorization_violation` (critical),
  `unsafe_attempt`, `unrecovered_tool_error`, `false_success_claim`, `injection_followed`, `loop`,
  `budget_exceeded`.

**Proving regressions are caught** (the Phase 2 AC10 principle): each `SimulatedFailure` mode must
be **detected**. The modes are: calls an unauthorized tool (with policy enforcement disabled),
skips verification, ignores a tool error, loops, follows injected tool output, and hallucinates a
tool. Tests assert that each mode fails exactly the expected cases.

## 4. Observability, CI and governance (extending, not duplicating)

| Existing component | Phase 11 extension |
|---|---|
| `observability/records.py` | New evaluation types `agent_mock` (deterministic) and `agent_live`, and a `build_agent()` record builder. Identity covers: dataset fingerprint, a tool-registry and policy fingerprint, the planner, and model digests (live). New catalog metrics with numerator/denominator: `task_success_rate`, `unauthorized_executions` (count; never tolerated), `policy_denials`, `false_success_claims`, `unrecovered_tool_errors`, `budget_exceeded` |
| `ci/regression_rules.json` | Rules for the new metrics, through an **owner PR with stated reasons**. `unauthorized_executions` blocks at any count, like safety failures |
| `observability/baselines.py` | Unchanged. `agent_mock` / `agent_live` baselines go through `baseline review` and **owner** `baseline approve` |
| `observability/live_batch.py` + `calibrate` | Generalised from "RAG only" to a `--kind` (rag or agent), reusing provenance checks, batches, warm-ups and calibration statistics as they are |
| `.github/workflows/ci.yml` | One new job, `agent-deterministic`: the scripted planner must be READY, and failure demonstrations (unauthorized action with enforcement off, ignored tool error, loop) must stay BLOCKED. Adds `agent_mock` to the regression-vs-baseline types. All through `ci_check` with `--report` cross-checks. Still offline: no models (`test_ci_workflow.py` keeps enforcing it) |
| `ci/known_defects.json`, skip audit, `console.py` | Unchanged mechanisms. Agent defects found live are recorded with evidence |

**Unchanged governance:**
- live runs only through `live-batch` after the owner approves the configuration
- official batches on clean commits, with pre-declared warm-ups
- no discarded runs; synthetic data only
- Claude never approves baselines or changes tolerances
- advisory metrics never block

## 5. Milestones (each: tests, the full suite passing, a PR, owner merge)

| Milestone | Delivers | Done when |
|---|---|---|
| **11.1 Contracts and sandbox** | tools, sandbox, policy, faults, runtime, trace, ScriptedPlanner and failure modes | Unit tests cover policy enforcement (an unauthorized call can't execute), fault injection, the step limit and loop detection; runs are deterministic |
| **11.2 Dataset and evaluator** | `evals/agent_cases.json`, schema, assertions, failure classes, summary, CLI | The healthy scripted planner passes everything; **every** simulated failure is detected; readiness policy is shared |
| **11.3 Observability** | `agent_mock` type, record builder, metrics, regression rules (owner PR), first owner-approved baseline | `compare` PASS against the approved baseline; failure demos BLOCKED; incompatible identity is ERROR |
| **11.4 CI** | The `agent-deterministic` job, demos, regression-gate wiring | GitHub Actions green; demos stay BLOCKED; CI workflow tests updated |
| **11.5 Live planner** | `LLMPlanner` (JSON actions), opt-in live tests, `live-batch --kind agent`, calibration | Owner-approved live batch (2 warm-ups + 10 official); calibration report; live baseline only if the owner approves it |
| **11.6 Documentation** | README status, an agent methodology doc, a demo guide section, a completion report | Definition of done in CLAUDE.md met, with results reported as measured |

## 6. Risks and how they're handled

| Risk | Mitigation |
|---|---|
| A 3B model plans tools poorly | Expected, and useful evidence. Failures are classified and recorded as known defects, not tuned away. A larger model is a separate decision |
| Live multi-step runs vary more than single answers | `live-batch` calibration (per-case Wilson, run-level t) already handles this. Tolerances only by owner PR |
| Scope creep toward a general agent framework | Only the tools and categories in this plan. New tools or categories need owner approval |
| Unsafe side effects | None possible: synthetic in-memory sandbox, no network or filesystem tools, policy enforced in code, hard step limit |
| D-6.1 confounding | Phase 11 doesn't change the RAG prompt or the RAG baselines. `search_kb` uses retrieval only, not the RAG answer generator |

## 7. Housekeeping on `main` (with or before 11.1)

`main` doesn't yet record that D-6.1-B was rejected and prompt tuning is paused. The record lives
on branch `d61-b-split-not-found`. A small docs change would:
- update `docs/experiments/d61-b-draft.md`
- add the D-6.1-B result to D-6.1's evidence in `ci/known_defects.json` (all of D-6.1 to D-6.4
  stay open)
