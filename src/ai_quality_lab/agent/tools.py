"""The bounded tool contract: the action format, the ten tool specs, stages 1–3, and the one
outcome contract every stage returns (``StageOutcome``).

* Stage 1 (parse): a planner's raw text must be one JSON object in the action format.
* Stage 2 (tool selection): the tool must be in the registry.
* Stage 3 (schema): exactly the declared parameters, with the declared types and enums.

The public stage functions (``parse_stage``, ``select_stage``, ``schema_stage``; stage 4 is
``Sandbox.arguments_stage`` and stage 5 ``policy.authorize_stage``) return a ``StageOutcome`` for
expected validation failures. ``StageError`` is internal. Any other exception is a programming
error and propagates, so it can never be mistaken for a rejected action.

The contract (tool specs and the authorization rules each tool is subject to) is fingerprinted, so
a run's identity changes whenever the contract does.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

MAX_TEXT = 500
REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{1,40}$")
WORLD_ID = re.compile(r"^(U|T|APR)-\d{1,8}$")  # user, ticket and approval IDs


def safe_repr(value: Any, known: tuple[str, ...] = ()) -> str:
    """How a planner-supplied value may appear in a rejection detail. Planner output can contain
    anything (an identifier-shaped token can still be a secret) and details are logged, so a value
    is echoed only if it is a sandbox ID (``U-…``, ``T-…``, ``APR-…``) or one of the ``known`` names;
    everything else is described by size only."""
    if isinstance(value, str) and (WORLD_ID.match(value) or value in known):
        return repr(value)
    if isinstance(value, str):
        return f"<redacted, {len(value)} chars>"
    return f"<redacted {type(value).__name__}>"


class ToolClass(str, Enum):
    READ = "read"
    WRITE = "write"
    CONSEQUENTIAL = "consequential"  # requires a human approval (R-APPROVAL)


class ParamType(str, Enum):
    STRING = "string"
    OBJECT = "object"


@dataclass(frozen=True)
class ParamSpec:
    name: str
    type: ParamType = ParamType.STRING
    required: bool = True
    enum: tuple[str, ...] = ()


@dataclass(frozen=True)
class ToolSpec:
    name: str
    tool_class: ToolClass
    params: tuple[ParamSpec, ...]
    rules: tuple[str, ...]  # stage-5 rule IDs (policy.py) this tool is subject to

    def param(self, name: str) -> ParamSpec | None:
        return next((p for p in self.params if p.name == name), None)


def _p(name: str, **kwargs: Any) -> ParamSpec:
    return ParamSpec(name, **kwargs)


TOOLS: tuple[ToolSpec, ...] = (
    ToolSpec("search_kb", ToolClass.READ, (_p("query"),), ("R-READ-KB",)),
    ToolSpec("get_user", ToolClass.READ, (_p("user_id"),), ("R-READ-SCOPE",)),
    ToolSpec("get_ticket", ToolClass.READ, (_p("ticket_id"),), ("R-READ-SCOPE",)),
    ToolSpec("verify_identity", ToolClass.READ,
             (_p("user_id"), _p("method", enum=("callback",))), ("R-SELF",)),
    ToolSpec("create_ticket", ToolClass.WRITE,
             (_p("request_id"), _p("user_id"), _p("summary"),
              _p("priority", enum=("P1", "P2", "P3", "P4"))), ("R-SELF",)),
    ToolSpec("add_comment", ToolClass.WRITE, (_p("ticket_id"), _p("text")), ("R-SELF",)),
    ToolSpec("request_approval", ToolClass.WRITE,
             (_p("tool"), _p("arguments", type=ParamType.OBJECT), _p("justification")),
             ("R-APPROVAL-PRECHECK",)),
    ToolSpec("reset_password", ToolClass.CONSEQUENTIAL, (_p("user_id"), _p("approval_id")),
             ("R-SELF", "R-VERIFIED", "R-APPROVAL")),
    ToolSpec("grant_access", ToolClass.CONSEQUENTIAL,
             (_p("user_id"), _p("resource"), _p("approval_id")),
             ("R-SELF", "R-NO-ADMIN", "R-CATALOGUE", "R-APPROVAL")),
    ToolSpec("escalate_to_human", ToolClass.WRITE,
             (_p("reason"), _p("ticket_id", required=False)), ("R-ESCALATE",)),
)
REGISTRY: Mapping[str, ToolSpec] = {t.name: t for t in TOOLS}
CONSEQUENTIAL_TOOLS = tuple(t.name for t in TOOLS if t.tool_class is ToolClass.CONSEQUENTIAL)
FINAL_STATUSES = ("resolved", "escalated", "refused")


class ActionType(str, Enum):
    TOOL_CALL = "tool_call"
    FINAL = "final"


@dataclass(frozen=True)
class ToolCall:
    tool: str
    arguments: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"type": ActionType.TOOL_CALL.value, "tool": self.tool,
                "arguments": dict(self.arguments)}


@dataclass(frozen=True)
class FinalAction:
    status: str
    message: str

    def to_dict(self) -> dict[str, Any]:
        return {"type": ActionType.FINAL.value, "status": self.status, "message": self.message}


Action = ToolCall | FinalAction


class Stage(str, Enum):
    PARSE = "parse"
    SELECT = "select"
    SCHEMA = "schema"
    ARGUMENTS = "arguments"
    AUTHORIZE = "authorize"
    EXECUTE = "execute"
    RESULT = "result"


@dataclass(frozen=True)
class StageOutcome:
    """The result of one stage: ``ok``, the stage, a classification ``code`` ("ok" or e.g.
    ``schema_error``), a detail safe to log, and on success the stage's ``value`` (the parsed action,
    the tool spec, the authorization decision). ``value`` is not serialized."""
    stage: Stage
    ok: bool
    code: str
    detail: str = ""
    value: Any = field(default=None, compare=False)

    def to_dict(self) -> dict[str, Any]:
        return {"stage": self.stage.value, "ok": self.ok, "code": self.code, "detail": self.detail}


def accept(stage: Stage, value: Any = None, detail: str = "") -> StageOutcome:
    return StageOutcome(stage, True, "ok", detail, value)


class StageError(ValueError):
    """Internal: raised inside a stage for an expected rejection, and turned into a
    ``StageOutcome`` by the public stage function. ``code`` is the trace's classification."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail

    def outcome(self, stage: Stage) -> StageOutcome:
        return StageOutcome(stage, False, self.code, self.detail)


def parse_stage(raw: str) -> StageOutcome:
    """Stage 1. ``value`` is the ``ToolCall`` or ``FinalAction``."""
    try:
        return accept(Stage.PARSE, _parse_action(raw))
    except StageError as exc:
        return exc.outcome(Stage.PARSE)


def select_stage(call: ToolCall) -> StageOutcome:
    """Stage 2. ``value`` is the ``ToolSpec``."""
    try:
        return accept(Stage.SELECT, _select_tool(call))
    except StageError as exc:
        return exc.outcome(Stage.SELECT)


def schema_stage(spec: ToolSpec, arguments: Mapping[str, Any]) -> StageOutcome:
    """Stage 3."""
    try:
        _validate_schema(spec, arguments)
    except StageError as exc:
        return exc.outcome(Stage.SCHEMA)
    return accept(Stage.SCHEMA)


def _parse_action(raw: str) -> Action:
    """Stage 1. Exactly one JSON object in the action format; anything else is ``parse_error``."""
    try:
        data = json.loads(raw)
    except RecursionError:  # absurdly nested input is a rejected action, not a crash
        raise StageError("parse_error", "not JSON: nested too deeply") from None
    except (TypeError, ValueError) as exc:  # the message gives a position, never the input
        raise StageError("parse_error", f"not JSON: {exc}") from None
    if not isinstance(data, dict):
        raise StageError("parse_error", "an action must be a JSON object")
    kind = data.get("type")
    if kind == ActionType.TOOL_CALL.value:
        _exact_keys(data, {"type", "tool", "arguments"})
        if not isinstance(data["tool"], str) or not isinstance(data["arguments"], dict):
            raise StageError("parse_error", "'tool' must be a string and 'arguments' an object")
        return ToolCall(data["tool"], data["arguments"])
    if kind == ActionType.FINAL.value:
        _exact_keys(data, {"type", "status", "message"})
        if data["status"] not in FINAL_STATUSES or not isinstance(data["message"], str):
            raise StageError("parse_error",
                             f"'status' must be one of {FINAL_STATUSES} and 'message' a string")
        return FinalAction(data["status"], data["message"])
    raise StageError("parse_error", f"unknown action type {safe_repr(kind)}; expected one of "
                                    f"{[t.value for t in ActionType]}")


def _exact_keys(data: Mapping[str, Any], keys: set[str]) -> None:
    if set(data) != keys:
        raise StageError("parse_error", f"expected exactly the keys {sorted(keys)}, "
                                        f"got {sorted(data)}")


def _select_tool(call: ToolCall) -> ToolSpec:
    """Stage 2. The tool must exist in the registry."""
    spec = REGISTRY.get(call.tool)
    if spec is None:
        raise StageError("unknown_tool", f"{safe_repr(call.tool)} is not in the tool registry "
                                         f"({len(REGISTRY)} tools)")
    return spec


def _validate_schema(spec: ToolSpec, arguments: Mapping[str, Any]) -> None:
    """Stage 3. Exactly the declared parameters, with the declared types and enum values."""
    unknown = sorted(set(arguments) - {p.name for p in spec.params})
    if unknown:
        raise StageError("schema_error", f"{spec.name}: {len(unknown)} unknown parameter(s); "
                                         f"allowed: {[p.name for p in spec.params]}")
    for param in spec.params:
        if param.name not in arguments:
            if param.required:
                raise StageError("schema_error", f"{spec.name}: missing '{param.name}'")
            continue
        value = arguments[param.name]
        if param.type is ParamType.OBJECT:
            if not isinstance(value, dict):
                raise StageError("schema_error", f"{spec.name}: '{param.name}' must be an object")
            continue
        if not isinstance(value, str) or not value.strip() or len(value) > MAX_TEXT:
            raise StageError("schema_error", f"{spec.name}: '{param.name}' must be a non-empty "
                                             f"string of at most {MAX_TEXT} characters")
        if param.enum and value not in param.enum:
            raise StageError("schema_error",
                             f"{spec.name}: '{param.name}' must be one of {list(param.enum)}")


def contract_fingerprint(rules: Mapping[str, str]) -> str:
    """Hash of the tool specs plus the text of every rule they use (pass ``policy.RULES``)."""
    used = sorted({rule for tool in TOOLS for rule in tool.rules})
    missing = [r for r in used if r not in rules]
    if missing:
        raise ValueError(f"tools reference undefined rules: {missing}")
    payload = {
        "tools": [{"name": t.name, "class": t.tool_class.value, "rules": list(t.rules),
                   "params": [{"name": p.name, "type": p.type.value, "required": p.required,
                               "enum": list(p.enum)} for p in t.params]} for t in TOOLS],
        "rules": {r: rules[r] for r in used},
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
