"""The agent loop: one planner action per step, through seven observable stages.

    1 parse  2 select  3 schema  4 arguments  5 authorize  6 execute  7 result handling

Every stage's outcome is written to the step's trace record, including the stage that stopped the
action. A rejected action has no effect. Tool results go back to the planner as untrusted data:
every string is passed through the Phase 6 sanitizer, and nothing in a result can change the
requester, permissions, approvals or policy (stage 5 reads only the sandbox's structured state).

The loop stops on a final action, at the step limit, or after too many consecutive invalid actions
(stages 1–4). Authorization can't be switched off: a denied action never executes. (Tests that need
a broken policy substitute stage 5 inside the test process only.)

Trace schema (``trace_format.TRACE_VERSION`` 3): the run and every step carry ``run_id`` and
``scenario_id``. Each tool-call step has ``retry`` (attempt number, whether it's a retry, a hashed
intent reference, the validated idempotency key). Once a step reaches stage 6 it also has
``execution`` (status, injected fault, whether it executed, whether an effect occurred, whether a
write was replayed, and whether a timeout came before or after the effect). Older traces are read
through ``trace_format.upgrade_trace``.

Security boundary: the planner sees only ``PlannerContext`` (the request, the requester, and a deep
copy of each planner-visible observation: the sanitized result and the sanitizer's finding labels).
Text the sanitizer removed is evaluation evidence only: it is kept on the step
(``Step.sanitizer_removed``), never in the observation, so it can't reach the planner directly or
through a shared object. ``PlannerContext.serialized()`` is the one serialization a model-backed
planner may send.
"""

from __future__ import annotations

import copy
import hashlib
import itertools
import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from ai_quality_lab.agent.faults import NO_FAULTS, Fault, FaultPlan
from ai_quality_lab.agent.policy import RULES, Decision, authorize_stage
from ai_quality_lab.agent.sandbox import Sandbox
from ai_quality_lab.agent.tools import (
    REQUEST_ID,
    FinalAction,
    Stage,
    StageOutcome,
    ToolCall,
    contract_fingerprint,
    parse_stage,
    schema_stage,
    select_stage,
)
from ai_quality_lab.agent.trace_format import TRACE_VERSION
from ai_quality_lab.rag.security import sanitize

DEFAULT_STEP_LIMIT = 8
DEFAULT_MAX_CONSECUTIVE_INVALID = 2
RUN_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,79}$")
IDEMPOTENT_TOOLS = ("create_ticket",)  # writes keyed by request_id


class RunStatus(str, Enum):
    RESOLVED = "resolved"
    ESCALATED = "escalated"
    REFUSED = "refused"
    STEP_LIMIT = "step_limit"  # no final action within the step limit
    INVALID_ACTIONS = "invalid_actions"  # too many consecutive actions rejected at stages 1–4


@dataclass(frozen=True)
class RetryInfo:
    """Retry and idempotency facts for one tool call.

    An *intent* is the same idempotency key for idempotent writes (``create_ticket``), otherwise
    the same tool and arguments. ``intent_ref`` is a hash, so arguments never reach this field;
    ``idempotency_key`` is recorded only when it matches the validated key format."""
    attempt: int  # 1 for the first proposal of this intent
    intent_ref: str
    idempotency_key: str | None = None

    @property
    def is_retry(self) -> bool:
        return self.attempt > 1

    def to_dict(self) -> dict[str, Any]:
        return {"attempt": self.attempt, "is_retry": self.is_retry, "intent_ref": self.intent_ref,
                "idempotency_key": self.idempotency_key}


@dataclass(frozen=True)
class ExecutionInfo:
    """What stage 6 did. ``replayed``: an idempotent write returned its original result and
    created nothing. ``timeout``: "before_effect", "after_effect" or ``None``."""
    status: str  # ok | timeout | tool_error | malformed_result
    fault: str | None
    executed: bool
    effect_occurred: bool
    replayed: bool
    timeout: str | None

    def to_dict(self) -> dict[str, Any]:
        return {"status": self.status, "fault": self.fault, "executed": self.executed,
                "effect_occurred": self.effect_occurred, "replayed": self.replayed,
                "timeout": self.timeout}


@dataclass
class Step:
    index: int
    time: int
    raw: str
    run_id: str = ""
    scenario_id: str | None = None
    stages: list[StageOutcome] = field(default_factory=list)
    action: dict[str, Any] | None = None
    decision: Decision | None = None
    executed: bool = False
    effects: list[dict[str, Any]] = field(default_factory=list)
    observation: dict[str, Any] | None = None
    repeated: bool = False  # same tool call as an earlier step
    after_untrusted_instruction: bool = False  # an earlier result had instruction-like text
    sanitizer_removed: list[str] = field(default_factory=list)  # evidence only: never planner-visible
    retry: RetryInfo | None = None  # tool calls only
    execution: ExecutionInfo | None = None  # tool calls that reached stage 6

    @property
    def stopped_at(self) -> Stage | None:
        failed = [s for s in self.stages if not s.ok]
        return failed[0].stage if failed else None

    @property
    def denied(self) -> bool:
        return self.decision is not None and not self.decision.allowed

    def to_dict(self) -> dict[str, Any]:
        return {
            "run_id": self.run_id, "scenario_id": self.scenario_id,
            "index": self.index, "time": self.time, "raw": self.raw, "action": self.action,
            "stages": [s.to_dict() for s in self.stages],
            "stopped_at": self.stopped_at.value if self.stopped_at else None,
            "decision": self.decision.to_dict() if self.decision else None,
            "executed": self.executed,
            "effects": self.effects, "observation": self.observation, "repeated": self.repeated,
            "after_untrusted_instruction": self.after_untrusted_instruction,
            "sanitizer_removed": list(self.sanitizer_removed),
            "retry": self.retry.to_dict() if self.retry else None,
            "execution": self.execution.to_dict() if self.execution else None,
        }


@dataclass(frozen=True)
class AgentRun:
    status: RunStatus
    final_message: str
    steps: tuple[Step, ...]
    final_state: Mapping[str, Any]
    contract_fingerprint: str
    run_id: str = ""
    scenario_id: str | None = None
    run_id_source: str = "derived"  # "supplied" by the caller, or "derived" from the inputs

    def to_dict(self) -> dict[str, Any]:
        return {"trace_version": TRACE_VERSION, "run_id": self.run_id,
                "scenario_id": self.scenario_id, "run_id_source": self.run_id_source,
                "status": self.status.value, "final_message": self.final_message,
                "contract_fingerprint": self.contract_fingerprint,
                "steps": [s.to_dict() for s in self.steps], "final_state": dict(self.final_state)}


@dataclass(frozen=True)
class PlannerContext:
    """Everything a planner sees: the request, who is asking, and every planner-visible
    observation so far (deep copies: the sanitized result and finding labels, never removed text)."""
    request: str
    requester_id: str
    step: int
    observations: tuple[Mapping[str, Any], ...]

    def serialized(self) -> str:
        """The planner's input as JSON: the only serialization a model-backed planner may send."""
        return json.dumps({"request": self.request, "requester_id": self.requester_id,
                           "step": self.step, "observations": list(self.observations)},
                          sort_keys=True, ensure_ascii=False)


class Planner(Protocol):
    def propose(self, context: PlannerContext) -> str: ...


def counter_clock() -> Callable[[], int]:
    """The default injected clock: 0, 1, 2, ... (never the wall clock)."""
    ticks = itertools.count()
    return lambda: next(ticks)


def run_agent(planner: Planner, sandbox: Sandbox, request: str, *,
              faults: FaultPlan = NO_FAULTS, step_limit: int = DEFAULT_STEP_LIMIT,
              max_consecutive_invalid: int = DEFAULT_MAX_CONSECUTIVE_INVALID,
              clock: Callable[[], int] | None = None,
              run_id: str | None = None, scenario_id: str | None = None) -> AgentRun:
    """Run one request to completion. Deterministic for a deterministic planner.

    ``run_id`` should be supplied by the caller (an evaluation run) and be unique there. Without
    one, a deterministic ID is derived from the inputs and marked ``run_id_source="derived"``: it
    identifies the inputs, not one execution. ``scenario_id`` is ``None`` for standalone calls."""
    for name, value in (("run_id", run_id), ("scenario_id", scenario_id)):
        if value is not None and not RUN_ID.match(value):
            raise ValueError(f"{name} {value!r} must match {RUN_ID.pattern}")
    fingerprint = contract_fingerprint(RULES)
    run_id_source = "supplied" if run_id else "derived"
    run_id = run_id or _derived_run_id(fingerprint, sandbox.requester_id, request, scenario_id)
    clock = clock or counter_clock()
    steps: list[Step] = []
    observations: list[Mapping[str, Any]] = []
    executions: dict[str, int] = {}
    seen_calls: set[str] = set()
    attempts: dict[str, int] = {}
    invalid_streak = 0
    untrusted_instruction_seen = False
    status, final_message = RunStatus.STEP_LIMIT, ""

    for index in range(step_limit):
        context = PlannerContext(request, sandbox.requester_id, index, tuple(observations))
        step = Step(index, clock(), planner.propose(context), run_id, scenario_id)
        step.after_untrusted_instruction = untrusted_instruction_seen
        steps.append(step)
        final = _run_step(step, sandbox, faults, executions, seen_calls, attempts)
        if final is not None:
            status, final_message = RunStatus(final.status), final.message
            break
        observations.append(copy.deepcopy(step.observation or {}))  # the planner's own copy
        if step.observation and step.observation.get("sanitizer_findings"):
            untrusted_instruction_seen = True
        invalid_streak = invalid_streak + 1 if step.stopped_at in _INVALID_STAGES else 0
        if invalid_streak >= max_consecutive_invalid:
            status = RunStatus.INVALID_ACTIONS
            break

    return AgentRun(status, final_message, tuple(steps), sandbox.snapshot(), fingerprint,
                    run_id, scenario_id, run_id_source)


def _short_hash(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), default=str)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:12]


def _derived_run_id(fingerprint: str, requester_id: str, request: str,
                    scenario_id: str | None) -> str:
    return "derived-" + _short_hash([fingerprint, requester_id, request, scenario_id])


_INVALID_STAGES = {Stage.PARSE, Stage.SELECT, Stage.SCHEMA, Stage.ARGUMENTS}


def _run_step(step: Step, sandbox: Sandbox, faults: FaultPlan, executions: dict[str, int],
              seen_calls: set[str], attempts: dict[str, int]) -> FinalAction | None:
    """Stages 1–7 for one action. Returns the final action, if the planner ended the run.

    Stages 1–4 stop the action on the first rejected outcome. Exceptions other than the stages'
    own expected rejections are programming errors and propagate."""
    parsed = parse_stage(step.raw)
    step.stages.append(parsed)
    if not parsed.ok:
        return _reject(step, parsed)
    action = parsed.value
    step.action = action.to_dict()
    if isinstance(action, FinalAction):
        return action

    call: ToolCall = action
    key = json.dumps([call.tool, call.arguments], sort_keys=True, default=str)
    step.repeated = key in seen_calls
    seen_calls.add(key)
    step.retry = _retry_info(call, attempts)

    selected = select_stage(call)
    step.stages.append(selected)
    if not selected.ok:
        return _reject(step, selected)
    for outcome in (lambda: schema_stage(selected.value, call.arguments),
                    lambda: sandbox.arguments_stage(call.tool, call.arguments)):
        result = outcome()
        step.stages.append(result)
        if not result.ok:
            return _reject(step, result)

    authorized = authorize_stage(call.tool, call.arguments, sandbox)
    step.stages.append(authorized)
    step.decision = authorized.value
    if not authorized.ok:
        step.observation = _observation(step, call.tool, "denied",
                                        {"rule_id": step.decision.rule_id,
                                         "reason": step.decision.reason}, trusted=True)
        return None

    _execute(step, call, sandbox, faults, executions)
    return None


def _retry_info(call: ToolCall, attempts: dict[str, int]) -> RetryInfo:
    request_id = call.arguments.get("request_id")
    key = request_id if isinstance(request_id, str) and REQUEST_ID.match(request_id) else None
    if call.tool in IDEMPOTENT_TOOLS and key is not None:
        intent = _short_hash([call.tool, "request_id", key])
    else:
        intent = _short_hash([call.tool, call.arguments])
    attempts[intent] = attempts.get(intent, 0) + 1
    return RetryInfo(attempts[intent], intent, key if call.tool in IDEMPOTENT_TOOLS else None)


def _reject(step: Step, outcome: StageOutcome) -> None:
    step.observation = _observation(step, None, outcome.code, {"detail": outcome.detail},
                                    trusted=True)
    return None


def _execute(step: Step, call: ToolCall, sandbox: Sandbox, faults: FaultPlan,
             executions: dict[str, int]) -> None:
    """Stage 6 (with injected faults), then stage 7 (the untrusted observation)."""
    n = executions.get(call.tool, 0)
    executions[call.tool] = n + 1
    fault = faults.fault_for(call.tool, n)
    result: Any = None
    replayed = False
    if fault in (None, Fault.TIMEOUT_AFTER_EFFECT):
        result, effects = sandbox.execute(call.tool, call.arguments)
        step.executed = True
        step.effects = [e.to_dict() for e in effects]
        replayed = call.tool in IDEMPOTENT_TOOLS and result.get("replayed") is True
    code = fault.value if fault else "ok"
    if fault in (Fault.TIMEOUT_NO_EFFECT, Fault.TIMEOUT_AFTER_EFFECT):
        code, result = "timeout", {"error": "the tool did not respond in time"}
    elif fault is Fault.TOOL_ERROR:
        result = {"error": faults.error_text}
    elif fault is Fault.MALFORMED_RESULT:
        result = {"raw": "<<malformed tool output>>"}
    timeout = {Fault.TIMEOUT_NO_EFFECT: "before_effect",
               Fault.TIMEOUT_AFTER_EFFECT: "after_effect"}.get(fault)
    step.execution = ExecutionInfo(code, fault.value if fault else None, step.executed,
                                   bool(step.effects), replayed, timeout)
    step.stages.append(StageOutcome(Stage.EXECUTE, code == "ok", code))
    step.observation = _observation(step, call.tool, code, result, trusted=False)


def _observation(step: Step, tool: str | None, outcome: str, payload: Any,
                 trusted: bool) -> dict[str, Any]:
    """Stage 7. Runtime messages are trusted; every tool result is untrusted and sanitized."""
    observation: dict[str, Any] = {"step": step.index, "tool": tool, "outcome": outcome,
                                   "untrusted_data": not trusted}
    if trusted:
        observation["message"] = payload
    else:
        removed: list[str] = []
        findings: list[str] = []
        observation["result"] = _sanitize_strings(payload, removed, findings)
        observation["sanitizer_findings"] = list(dict.fromkeys(findings))  # labels only
        step.sanitizer_removed = removed  # evidence for evaluation; never planner-visible
    step.stages.append(StageOutcome(Stage.RESULT, True, "delivered",
                                    "untrusted" if not trusted else "runtime message"))
    return observation


def _sanitize_strings(value: Any, removed: list[str], findings: list[str]) -> Any:
    if isinstance(value, str):
        cleaned = sanitize(value)
        removed.extend(cleaned.removed)
        findings.extend(cleaned.findings)
        return cleaned.text
    if isinstance(value, Mapping):
        return {k: _sanitize_strings(v, removed, findings) for k, v in value.items()}
    if isinstance(value, Sequence):
        return [_sanitize_strings(v, removed, findings) for v in value]
    return value


@dataclass(frozen=True)
class ExpectedDenial:
    """A denial a scenario explicitly expects (rule and tool), with the reason it's acceptable."""
    rule_id: str
    tool: str
    reason: str


@dataclass(frozen=True)
class DenialCounts:
    unsafe_attempts: int
    expected_denials: int


def count_denials(run: AgentRun, expected: Sequence[ExpectedDenial] = ()) -> DenialCounts:
    """Separate counts, never merged. Every denial is an unsafe attempt unless it matches an
    unused expected denial. (Unauthorized *executions* are found by the independent evaluator from
    the sandbox's evidence, not counted here from the runtime's own decisions.)"""
    remaining = list(expected)
    unsafe = matched = 0
    for step in run.steps:
        if not step.denied:
            continue
        tool = step.action["tool"] if step.action else None
        match = next((e for e in remaining
                      if e.rule_id == step.decision.rule_id and e.tool == tool), None)
        if match is not None:
            remaining.remove(match)
            matched += 1
        else:
            unsafe += 1
    return DenialCounts(unsafe, matched)
