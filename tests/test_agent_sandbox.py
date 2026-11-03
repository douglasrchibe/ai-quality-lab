"""The sandbox: stage-4 argument validation, idempotency, scripted approvals and verification,
the effect log, and the snapshot used for final-state assertions."""

import json

import pytest

from ai_quality_lab.agent.sandbox import ApprovalResponse, Sandbox, User
from ai_quality_lab.agent.tools import Stage

TICKET = {"request_id": "r-1", "user_id": "U-100", "summary": "VPN down", "priority": "P1"}


def test_the_requester_must_be_a_sandbox_user():
    with pytest.raises(ValueError, match="not a sandbox user"):
        Sandbox(requester_id="U-999", users=[User("U-100", "Alex")])


@pytest.mark.parametrize("tool, arguments, fragment", [
    ("get_user", {"user_id": "U-999"}, "unknown user"),
    ("get_ticket", {"ticket_id": "T-999"}, "unknown ticket"),
    ("create_ticket", {**TICKET, "request_id": "r 1!"}, "malformed request_id"),
    ("request_approval", {"tool": "create_ticket", "arguments": TICKET, "justification": "x"},
     "only be requested for"),
    ("request_approval", {"tool": "reset_password", "arguments": {"user_id": "U-100", "x": "1"},
                          "justification": "x"}, "proposed reset_password arguments are invalid"),
    ("request_approval", {"tool": "reset_password", "arguments": {"user_id": "U-999"},
                          "justification": "x"}, "unknown user"),
])
def test_stage_4_rejects_arguments_that_dont_match_the_world(agent_sandbox, tool, arguments,
                                                             fragment):
    outcome = agent_sandbox().arguments_stage(tool, arguments)
    assert (outcome.stage, outcome.ok, outcome.code) == (Stage.ARGUMENTS, False, "argument_error")
    assert fragment in outcome.detail


def test_stage_4_accepts_arguments_that_match_the_world(agent_sandbox):
    outcome = agent_sandbox().arguments_stage("get_ticket", {"ticket_id": "T-100"})
    assert (outcome.stage, outcome.ok, outcome.code, outcome.detail) == (Stage.ARGUMENTS, True,
                                                                         "ok", "")


def test_stage_4_details_dont_echo_free_text(agent_sandbox):
    outcome = agent_sandbox().arguments_stage("get_user", {"user_id": "my password is hunter2"})
    assert not outcome.ok and "hunter2" not in outcome.detail
    assert "<redacted, 22 chars>" in outcome.detail


def test_an_idempotency_key_replays_the_original_result_and_creates_nothing(agent_sandbox):
    sandbox = agent_sandbox()
    first, effects = sandbox.execute("create_ticket", TICKET)
    assert sandbox.arguments_stage("create_ticket", TICKET).ok  # the same request may be retried
    again, more = sandbox.execute("create_ticket", TICKET)
    assert (first["replayed"], again["replayed"]) == (False, True)
    assert again["ticket_id"] == first["ticket_id"]
    assert len(effects) == 1 and more == []


def test_an_idempotency_key_cant_be_reused_for_a_different_request(agent_sandbox):
    sandbox = agent_sandbox()
    sandbox.execute("create_ticket", TICKET)
    outcome = sandbox.arguments_stage("create_ticket", {**TICKET, "priority": "P3"})
    assert (outcome.ok, outcome.code) == (False, "argument_error")
    assert "already used for a different request" in outcome.detail


def test_approvals_follow_the_script_in_order_and_are_pending_without_a_response(agent_sandbox):
    sandbox = agent_sandbox(approval_script=[
        ApprovalResponse("approved", "U-200", "Fine by me."), ApprovalResponse("denied", "U-200")])
    request = {"tool": "reset_password", "arguments": {"user_id": "U-100"}, "justification": "x"}
    results = [sandbox.execute("request_approval", request)[0] for _ in range(3)]
    assert [r["status"] for r in results] == ["approved", "denied", "pending"]
    assert [r["approval_id"] for r in results] == ["APR-1", "APR-2", "APR-3"]
    assert results[0]["comment"] == "Fine by me."
    assert sandbox.approvals["APR-3"].approver_id is None


def test_verification_succeeds_only_when_the_script_says_so(agent_sandbox):
    sandbox = agent_sandbox(verification_script={"U-100": False})
    assert sandbox.execute("verify_identity", {"user_id": "U-100", "method": "callback"})[0] == \
        {"verified": False}
    assert sandbox.verified == set()
    sandbox = agent_sandbox(verification_script={"U-100": True})
    result, effects = sandbox.execute("verify_identity", {"user_id": "U-100", "method": "callback"})
    assert result == {"verified": True} and sandbox.verified == {"U-100"}
    assert [e.kind for e in effects] == ["identity_verified"]


def test_consequential_tools_consume_their_approval_and_log_an_effect(agent_sandbox):
    sandbox = agent_sandbox(approval_script=[ApprovalResponse("approved", "U-200")])
    sandbox.execute("request_approval", {"tool": "reset_password",
                                         "arguments": {"user_id": "U-100"}, "justification": "x"})
    _, effects = sandbox.execute("reset_password", {"user_id": "U-100", "approval_id": "APR-1"})
    assert sandbox.approvals["APR-1"].used is True
    assert sandbox.password_resets == ["U-100"]
    assert [e.to_dict() for e in effects] == [
        {"kind": "password_reset", "user_id": "U-100", "approval_id": "APR-1"}]


def test_the_snapshot_is_json_ready_and_deterministic(agent_sandbox):
    def build():
        sandbox = agent_sandbox()
        sandbox.execute("create_ticket", TICKET)
        sandbox.execute("add_comment", {"ticket_id": "T-100", "text": "Still slow."})
        return sandbox.snapshot()

    first = build()
    assert json.dumps(first, sort_keys=True) == json.dumps(build(), sort_keys=True)
    assert first["tickets"]["T-1001"] == {"owner_id": "U-100", "priority": "P1", "status": "open",
                                          "comments": 0}
    assert [e["kind"] for e in first["effects"]] == ["ticket_created", "comment_added"]


def test_the_sandbox_copies_its_inputs(agent_sandbox):
    users = [User("U-100", "Alex")]
    sandbox = Sandbox(requester_id="U-100", users=users)
    sandbox.users["U-100"].notes = "changed"
    assert users[0].notes == ""
