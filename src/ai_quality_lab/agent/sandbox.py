"""The synthetic, in-memory world the agent acts on, and the tool implementations.

The sandbox holds the *authoritative* structured state: users with roles and managers, tickets,
the access catalogue, identity-verification results, approval records, and an append-only effect
log. Authorization (``policy.py``) reads only this state, never text a tool returned to the planner.

Scenario scripts make everything deterministic:
* ``verification_script``: whether a callback verification succeeds, per user (missing = fails)
* ``approval_script``: the "human" approver's responses, consumed in order
  (missing = no response, so the approval stays pending)
* ``kb_search``: an injected search function (the caller wires the Phase 6 retriever; tests a fake)
"""

from __future__ import annotations

import copy
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ai_quality_lab.agent.tools import (
    CONSEQUENTIAL_TOOLS,
    REGISTRY,
    REQUEST_ID,
    Stage,
    StageError,
    StageOutcome,
    accept,
    safe_repr,
    schema_stage,
)


class Role(str, Enum):
    EMPLOYEE = "employee"
    MANAGER = "manager"


@dataclass
class User:
    user_id: str
    name: str
    role: Role = Role.EMPLOYEE
    manager_id: str | None = None
    notes: str = ""  # free text: untrusted when returned to the planner


@dataclass
class Ticket:
    ticket_id: str
    owner_id: str
    summary: str
    priority: str = "P3"
    status: str = "open"
    comments: list[str] = field(default_factory=list)  # free text: untrusted


@dataclass(frozen=True)
class ApprovalResponse:
    """One scripted answer from the human approver."""
    status: str  # "approved" | "denied"
    approver_id: str
    comment: str = ""  # free text from the approver: untrusted when returned to the planner


@dataclass
class Approval:
    approval_id: str
    tool: str
    arguments: dict[str, Any]
    status: str  # "approved" | "denied" | "pending"
    approver_id: str | None
    used: bool = False


@dataclass(frozen=True)
class KBPassage:
    doc_id: str
    text: str


@dataclass(frozen=True)
class Effect:
    """A state change. ``kind``: ticket_created, comment_added, identity_verified,
    approval_requested, password_reset, access_granted, escalated."""
    kind: str
    details: Mapping[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {"kind": self.kind, **dict(self.details)}


class Sandbox:
    def __init__(self, *, requester_id: str, users: Sequence[User], tickets: Sequence[Ticket] = (),
                 access_catalogue: Sequence[str] = (), admin_resources: Sequence[str] = (),
                 verification_script: Mapping[str, bool] | None = None,
                 approval_script: Sequence[ApprovalResponse] = (),
                 kb_search: Callable[[str], Sequence[KBPassage]] | None = None) -> None:
        self.users = {u.user_id: copy.deepcopy(u) for u in users}
        if requester_id not in self.users:
            raise ValueError(f"requester {requester_id!r} is not a sandbox user")
        self.requester_id = requester_id
        self.tickets = {t.ticket_id: copy.deepcopy(t) for t in tickets}
        self.access_catalogue = tuple(access_catalogue)
        self.admin_resources = tuple(admin_resources)
        self._verification_script = dict(verification_script or {})
        self._approval_script = list(approval_script)
        self._kb_search = kb_search or (lambda query: ())
        self.verified: set[str] = set()
        self.approvals: dict[str, Approval] = {}
        self.grants: list[tuple[str, str]] = []
        self.password_resets: list[str] = []
        self.effects: list[Effect] = []
        self._requests: dict[str, tuple[dict[str, Any], str]] = {}  # request_id -> (args, ticket)
        self._next_ticket = 1001
        self._next_approval = 1

    @property
    def requester(self) -> User:
        return self.users[self.requester_id]

    # --- Stage 4: argument validation against the current state --------------------------------

    def arguments_stage(self, tool: str, arguments: Mapping[str, Any]) -> StageOutcome:
        """Stage 4. Referenced users and tickets must exist; idempotency keys must be well formed
        and not reused for a different request."""
        try:
            self._validate_arguments(tool, arguments)
        except StageError as exc:
            return exc.outcome(Stage.ARGUMENTS)
        return accept(Stage.ARGUMENTS)

    def _validate_arguments(self, tool: str, arguments: Mapping[str, Any]) -> None:
        if "user_id" in arguments and arguments["user_id"] not in self.users:
            raise StageError("argument_error", f"unknown user {safe_repr(arguments['user_id'])}")
        if "ticket_id" in arguments and arguments["ticket_id"] not in self.tickets:
            raise StageError("argument_error", f"unknown ticket {safe_repr(arguments['ticket_id'])}")
        if tool == "create_ticket":
            request_id = arguments["request_id"]
            if not REQUEST_ID.match(request_id):
                raise StageError("argument_error", f"malformed request_id {safe_repr(request_id)}")
            seen = self._requests.get(request_id)
            if seen and seen[0] != dict(arguments):
                raise StageError("argument_error",
                                 f"request_id {safe_repr(request_id)} was already used for a "
                                 "different request")
        if tool == "request_approval":
            target, target_args = arguments["tool"], arguments["arguments"]
            if target not in CONSEQUENTIAL_TOOLS:
                raise StageError("argument_error",
                                 f"approval can only be requested for {list(CONSEQUENTIAL_TOOLS)}")
            schema_args = {**target_args, "approval_id": "pending"}  # the id doesn't exist yet
            proposed = schema_stage(REGISTRY[target], schema_args)
            if not proposed.ok:
                raise StageError("argument_error",
                                 f"proposed {target} arguments are invalid: {proposed.detail}")
            self._validate_arguments(target, target_args)

    # --- Stage 6: execution (fault injection is applied by the runtime around this) ------------

    def execute(self, tool: str, arguments: Mapping[str, Any]) -> tuple[dict[str, Any], list[Effect]]:
        """Run a tool that already passed stages 1–5. Returns (result, new effects)."""
        handler = getattr(self, f"_tool_{tool}")
        before = len(self.effects)
        result = handler(dict(arguments))
        return result, self.effects[before:]

    def _effect(self, kind: str, **details: Any) -> None:
        self.effects.append(Effect(kind, details))

    def _tool_search_kb(self, args: dict[str, Any]) -> dict[str, Any]:
        passages = self._kb_search(args["query"])
        return {"passages": [{"doc_id": p.doc_id, "text": p.text} for p in passages]}

    def _tool_get_user(self, args: dict[str, Any]) -> dict[str, Any]:
        user = self.users[args["user_id"]]
        return {"user_id": user.user_id, "name": user.name, "role": user.role.value,
                "manager_id": user.manager_id, "notes": user.notes}

    def _tool_get_ticket(self, args: dict[str, Any]) -> dict[str, Any]:
        ticket = self.tickets[args["ticket_id"]]
        return {"ticket_id": ticket.ticket_id, "owner_id": ticket.owner_id,
                "summary": ticket.summary, "priority": ticket.priority, "status": ticket.status,
                "comments": list(ticket.comments)}

    def _tool_verify_identity(self, args: dict[str, Any]) -> dict[str, Any]:
        user_id = args["user_id"]
        if self._verification_script.get(user_id, False):
            self.verified.add(user_id)
            self._effect("identity_verified", user_id=user_id)
            return {"verified": True}
        return {"verified": False}

    def _tool_create_ticket(self, args: dict[str, Any]) -> dict[str, Any]:
        request_id = args["request_id"]
        if request_id in self._requests:  # idempotent replay: the original result, no new effect
            return {"ticket_id": self._requests[request_id][1], "replayed": True}
        ticket_id = f"T-{self._next_ticket}"
        self._next_ticket += 1
        self.tickets[ticket_id] = Ticket(ticket_id, args["user_id"], args["summary"],
                                         args["priority"])
        self._requests[request_id] = (args, ticket_id)
        self._effect("ticket_created", ticket_id=ticket_id, request_id=request_id,
                     user_id=args["user_id"], priority=args["priority"])
        return {"ticket_id": ticket_id, "replayed": False}

    def _tool_add_comment(self, args: dict[str, Any]) -> dict[str, Any]:
        self.tickets[args["ticket_id"]].comments.append(args["text"])
        self._effect("comment_added", ticket_id=args["ticket_id"])
        return {"ticket_id": args["ticket_id"], "comment_added": True}

    def _tool_request_approval(self, args: dict[str, Any]) -> dict[str, Any]:
        approval_id = f"APR-{self._next_approval}"
        self._next_approval += 1
        response = self._approval_script.pop(0) if self._approval_script else None
        approval = Approval(approval_id, args["tool"], dict(args["arguments"]),
                            response.status if response else "pending",
                            response.approver_id if response else None)
        self.approvals[approval_id] = approval
        self._effect("approval_requested", approval_id=approval_id, tool=args["tool"],
                     status=approval.status)
        return {"approval_id": approval_id, "status": approval.status,
                "comment": response.comment if response else ""}

    def _tool_reset_password(self, args: dict[str, Any]) -> dict[str, Any]:
        self._consume_approval(args.get("approval_id"))
        self.password_resets.append(args["user_id"])
        self._effect("password_reset", user_id=args["user_id"], approval_id=args["approval_id"])
        return {"user_id": args["user_id"], "password_reset": True}

    def _tool_grant_access(self, args: dict[str, Any]) -> dict[str, Any]:
        self._consume_approval(args.get("approval_id"))
        self.grants.append((args["user_id"], args["resource"]))
        self._effect("access_granted", user_id=args["user_id"], resource=args["resource"],
                     approval_id=args["approval_id"])
        return {"user_id": args["user_id"], "resource": args["resource"], "granted": True}

    def _tool_escalate_to_human(self, args: dict[str, Any]) -> dict[str, Any]:
        self._effect("escalated", reason=args["reason"], ticket_id=args.get("ticket_id"))
        return {"escalated": True}

    def _consume_approval(self, approval_id: str | None) -> None:
        approval = self.approvals.get(approval_id or "")
        if approval is not None:  # with enforcement off, an execution may have no valid approval
            approval.used = True

    def snapshot(self) -> dict[str, Any]:
        """The final state for assertions: JSON-ready and deterministic."""
        return {
            "tickets": {t.ticket_id: {"owner_id": t.owner_id, "priority": t.priority,
                                      "status": t.status, "comments": len(t.comments)}
                        for t in sorted(self.tickets.values(), key=lambda t: t.ticket_id)},
            "password_resets": list(self.password_resets),
            "grants": [list(g) for g in self.grants],
            "verified": sorted(self.verified),
            "approvals": {a.approval_id: {"tool": a.tool, "status": a.status, "used": a.used}
                          for a in self.approvals.values()},
            "effects": [e.to_dict() for e in self.effects],
        }
