"""The agent loop: stage outcomes in the trace (AC2), limits, loops, idempotency and separate denial
counts (AC4), faults and enforcement-off execution, determinism and the I/O boundary (AC5)."""

import ast
import builtins
import io
import json
import socket
import subprocess
import urllib.request
from pathlib import Path

import pytest

from ai_quality_lab.agent.faults import Fault, FaultPlan
from ai_quality_lab.agent.runtime import (
    DenialCounts,
    ExpectedDenial,
    RunStatus,
    Stage,
    count_denials,
    run_agent,
)
from ai_quality_lab.agent.sandbox import ApprovalResponse, KBPassage

AGENT_DIR = Path(__file__).resolve().parents[1] / "src" / "ai_quality_lab" / "agent"
ALL_STAGES = ["parse", "select", "schema", "arguments", "authorize", "execute", "result"]
TICKET = {"request_id": "r-1", "user_id": "U-100", "summary": "VPN down", "priority": "P1"}


def stages(step):
    return [(s.stage.value, s.code) for s in step.stages]


# --- AC2: every stage outcome is in the trace ----------------------------------------------------

def test_an_executed_call_records_all_seven_stages_in_order(agent_sandbox, replay, call):
    run = run_agent(replay([call("get_user", user_id="U-100")]), agent_sandbox(), "who am I?")
    step = run.steps[0]
    assert [s.stage.value for s in step.stages] == ALL_STAGES
    assert all(s.ok for s in step.stages) and step.executed and step.stopped_at is None
    assert step.observation["untrusted_data"] is True and step.observation["outcome"] == "ok"
    assert run.status is RunStatus.RESOLVED and len(run.steps) == 2


@pytest.mark.parametrize("raw, stopped_at, code", [
    ("not json", Stage.PARSE, "parse_error"),
    ({"type": "tool_call", "tool": "delete_user", "arguments": {}}, Stage.SELECT, "unknown_tool"),
    ({"type": "tool_call", "tool": "create_ticket",
      "arguments": {**TICKET, "priority": "urgent!!!"}}, Stage.SCHEMA, "schema_error"),
    ({"type": "tool_call", "tool": "get_ticket", "arguments": {"ticket_id": "T-999"}},
     Stage.ARGUMENTS, "argument_error"),
    ({"type": "tool_call", "tool": "get_user", "arguments": {"user_id": "U-101"}},
     Stage.AUTHORIZE, "denied"),
])
def test_a_rejected_action_stops_at_its_stage_has_no_effect_and_is_traced(agent_sandbox, replay,
                                                                          raw, stopped_at, code):
    run = run_agent(replay([raw]), agent_sandbox(), "x")
    step = run.steps[0]
    assert step.stopped_at is stopped_at and stages(step)[-2] == (stopped_at.value, code)
    assert stages(step)[-1] == ("result", "delivered")  # the planner is told, as a runtime message
    assert not step.executed and step.effects == [] and run.final_state["effects"] == []
    assert step.observation["untrusted_data"] is False and step.observation["outcome"] == code


def test_the_trace_is_json_ready_and_names_the_denying_rule(agent_sandbox, replay, call):
    run = run_agent(replay([call("get_user", user_id="U-101")]), agent_sandbox(), "x")
    data = json.loads(json.dumps(run.to_dict()))
    assert data["steps"][0]["stopped_at"] == "authorize"
    assert data["steps"][0]["decision"] == {"allowed": False, "rule_id": "R-READ-SCOPE",
                                            "reason": "out_of_scope",
                                            "detail": data["steps"][0]["decision"]["detail"]}
    assert data["contract_fingerprint"] == run.contract_fingerprint


def test_a_final_action_ends_the_run_with_its_status_and_message(agent_sandbox, replay):
    run = run_agent(replay([{"type": "final", "status": "refused", "message": "Not allowed."}]),
                    agent_sandbox(), "x")
    assert (run.status, run.final_message, len(run.steps)) == (RunStatus.REFUSED, "Not allowed.", 1)


def test_the_planner_sees_the_request_and_every_observation(agent_sandbox, replay, call):
    planner = replay([call("get_user", user_id="U-100"), "garbage"])
    run_agent(planner, agent_sandbox(), "who am I?")
    last = planner.contexts[-1]
    assert (last.request, last.requester_id, last.step) == ("who am I?", "U-100", 2)
    assert [o["outcome"] for o in last.observations] == ["ok", "parse_error"]


# --- AC4: limits, loops, idempotency, separate denial counts -------------------------------------

def test_the_step_limit_ends_a_run_without_a_final_action(agent_sandbox, replay, call):
    run = run_agent(replay([call("search_kb", query=f"q{i}") for i in range(20)]),
                    agent_sandbox(), "x", step_limit=5)
    assert run.status is RunStatus.STEP_LIMIT and len(run.steps) == 5 and run.final_message == ""


def test_consecutive_invalid_actions_end_the_run_but_a_valid_one_resets_the_count(
        agent_sandbox, replay, call):
    run = run_agent(replay(["bad", call("search_kb", query="vpn"), "bad", "bad"]),
                    agent_sandbox(), "x")
    assert run.status is RunStatus.INVALID_ACTIONS and len(run.steps) == 4
    denied = run_agent(replay([call("get_user", user_id="U-101")] * 3), agent_sandbox(), "x")
    assert denied.status is RunStatus.RESOLVED  # a denial is not an invalid action


def test_repeated_identical_calls_are_flagged(agent_sandbox, replay, call):
    run = run_agent(replay([call("get_ticket", ticket_id="T-100"),
                            call("get_ticket", ticket_id="T-100"),
                            call("get_ticket", ticket_id="T-100")]), agent_sandbox(), "x")
    assert [s.repeated for s in run.steps[:3]] == [False, True, True]


def test_a_retry_with_the_same_key_after_a_timeout_creates_exactly_one_ticket(
        agent_sandbox, replay, call):
    faults = FaultPlan(per_call={"create_ticket": [Fault.TIMEOUT_AFTER_EFFECT]})
    run = run_agent(replay([call("create_ticket", **TICKET), call("create_ticket", **TICKET)]),
                    agent_sandbox(), "x", faults=faults)
    assert stages(run.steps[0])[-2] == ("execute", "timeout") and run.steps[0].executed
    assert run.steps[1].observation["result"] == {"ticket_id": "T-1001", "replayed": True}
    assert [e["kind"] for e in run.final_state["effects"]] == ["ticket_created"]


def test_a_retry_with_a_new_key_after_a_timeout_shows_up_as_a_duplicate(agent_sandbox, replay,
                                                                         call):
    faults = FaultPlan(per_call={"create_ticket": [Fault.TIMEOUT_AFTER_EFFECT]})
    run = run_agent(replay([call("create_ticket", **TICKET),
                            call("create_ticket", **{**TICKET, "request_id": "r-2"})]),
                    agent_sandbox(), "x", faults=faults)
    created = [e for e in run.final_state["effects"] if e["kind"] == "ticket_created"]
    assert [e["request_id"] for e in created] == ["r-1", "r-2"]  # the evaluator's duplicate signal


def test_unsafe_attempts_and_expected_denials_are_counted_separately(agent_sandbox, replay, call):
    run = run_agent(replay([call("get_ticket", ticket_id="T-300"),  # outside the manager's team
                            call("reset_password", user_id="U-100", approval_id="APR-9")]),
                    agent_sandbox(requester_id="U-200"), "x")
    probe = ExpectedDenial("R-READ-SCOPE", "get_ticket", "a manager checks a ticket number")
    assert count_denials(run) == (counts := count_denials(run, ()))
    assert (counts.unsafe_attempts, counts.expected_denials) == (2, 0)
    with_expected = count_denials(run, [probe])
    assert (with_expected.unsafe_attempts, with_expected.expected_denials,
            with_expected.unauthorized_executions) == (1, 1, 0)
    # One expected entry excuses one denial only.
    twice = run_agent(replay([call("get_ticket", ticket_id="T-300")] * 2),
                      agent_sandbox(requester_id="U-200"), "x")
    assert count_denials(twice, [probe]).unsafe_attempts == 1


# --- Faults and the test-only enforcement switch -------------------------------------------------

@pytest.mark.parametrize("fault, code", [(Fault.TIMEOUT_NO_EFFECT, "timeout"),
                                         (Fault.TOOL_ERROR, "tool_error"),
                                         (Fault.MALFORMED_RESULT, "malformed_result")])
def test_faults_without_effect_are_reported_and_change_nothing(agent_sandbox, replay, call, fault,
                                                               code):
    run = run_agent(replay([call("create_ticket", **TICKET)]), agent_sandbox(), "x",
                    faults=FaultPlan(always={"create_ticket": fault}))
    step = run.steps[0]
    assert stages(step)[-2] == ("execute", code) and not step.executed
    assert run.final_state["tickets"].keys() == {"T-100", "T-101", "T-300"}
    assert step.observation["untrusted_data"] is True


def test_with_enforcement_off_a_denied_action_executes_and_is_marked(agent_sandbox, replay, call):
    actions = [call("reset_password", user_id="U-100", approval_id="APR-1")]
    enforced = run_agent(replay(actions), agent_sandbox(), "x")
    unenforced = run_agent(replay(actions), agent_sandbox(), "x", policy_enforcement=False)
    assert enforced.final_state["password_resets"] == []
    assert unenforced.final_state["password_resets"] == ["U-100"]
    step = unenforced.steps[0]
    assert step.decision.rule_id == "R-VERIFIED" and step.executed and step.unauthorized_execution
    assert stages(step)[4] == ("authorize", "denied")  # the decision is still recorded
    counts = count_denials(unenforced)
    assert (counts.unsafe_attempts, counts.unauthorized_executions) == (1, 1)
    assert unenforced.policy_enforcement is False and enforced.policy_enforcement is True


def test_a_full_password_reset_workflow(agent_sandbox, replay, call):
    sandbox = agent_sandbox(verification_script={"U-100": True},
                            approval_script=[ApprovalResponse("approved", "U-200")])
    run = run_agent(replay([
        call("verify_identity", user_id="U-100", method="callback"),
        call("request_approval", tool="reset_password", arguments={"user_id": "U-100"},
             justification="Locked out"),
        call("reset_password", user_id="U-100", approval_id="APR-1"),
        call("add_comment", ticket_id="T-100", text="Password reset."),
        {"type": "final", "status": "resolved", "message": "Your password was reset."},
    ]), sandbox, "I'm locked out")
    assert run.status is RunStatus.RESOLVED
    assert [e["kind"] for e in run.final_state["effects"]] == [
        "identity_verified", "approval_requested", "password_reset", "comment_added"]
    assert count_denials(run) == DenialCounts(0, 0, 0)


# --- AC5: determinism and the I/O boundary -------------------------------------------------------

def scenario_run(agent_sandbox, replay, call):
    sandbox = agent_sandbox(verification_script={"U-100": True},
                            approval_script=[ApprovalResponse("approved", "U-200")],
                            kb_search=lambda q: [KBPassage("IT-001", "Report lost laptops.")])
    actions = [call("search_kb", query="lost laptop"), call("get_user", user_id="U-100"),
               call("get_ticket", ticket_id="T-100"),
               call("verify_identity", user_id="U-100", method="callback"),
               call("create_ticket", **TICKET), call("add_comment", ticket_id="T-100", text="x"),
               call("request_approval", tool="grant_access",
                    arguments={"user_id": "U-100", "resource": "crm-read"}, justification="x"),
               call("grant_access", user_id="U-100", resource="crm-read", approval_id="APR-1"),
               call("escalate_to_human", reason="x")]
    return run_agent(replay(actions), sandbox, "x", step_limit=10,
                     faults=FaultPlan(per_call={"create_ticket": [Fault.TIMEOUT_AFTER_EFFECT]}))


def test_identical_inputs_give_identical_traces(agent_sandbox, replay, call):
    first = json.dumps(scenario_run(agent_sandbox, replay, call).to_dict(), sort_keys=True)
    assert first == json.dumps(scenario_run(agent_sandbox, replay, call).to_dict(), sort_keys=True)


def test_a_run_using_every_tool_does_no_network_or_file_io(agent_sandbox, replay, call,
                                                           monkeypatch):
    def forbidden(*args, **kwargs):
        raise AssertionError("the agent sandbox must not do network or file I/O")

    for target, name in [(builtins, "open"), (io, "open"), (socket, "socket"),
                         (socket, "create_connection"), (urllib.request, "urlopen"),
                         (subprocess, "Popen"), (Path, "open"), (Path, "read_text"),
                         (Path, "write_text")]:
        monkeypatch.setattr(target, name, forbidden)
    run = scenario_run(agent_sandbox, replay, call)
    used = {s.action["tool"] for s in run.steps if s.action and "tool" in s.action}
    assert used == {"search_kb", "get_user", "get_ticket", "verify_identity", "create_ticket",
                    "add_comment", "request_approval", "grant_access", "escalate_to_human"}
    assert run.final_state["grants"] == [["U-100", "crm-read"]]


ALLOWED_IMPORTS = {"__future__", "collections", "copy", "dataclasses", "enum", "hashlib",
                   "itertools", "json", "re", "typing"}
ALLOWED_PROJECT_IMPORTS = {"ai_quality_lab.agent", "ai_quality_lab.rag.security"}
FORBIDDEN_CALLS = {"open", "exec", "eval", "compile", "__import__"}


def test_the_agent_package_imports_nothing_that_can_reach_the_network_or_filesystem():
    for path in sorted(AGENT_DIR.glob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                names = [alias.name for alias in node.names]
            elif isinstance(node, ast.ImportFrom):
                names = [node.module or ""]
            elif (isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                  and node.func.id in FORBIDDEN_CALLS):
                pytest.fail(f"{path.name}: calls {node.func.id}()")
            else:
                continue
            for name in names:
                if name.startswith("ai_quality_lab"):
                    assert any(name == p or name.startswith(p + ".")
                               for p in ALLOWED_PROJECT_IMPORTS), f"{path.name}: imports {name}"
                else:
                    assert name.split(".")[0] in ALLOWED_IMPORTS, f"{path.name}: imports {name}"
