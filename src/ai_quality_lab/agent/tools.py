"""The bounded tool contract: the action format, the ten tool specs, and stages 1–3.

* Stage 1 (parse): a planner's raw text must be one JSON object in the action format.
* Stage 2 (tool selection): the tool must be in the registry.
* Stage 3 (schema): exactly the declared parameters, with the declared types and enums.

The contract (tool specs and the authorization rules each tool is subject to) is fingerprinted, so
a run's identity changes whenever the contract does.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

MAX_TEXT = 500
REQUEST_ID = re.compile(r"^[A-Za-z0-9-]{1,40}$")


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


class StageError(ValueError):
    """An action rejected at one of stages 1–4; ``code`` is the trace's rejection code."""

    def __init__(self, code: str, detail: str) -> None:
        super().__init__(f"{code}: {detail}")
        self.code = code
        self.detail = detail


def parse_action(raw: str) -> Action:
    """Stage 1. Exactly one JSON object in the action format; anything else is ``parse_error``."""
    try:
        data = json.loads(raw)
    except (TypeError, ValueError) as exc:
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
    raise StageError("parse_error", f"unknown action type {kind!r}")


def _exact_keys(data: Mapping[str, Any], keys: set[str]) -> None:
    if set(data) != keys:
        raise StageError("parse_error", f"expected exactly the keys {sorted(keys)}, "
                                        f"got {sorted(data)}")


def select_tool(call: ToolCall) -> ToolSpec:
    """Stage 2. The tool must exist in the registry."""
    spec = REGISTRY.get(call.tool)
    if spec is None:
        raise StageError("unknown_tool", f"{call.tool!r} is not in the tool registry")
    return spec


def validate_schema(spec: ToolSpec, arguments: Mapping[str, Any]) -> None:
    """Stage 3. Exactly the declared parameters, with the declared types and enum values."""
    unknown = sorted(set(arguments) - {p.name for p in spec.params})
    if unknown:
        raise StageError("schema_error", f"{spec.name}: unknown parameter(s) {unknown}")
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
