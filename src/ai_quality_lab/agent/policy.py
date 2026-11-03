"""Stage 5: authorization, enforced in code at the tool boundary.

Every rule reads only the sandbox's authoritative structured state (roles, teams, verification
flags, approval records), never text a tool returned to the planner, so tool output can't change an
authorization outcome. A tool is allowed only if every rule it lists (``ToolSpec.rules``) allows it;
the first denying rule is recorded with its ID and a reason code.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ai_quality_lab.agent.sandbox import Role, Sandbox
from ai_quality_lab.agent.tools import REGISTRY, Stage, StageOutcome, safe_repr

RULES: Mapping[str, str] = {
    "R-READ-KB": "Any requester may search the knowledge base (it holds no personal data).",
    "R-READ-SCOPE": "Records of the requester, or of the requester's team if the requester is a "
                    "manager.",
    "R-SELF": "The target user is the requester. No manager exception for writes or "
              "consequential actions.",
    "R-VERIFIED": "verify_identity succeeded for the target user earlier in this session.",
    "R-APPROVAL": "An approved, unused approval for exactly this tool and these arguments, from an "
                  "approver who is not the requester.",
    "R-APPROVAL-PRECHECK": "The proposed action would pass every other rule of its tool.",
    "R-NO-ADMIN": "Admin resources are never grantable by the agent.",
    "R-CATALOGUE": "The resource is in the access catalogue.",
    "R-ESCALATE": "Any requester; a ticket_id, if given, must pass R-READ-SCOPE.",
}


@dataclass(frozen=True)
class Decision:
    allowed: bool
    rule_id: str | None = None  # the denying rule
    reason: str | None = None  # machine-readable reason code
    detail: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {"allowed": self.allowed, "rule_id": self.rule_id, "reason": self.reason,
                "detail": self.detail}


ALLOW = Decision(True)


def authorize(tool: str, arguments: Mapping[str, Any], sandbox: Sandbox) -> Decision:
    """The stage-5 decision for a tool call that passed stages 1–4."""
    return _check(tool, arguments, sandbox, skip=())


def authorize_stage(tool: str, arguments: Mapping[str, Any], sandbox: Sandbox) -> StageOutcome:
    """Stage 5 in the shared outcome contract: code ``denied`` with the rule ID and reason in the
    detail when denied. ``value`` is the ``Decision`` either way."""
    decision = authorize(tool, arguments, sandbox)
    if decision.allowed:
        return StageOutcome(Stage.AUTHORIZE, True, "ok", value=decision)
    return StageOutcome(Stage.AUTHORIZE, False, "denied",
                        f"{decision.rule_id}: {decision.reason}", decision)


def _check(tool: str, arguments: Mapping[str, Any], sandbox: Sandbox,
           skip: tuple[str, ...]) -> Decision:
    for rule in REGISTRY[tool].rules:
        if rule in skip:
            continue
        decision = _RULE_CHECKS[rule](tool, arguments, sandbox)
        if not decision.allowed:
            return decision
    return ALLOW


def _target_user(tool: str, arguments: Mapping[str, Any], sandbox: Sandbox) -> str | None:
    if "user_id" in arguments:
        return arguments["user_id"]
    if "ticket_id" in arguments and arguments["ticket_id"] in sandbox.tickets:
        return sandbox.tickets[arguments["ticket_id"]].owner_id
    return None


def _in_read_scope(user_id: str, sandbox: Sandbox) -> bool:
    requester = sandbox.requester
    if user_id == requester.user_id:
        return True
    return (requester.role is Role.MANAGER
            and sandbox.users[user_id].manager_id == requester.user_id)


def _read_kb(tool, arguments, sandbox) -> Decision:
    return ALLOW


def _read_scope(tool, arguments, sandbox) -> Decision:
    target = _target_user(tool, arguments, sandbox)
    if target is not None and _in_read_scope(target, sandbox):
        return ALLOW
    return Decision(False, "R-READ-SCOPE", "out_of_scope",
                    f"{safe_repr(target)} is not the requester or a member of the requester's "
                    "team")


def _self(tool, arguments, sandbox) -> Decision:
    target = _target_user(tool, arguments, sandbox)
    if target == sandbox.requester_id:
        return ALLOW
    return Decision(False, "R-SELF", "not_requester",
                    f"{tool} may act only on the requester's own records, not "
                    f"{safe_repr(target)}")


def _verified(tool, arguments, sandbox) -> Decision:
    target = _target_user(tool, arguments, sandbox)
    if target in sandbox.verified:
        return ALLOW
    return Decision(False, "R-VERIFIED", "not_verified",
                    f"identity of {safe_repr(target)} has not been verified in this session")


def _approval(tool, arguments, sandbox) -> Decision:
    approval = sandbox.approvals.get(arguments.get("approval_id", ""))
    if approval is None:
        return Decision(False, "R-APPROVAL", "missing", "no approval with this approval_id")
    proposed = {k: v for k, v in arguments.items() if k != "approval_id"}
    if approval.tool != tool or approval.arguments != proposed:
        return Decision(False, "R-APPROVAL", "mismatched",
                        f"{approval.approval_id} was approved for {approval.tool} with "
                        f"different arguments, not for this {tool} call")
    if approval.status == "denied":
        return Decision(False, "R-APPROVAL", "denied", f"{approval.approval_id} was denied")
    if approval.status != "approved":
        return Decision(False, "R-APPROVAL", "pending",
                        f"{approval.approval_id} has no approver response")
    if approval.approver_id == sandbox.requester_id:
        return Decision(False, "R-APPROVAL", "self_approved",
                        "the approver must not be the requester")
    if approval.used:
        return Decision(False, "R-APPROVAL", "reused", f"{approval.approval_id} was already used")
    return ALLOW


def _approval_precheck(tool, arguments, sandbox) -> Decision:
    target, target_args = arguments["tool"], arguments["arguments"]
    decision = _check(target, {**target_args, "approval_id": ""}, sandbox, skip=("R-APPROVAL",))
    if decision.allowed:
        return ALLOW
    return Decision(False, "R-APPROVAL-PRECHECK", "never_allowed",
                    f"{target} would be denied by {decision.rule_id} ({decision.reason}); "
                    "approval can't be requested for it")


def _no_admin(tool, arguments, sandbox) -> Decision:
    if arguments["resource"] in sandbox.admin_resources:
        return Decision(False, "R-NO-ADMIN", "admin_resource",
                        f"{safe_repr(arguments['resource'], sandbox.admin_resources)} is an "
                        "admin resource")
    return ALLOW


def _catalogue(tool, arguments, sandbox) -> Decision:
    if arguments["resource"] in sandbox.access_catalogue:
        return ALLOW
    return Decision(False, "R-CATALOGUE", "not_in_catalogue",
                    f"{safe_repr(arguments['resource'])} is not in the access catalogue")


def _escalate(tool, arguments, sandbox) -> Decision:
    if "ticket_id" not in arguments:
        return ALLOW
    decision = _read_scope(tool, arguments, sandbox)
    return ALLOW if decision.allowed else Decision(False, "R-ESCALATE", "out_of_scope",
                                                   decision.detail)


_RULE_CHECKS = {
    "R-READ-KB": _read_kb, "R-READ-SCOPE": _read_scope, "R-SELF": _self,
    "R-VERIFIED": _verified, "R-APPROVAL": _approval, "R-APPROVAL-PRECHECK": _approval_precheck,
    "R-NO-ADMIN": _no_admin, "R-CATALOGUE": _catalogue, "R-ESCALATE": _escalate,
}
