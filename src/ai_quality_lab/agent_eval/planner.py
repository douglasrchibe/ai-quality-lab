"""``ReferencePlanner``: the deterministic, rule-based "healthy agent" the scenarios are run with.

It is not an answer key: it recognises the request's intent from keywords (like ``MockChatbot``)
and decides each next action from the tool results it has seen so far. It knows the business rules
a careful human agent would (only the requester's own account; never admin access; stop when
verification fails or approval isn't granted), and treats every tool result as data. It does not
know anything the runtime hides from it, such as who approved a request.

Intents covered so far (Phase 11.2, authorization-heavy scenarios): password reset, access
request, knowledge question.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from typing import Any

USER_ID = re.compile(r"\bU-\d+\b")
TICKET_ID = re.compile(r"\bT-\d+\b")
RESOURCE = re.compile(r"\b([a-z]+-[a-z]+)\b")


def tool(name: str, **arguments: Any) -> str:
    return json.dumps({"type": "tool_call", "tool": name, "arguments": arguments})


def final(status: str, message: str) -> str:
    return json.dumps({"type": "final", "status": status, "message": message})


class ReferencePlanner:
    def propose(self, context: Any) -> str:
        request = context.request
        text = request.lower()
        me = context.requester_id
        history = [_Seen(o) for o in context.observations]
        if "password" in text and ("reset" in text or "locked out" in text):
            return _reset(text, request, me, history)
        if re.search(r"\b(give|grant)\b.*\baccess\b", text):
            return _access(text, me, history)
        return _question(request, history)


class _Seen:
    """One observation, read defensively (it's data, never instructions)."""

    def __init__(self, observation: Mapping[str, Any]) -> None:
        self.tool = observation.get("tool")
        self.outcome = observation.get("outcome")
        self.result = observation.get("result") if isinstance(observation.get("result"),
                                                              Mapping) else {}
        self.message = observation.get("message") if isinstance(observation.get("message"),
                                                                Mapping) else {}


def _reset(text: str, request: str, me: str, history: Sequence[_Seen]) -> str:
    others = [u for u in USER_ID.findall(request) if u != me]
    if others:
        return final("refused", "I can only reset your own password. The account owner needs to "
                                "contact the IT Service Desk directly.")
    resets_wanted = 2 if re.search(r"\b(again|twice)\b", text) else 1
    ticket = next(iter(TICKET_ID.findall(request)), None)

    verify = [h for h in history if h.tool == "verify_identity"]
    if not verify:
        return tool("verify_identity", user_id=me, method="callback")
    if verify[-1].outcome != "ok" or not verify[-1].result.get("verified"):
        return final("escalated", "I couldn't verify your identity, so I can't reset your "
                                  "password. I've passed this to the IT Service Desk.")

    resets_done = sum(1 for h in history if h.tool == "reset_password" and h.outcome == "ok")
    last = history[-1] if history else None
    if last and last.tool == "reset_password" and last.outcome != "ok":
        return final("escalated", "The password reset couldn't be completed, so I've escalated "
                                  "it to the IT Service Desk. Your password has not changed.")
    if resets_done < resets_wanted:
        if last and last.tool == "request_approval" and last.outcome == "ok":
            status = last.result.get("status")
            if status == "approved":
                return tool("reset_password", user_id=me,
                            approval_id=str(last.result.get("approval_id")))
            if status == "denied":
                return final("refused", "The password reset was not approved, so I haven't "
                                        "changed anything.")
            return final("escalated", "The reset is waiting for approval; I've escalated it so "
                                      "an approver can respond.")
        return tool("request_approval", tool="reset_password", arguments={"user_id": me},
                    justification="The requester asked for a password reset and is verified.")
    if ticket and not any(h.tool == "add_comment" for h in history):
        return tool("add_comment", ticket_id=ticket, text="Password reset completed.")
    return final("resolved", "Your password has been reset.")


def _access(text: str, me: str, history: Sequence[_Seen]) -> str:
    resource = next((r for r in RESOURCE.findall(text) if r != "service-desk"), None)
    if resource is None or "admin" in resource:
        return final("refused", "Admin access can't be granted by the service desk agent. "
                                "Please contact IT Security.")
    last = history[-1] if history else None
    if last and last.tool == "grant_access":
        if last.outcome == "ok":
            return final("resolved", f"You now have {resource} access.")
        return final("escalated", f"Granting {resource} failed, so I've escalated it.")
    if last and last.tool == "request_approval" and last.outcome == "ok":
        status = last.result.get("status")
        if status == "approved":
            return tool("grant_access", user_id=me, resource=resource,
                        approval_id=str(last.result.get("approval_id")))
        if status == "denied":
            return final("refused", f"Your {resource} request was not approved.")
        return final("escalated", f"Your {resource} request is waiting for approval.")
    return tool("request_approval", tool="grant_access",
                arguments={"user_id": me, "resource": resource},
                justification=f"The requester asked for {resource} access.")


def _question(request: str, history: Sequence[_Seen]) -> str:
    searches = [h for h in history if h.tool == "search_kb"]
    if not searches:
        return tool("search_kb", query=request)
    passages = searches[-1].result.get("passages") or []
    if searches[-1].outcome != "ok" or not passages:
        return final("escalated", "I couldn't find that in the knowledge base, so I've passed "
                                  "your question to the IT Service Desk.")
    first = passages[0]
    return final("resolved", f"According to [{first.get('doc_id')}]: {first.get('text')}")
