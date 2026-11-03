"""Schema, loading and validation for ``evals/agent_cases.json``, and building a scenario's sandbox.

The dataset is data: a shared synthetic ``world`` (users with roles and managers, tickets, the
access catalogue, admin resources) and ``cases``. Each case sets up its scripts (identity
verification, the human approver's responses, knowledge-base passages, injected text, tool faults)
and states its expected behaviour. Validation is strict: unknown keys, unknown tools or effect
kinds, references to users or tickets that don't exist, and duplicate IDs are all errors.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ai_quality_lab.agent.faults import Fault, FaultPlan
from ai_quality_lab.agent.sandbox import (
    ApprovalResponse,
    KBPassage,
    Role,
    Sandbox,
    Ticket,
    User,
)
from ai_quality_lab.agent.tools import REGISTRY
from ai_quality_lab.dataset import Severity

DEFAULT_AGENT_DATASET = Path(__file__).resolve().parents[3] / "evals" / "agent_cases.json"
CASE_ID = re.compile(r"^AGT-\d{3}$")
FINAL_STATUSES = ("resolved", "escalated", "refused")
EFFECT_KINDS = ("ticket_created", "comment_added", "identity_verified", "approval_requested",
                "password_reset", "access_granted", "escalated")
CASE_KEYS = {"id", "title", "category", "severity", "deterministic", "requester_id", "request",
             "setup", "expected_behavior", "expected", "pass_criteria", "notes"}
SETUP_KEYS = {"verification", "approvals", "kb_passages", "ticket_comments", "user_notes",
              "faults", "fault_error_text"}
EXPECTED_KEYS = {"final_status", "required_calls", "forbidden_tools", "effects",
                 "expected_denials", "max_steps", "message_contains", "message_not_contains"}
DENIAL_KEYS = {"rule_id", "tool", "decision_reason", "reason"}


class AgentDatasetError(ValueError):
    def __init__(self, problems: Sequence[str]) -> None:
        super().__init__("invalid agent dataset:\n- " + "\n- ".join(problems))
        self.problems = tuple(problems)


@dataclass(frozen=True)
class World:
    users: Mapping[str, Mapping[str, Any]]  # user_id -> {name, role, manager_id}
    tickets: Mapping[str, Mapping[str, Any]]  # ticket_id -> {owner_id, summary}
    access_catalogue: tuple[str, ...]
    admin_resources: tuple[str, ...]

    def to_facts(self) -> dict[str, Any]:
        """Plain data for the oracle."""
        return {"users": {k: dict(v) for k, v in self.users.items()},
                "tickets": {k: dict(v) for k, v in self.tickets.items()},
                "access_catalogue": list(self.access_catalogue),
                "admin_resources": list(self.admin_resources)}


@dataclass(frozen=True)
class AgentCase:
    id: str
    title: str
    category: str
    severity: Severity
    deterministic: bool
    requester_id: str
    request: str
    setup: Mapping[str, Any]
    expected_behavior: str
    expected: Mapping[str, Any]
    pass_criteria: str


@dataclass(frozen=True)
class AgentDataset:
    world: World
    cases: tuple[AgentCase, ...]

    def case(self, case_id: str) -> AgentCase:
        for case in self.cases:
            if case.id == case_id:
                return case
        raise KeyError(f"unknown scenario {case_id!r}")


def load_agent_dataset(path: str | Path = DEFAULT_AGENT_DATASET) -> AgentDataset:
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8-sig"))
    except (OSError, ValueError) as exc:
        raise AgentDatasetError([f"cannot read {path}: {exc}"]) from None
    return parse_agent_dataset(data)


def parse_agent_dataset(data: Any) -> AgentDataset:
    problems: list[str] = []
    if not isinstance(data, Mapping) or data.get("schema_version") != 1:
        raise AgentDatasetError(["expected an object with schema_version 1"])
    world = _parse_world(data.get("world"), problems)
    cases: list[AgentCase] = []
    seen: set[str] = set()
    for i, raw in enumerate(data.get("cases") or []):
        case = _parse_case(raw, world, problems, f"cases[{i}]") if world else None
        if case is None:
            continue
        if case.id in seen:
            problems.append(f"{case.id}: duplicate id")
        seen.add(case.id)
        cases.append(case)
    if not cases and not problems:
        problems.append("no cases")
    if problems:
        raise AgentDatasetError(problems)
    return AgentDataset(world, tuple(cases))


def _parse_world(raw: Any, problems: list[str]) -> World | None:
    if not isinstance(raw, Mapping):
        problems.append("world: missing")
        return None
    users = {u["user_id"]: {"name": u["name"], "role": u["role"], "manager_id": u["manager_id"]}
             for u in raw.get("users", [])}
    for uid, user in users.items():
        if user["role"] not in ("employee", "manager"):
            problems.append(f"world: {uid} has unknown role {user['role']!r}")
        if user["manager_id"] is not None and user["manager_id"] not in users:
            problems.append(f"world: {uid} has unknown manager {user['manager_id']!r}")
    tickets = {t["ticket_id"]: {"owner_id": t["owner_id"], "summary": t["summary"]}
               for t in raw.get("tickets", [])}
    for tid, ticket in tickets.items():
        if ticket["owner_id"] not in users:
            problems.append(f"world: {tid} has unknown owner {ticket['owner_id']!r}")
    return World(users, tickets, tuple(raw.get("access_catalogue", ())),
                 tuple(raw.get("admin_resources", ())))


def _parse_case(raw: Any, world: World, problems: list[str], where: str) -> AgentCase | None:
    if not isinstance(raw, Mapping):
        problems.append(f"{where}: not an object")
        return None
    cid = raw.get("id", where)
    bad = sorted(set(raw) - CASE_KEYS)
    missing = sorted(CASE_KEYS - {"notes"} - set(raw))
    if bad or missing:
        problems.append(f"{cid}: unknown keys {bad}, missing keys {missing}")
        return None
    start = len(problems)
    if not CASE_ID.match(cid):
        problems.append(f"{cid}: id must look like AGT-001")
    try:
        severity = Severity(raw["severity"])
    except ValueError:
        problems.append(f"{cid}: unknown severity {raw['severity']!r}")
        severity = Severity.LOW
    if raw["requester_id"] not in world.users:
        problems.append(f"{cid}: unknown requester {raw['requester_id']!r}")
    if not isinstance(raw["deterministic"], bool):
        problems.append(f"{cid}: deterministic must be true or false")
    _check_setup(cid, raw["setup"], world, problems)
    _check_expected(cid, raw["expected"], problems)
    if len(problems) > start:
        return None
    return AgentCase(cid, raw["title"], raw["category"], severity, raw["deterministic"],
                     raw["requester_id"], raw["request"], raw["setup"],
                     raw["expected_behavior"], raw["expected"], raw["pass_criteria"])


def _check_setup(cid: str, setup: Any, world: World, problems: list[str]) -> None:
    if not isinstance(setup, Mapping) or set(setup) - SETUP_KEYS:
        problems.append(f"{cid}: setup must be an object with keys from {sorted(SETUP_KEYS)}")
        return
    for uid in setup.get("verification", {}):
        if uid not in world.users:
            problems.append(f"{cid}: verification for unknown user {uid!r}")
    for response in setup.get("approvals", []):
        if response.get("status") not in ("approved", "denied"):
            problems.append(f"{cid}: approval status must be approved or denied")
        if response.get("approver_id") not in world.users:
            problems.append(f"{cid}: approval from unknown user {response.get('approver_id')!r}")
    for tid in setup.get("ticket_comments", {}):
        if tid not in world.tickets:
            problems.append(f"{cid}: comments for unknown ticket {tid!r}")
    for uid in setup.get("user_notes", {}):
        if uid not in world.users:
            problems.append(f"{cid}: notes for unknown user {uid!r}")
    faults = setup.get("faults", {})
    for tool, fault in {**faults.get("always", {}),
                        **{t: f for t, fs in faults.get("per_call", {}).items()
                           for f in fs if f}}.items():
        if tool not in REGISTRY or fault not in {f.value for f in Fault}:
            problems.append(f"{cid}: bad fault {tool!r}: {fault!r}")


def _check_expected(cid: str, expected: Any, problems: list[str]) -> None:
    if not isinstance(expected, Mapping) or set(expected) - EXPECTED_KEYS:
        problems.append(f"{cid}: expected must be an object with keys from {sorted(EXPECTED_KEYS)}")
        return
    required = {"final_status", "required_calls", "forbidden_tools", "effects",
                "expected_denials", "max_steps"}
    if required - set(expected):
        problems.append(f"{cid}: expected is missing {sorted(required - set(expected))}")
        return
    if not expected["final_status"] or set(expected["final_status"]) - set(FINAL_STATUSES):
        problems.append(f"{cid}: final_status must be a non-empty subset of {FINAL_STATUSES}")
    for call in expected["required_calls"]:
        if call.get("tool") not in REGISTRY or not isinstance(call.get("arguments"), Mapping):
            problems.append(f"{cid}: required call needs a known tool and an arguments object")
    for tool in expected["forbidden_tools"]:
        if tool not in REGISTRY:
            problems.append(f"{cid}: forbidden tool {tool!r} is not in the registry")
    for kind, count in expected["effects"].items():
        if kind not in EFFECT_KINDS or not isinstance(count, int) or count < 0:
            problems.append(f"{cid}: effects[{kind!r}] must be a known effect kind with a count")
    for denial in expected["expected_denials"]:
        if set(denial) != DENIAL_KEYS or denial["tool"] not in REGISTRY:
            problems.append(f"{cid}: expected denial needs exactly {sorted(DENIAL_KEYS)}")
    if not isinstance(expected["max_steps"], int) or expected["max_steps"] < 1:
        problems.append(f"{cid}: max_steps must be a positive integer")
    if (not expected.get("forbidden_tools") and not expected.get("expected_denials")
            and not expected["effects"]):
        problems.append(f"{cid}: needs at least one safety expectation")


def build_sandbox(case: AgentCase, world: World) -> tuple[Sandbox, FaultPlan]:
    """A fresh sandbox for one case, with the case's scripts applied."""
    setup = case.setup
    users = [User(uid, u["name"], Role(u["role"]), u["manager_id"],
                  setup.get("user_notes", {}).get(uid, "")) for uid, u in world.users.items()]
    tickets = [Ticket(tid, t["owner_id"], t["summary"],
                      comments=list(setup.get("ticket_comments", {}).get(tid, [])))
               for tid, t in world.tickets.items()]
    passages = tuple(KBPassage(p["doc_id"], p["text"]) for p in setup.get("kb_passages", []))
    sandbox = Sandbox(
        requester_id=case.requester_id, users=users, tickets=tickets,
        access_catalogue=world.access_catalogue, admin_resources=world.admin_resources,
        verification_script=setup.get("verification", {}),
        approval_script=[ApprovalResponse(a["status"], a["approver_id"], a.get("comment", ""))
                         for a in setup.get("approvals", [])],
        kb_search=lambda query: passages)
    faults = setup.get("faults", {})
    plan = FaultPlan(
        per_call={tool: [Fault(f) if f else None for f in fs]
                  for tool, fs in faults.get("per_call", {}).items()},
        always={tool: Fault(f) for tool, f in faults.get("always", {}).items()},
        **({"error_text": setup["fault_error_text"]} if "fault_error_text" in setup else {}))
    return sandbox, plan
