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
    assert (with_expected.unsafe_attempts, with_expected.expected_denials) == (1, 1)
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


def test_a_denied_action_never_executes(agent_sandbox, replay, call):
    run = run_agent(replay([call("reset_password", user_id="U-100", approval_id="APR-1")]),
                    agent_sandbox(), "x")
    step = run.steps[0]
    assert step.decision.rule_id == "R-VERIFIED" and stages(step)[4] == ("authorize", "denied")
    assert not step.executed and step.execution is None
    assert run.final_state["password_resets"] == [] and run.final_state["effects"] == []


def test_with_a_broken_policy_the_runtime_executes_and_its_trace_hides_it(
        agent_sandbox, replay, call, broken_authorization):
    """Why an independent evaluator is needed: a broken stage 5 (test-only substitution) lets an
    unverified, unapproved reset execute, and the runtime's own trace reports nothing wrong."""
    run = run_agent(replay([call("reset_password", user_id="U-100", approval_id="APR-1")]),
                    agent_sandbox(), "x")
    step = run.steps[0]
    assert stages(step)[4] == ("authorize", "ok") and step.decision.allowed
    assert step.executed and run.final_state["password_resets"] == ["U-100"]
    assert count_denials(run) == DenialCounts(0, 0)  # the runtime sees no problem at all
    assert [d.rule_id for d in broken_authorization] == ["R-VERIFIED"]  # what policy would say


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
    assert count_denials(run) == DenialCounts(0, 0)


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


# --- Follow-up: run and scenario IDs ------------------------------------------------------------

# Version-1 fields minus the two that version 3 removed (policy_enforcement, unauthorized_execution).
V1_RUN_KEYS = {"status", "final_message", "contract_fingerprint", "steps", "final_state"}
V1_STEP_KEYS = {"index", "time", "raw", "action", "stages", "stopped_at", "decision", "executed",
                "effects", "observation", "repeated", "after_untrusted_instruction"}


def test_supplied_ids_are_on_the_run_and_every_step_and_serialized(agent_sandbox, replay, call):
    run = run_agent(replay([call("get_user", user_id="U-100"), "garbage"]), agent_sandbox(), "x",
                    run_id="eval-20261103-a1", scenario_id="AGT-005")
    data = json.loads(json.dumps(run.to_dict()))
    assert (data["run_id"], data["scenario_id"], data["run_id_source"]) == (
        "eval-20261103-a1", "AGT-005", "supplied")
    assert len(data["steps"]) == 3
    assert all((s["run_id"], s["scenario_id"]) == ("eval-20261103-a1", "AGT-005")
               for s in data["steps"])


def test_a_standalone_run_has_no_scenario_and_a_derived_deterministic_run_id(agent_sandbox,
                                                                             replay, call):
    def run(request):
        return run_agent(replay([call("get_user", user_id="U-100")]), agent_sandbox(), request)

    first, again, other = run("who am I?"), run("who am I?"), run("something else")
    assert first.scenario_id is None and first.run_id_source == "derived"
    assert first.run_id.startswith("derived-") and first.run_id == again.run_id
    assert other.run_id != first.run_id
    assert all(s.run_id == first.run_id and s.scenario_id is None for s in first.steps)


@pytest.mark.parametrize("field", ["run_id", "scenario_id"])
@pytest.mark.parametrize("value", ["", "has space", "x" * 81, "-leading-dash"])
def test_malformed_ids_are_refused(agent_sandbox, replay, field, value):
    with pytest.raises(ValueError, match=field):
        run_agent(replay([]), agent_sandbox(), "x", **{field: value})


def test_trace_version_3_keeps_every_other_earlier_field(agent_sandbox, replay, call):
    data = run_agent(replay([call("get_user", user_id="U-100")]), agent_sandbox(), "x").to_dict()
    assert data["trace_version"] == 3
    assert "policy_enforcement" not in data
    assert all("unauthorized_execution" not in s for s in data["steps"])
    assert V1_RUN_KEYS <= set(data) and all(V1_STEP_KEYS <= set(s) for s in data["steps"])
    assert set(data) - V1_RUN_KEYS == {"trace_version", "run_id", "scenario_id", "run_id_source"}
    assert set(data["steps"][0]) - V1_STEP_KEYS == {"run_id", "scenario_id", "retry", "execution"}


# --- Follow-up: retry and idempotency fields ----------------------------------------------------

def retry_of(step):
    return step.retry.to_dict()


def test_retries_of_the_same_idempotent_intent_are_numbered_and_carry_the_key(agent_sandbox,
                                                                              replay, call):
    run = run_agent(replay([call("create_ticket", **TICKET), call("create_ticket", **TICKET),
                            call("create_ticket", **{**TICKET, "request_id": "r-2"})]),
                    agent_sandbox(), "x")
    first, retry, new_key = (retry_of(s) for s in run.steps[:3])
    assert (first["attempt"], first["is_retry"], first["idempotency_key"]) == (1, False, "r-1")
    assert (retry["attempt"], retry["is_retry"], retry["idempotency_key"]) == (2, True, "r-1")
    assert retry["intent_ref"] == first["intent_ref"]
    assert (new_key["attempt"], new_key["is_retry"], new_key["idempotency_key"]) == (1, False, "r-2")
    assert new_key["intent_ref"] != first["intent_ref"]


def test_other_calls_count_attempts_by_tool_and_arguments_without_recording_them(
        agent_sandbox, replay, call):
    text = "my password is hunter2"
    run = run_agent(replay([call("add_comment", ticket_id="T-100", text=text),
                            call("add_comment", ticket_id="T-100", text=text),
                            call("add_comment", ticket_id="T-100", text="different")]),
                    agent_sandbox(), "x")
    retries = [retry_of(s) for s in run.steps[:3]]
    assert [r["attempt"] for r in retries] == [1, 2, 1]
    assert all(r["idempotency_key"] is None for r in retries)
    assert "hunter2" not in json.dumps(retries)


def test_a_malformed_idempotency_key_is_never_recorded(agent_sandbox, replay, call):
    run = run_agent(replay([call("create_ticket", **{**TICKET, "request_id": "has spaces!"})]),
                    agent_sandbox(), "x")
    assert run.steps[0].stopped_at is Stage.ARGUMENTS
    assert retry_of(run.steps[0])["idempotency_key"] is None


def execution_of(step):
    return step.execution.to_dict() if step.execution else None


def test_a_new_write_and_its_replay_are_distinguished(agent_sandbox, replay, call):
    run = run_agent(replay([call("create_ticket", **TICKET), call("create_ticket", **TICKET)]),
                    agent_sandbox(), "x")
    assert execution_of(run.steps[0]) == {"status": "ok", "fault": None, "executed": True,
                                          "effect_occurred": True, "replayed": False,
                                          "timeout": None}
    assert execution_of(run.steps[1]) == {"status": "ok", "fault": None, "executed": True,
                                          "effect_occurred": False, "replayed": True,
                                          "timeout": None}
    assert [e["kind"] for e in run.final_state["effects"]] == ["ticket_created"]


def test_a_timeout_before_the_effect_and_a_successful_retry(agent_sandbox, replay, call):
    faults = FaultPlan(per_call={"create_ticket": [Fault.TIMEOUT_NO_EFFECT]})
    run = run_agent(replay([call("create_ticket", **TICKET), call("create_ticket", **TICKET)]),
                    agent_sandbox(), "x", faults=faults)
    assert execution_of(run.steps[0]) == {"status": "timeout", "fault": "timeout_no_effect",
                                          "executed": False, "effect_occurred": False,
                                          "replayed": False, "timeout": "before_effect"}
    assert run.steps[0].observation["outcome"] == "timeout"  # never reported as success
    assert retry_of(run.steps[1])["is_retry"] is True
    assert execution_of(run.steps[1])["effect_occurred"] is True  # the retry did the work
    assert execution_of(run.steps[1])["replayed"] is False


def test_a_timeout_after_the_effect_then_a_replayed_retry_gives_one_ticket(agent_sandbox,
                                                                           replay, call):
    faults = FaultPlan(per_call={"create_ticket": [Fault.TIMEOUT_AFTER_EFFECT]})
    run = run_agent(replay([call("create_ticket", **TICKET), call("create_ticket", **TICKET)]),
                    agent_sandbox(), "x", faults=faults)
    assert execution_of(run.steps[0]) == {"status": "timeout", "fault": "timeout_after_effect",
                                          "executed": True, "effect_occurred": True,
                                          "replayed": False, "timeout": "after_effect"}
    assert run.steps[0].observation["outcome"] == "timeout"
    assert execution_of(run.steps[1])["replayed"] is True
    assert execution_of(run.steps[1])["effect_occurred"] is False
    assert [e["kind"] for e in run.final_state["effects"]] == ["ticket_created"]


@pytest.mark.parametrize("fault", [Fault.TOOL_ERROR, Fault.MALFORMED_RESULT])
def test_a_failed_execution_records_no_effect_and_no_success(agent_sandbox, replay, call, fault):
    run = run_agent(replay([call("create_ticket", **TICKET)]), agent_sandbox(), "x",
                    faults=FaultPlan(always={"create_ticket": fault}))
    info = execution_of(run.steps[0])
    assert (info["status"], info["executed"], info["effect_occurred"], info["replayed"]) == (
        fault.value, False, False, False)
    assert run.steps[0].observation["outcome"] == fault.value


def test_rejected_and_final_steps_have_no_execution_record(agent_sandbox, replay, call):
    run = run_agent(replay([call("get_user", user_id="U-101"), "garbage",
                            {"type": "final", "status": "refused", "message": "No."}]),
                    agent_sandbox(), "x")
    denied, invalid, final = run.steps
    assert execution_of(denied) is None and denied.retry is not None
    assert execution_of(invalid) is None and invalid.retry is None  # never parsed
    assert execution_of(final) is None and final.retry is None


# --- Follow-up: programming errors are not rejections -------------------------------------------

def test_a_programming_error_in_a_stage_propagates_out_of_the_run(agent_sandbox, replay, call,
                                                                  monkeypatch):
    sandbox = agent_sandbox()

    def broken(tool, arguments):
        raise RuntimeError("bug in stage 4")

    monkeypatch.setattr(sandbox, "arguments_stage", broken)
    with pytest.raises(RuntimeError, match="bug in stage 4"):
        run_agent(replay([call("get_user", user_id="U-100")]), sandbox, "x")


# --- No production way to disable authorization (Phase 11.2, step 1) ----------------------------

SRC = Path(__file__).resolve().parents[1] / "src" / "ai_quality_lab"
BYPASS_WORDS = ("enforce", "bypass", "no_policy", "no-policy", "disable_auth", "skip_auth",
                "allow_all", "unsafe")


def test_run_agent_has_no_parameter_that_could_disable_authorization():
    import inspect

    params = inspect.signature(run_agent).parameters
    assert set(params) == {"planner", "sandbox", "request", "faults", "step_limit",
                           "max_consecutive_invalid", "clock", "run_id", "scenario_id"}


def test_no_production_code_or_cli_offers_a_policy_switch():
    """Scans every module in src/: no function parameter or CLI option named like a policy switch,
    and the removed v1/v2 field name appears only in the trace-format module (as a legacy key)."""
    offenders = []
    for path in sorted(SRC.rglob("*.py")):
        text = path.read_text(encoding="utf-8")
        if "policy_enforcement" in text and path.name != "trace_format.py":
            offenders.append(f"{path.name}: mentions policy_enforcement")
        for node in ast.walk(ast.parse(text)):
            names = []
            if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                args = node.args
                names = [a.arg for a in args.args + args.kwonlyargs + args.posonlyargs]
            elif (isinstance(node, ast.Call) and getattr(node.func, "attr", "") == "add_argument"):
                names = [a.value for a in node.args if isinstance(a, ast.Constant)
                         and isinstance(a.value, str)]
            offenders += [f"{path.name}: {n}" for n in names
                          if any(w in n.lower() for w in BYPASS_WORDS)
                          and (path.parent.name in ("agent", "agent_eval") or "polic" in n.lower()
                               or "auth" in n.lower())]
    assert offenders == []


def test_the_broken_authorization_substitute_exists_only_in_tests():
    assert all("broken_authorization" not in p.read_text(encoding="utf-8")
               for p in SRC.rglob("*.py"))
    conftest = Path(__file__).resolve().parent / "conftest.py"
    assert "def broken_authorization(monkeypatch)" in conftest.read_text(encoding="utf-8")


def test_every_executed_step_passed_authorization(agent_sandbox, replay, call):
    """Normal behaviour: across allowed, denied, rejected and faulted actions, nothing executes
    without an "authorize: ok" outcome."""
    sandbox = agent_sandbox(verification_script={"U-100": True},
                            approval_script=[ApprovalResponse("approved", "U-200")])
    run = run_agent(replay([
        call("get_user", user_id="U-101"), call("reset_password", user_id="U-100", approval_id="APR-1"),
        call("verify_identity", user_id="U-100", method="callback"),
        call("request_approval", tool="reset_password", arguments={"user_id": "U-100"},
             justification="x"),
        call("reset_password", user_id="U-100", approval_id="APR-1"),
        call("reset_password", user_id="U-100", approval_id="APR-1"),
        call("grant_access", user_id="U-100", resource="domain-admin", approval_id="APR-1"),
        call("create_ticket", **TICKET)]), sandbox, "x", step_limit=10)
    for step in run.steps:
        if step.executed:
            assert ("authorize", "ok") in stages(step), step.index
    assert [s.index for s in run.steps if s.denied] == [0, 1, 5, 6]
    assert run.final_state["password_resets"] == ["U-100"] and run.final_state["grants"] == []
