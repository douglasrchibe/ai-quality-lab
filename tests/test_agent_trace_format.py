"""Trace format versions 1–3 (AC: compatibility). The fixtures are real traces of one scenario,
produced by the merged code of each version: v1 by commit 460767b (Milestone 11.1), v2 by 272a86b
(the trace refinement), v3 by this branch. The scenario has an allowed read, a denied read, a
create_ticket that times out after its effect, a replayed retry, a parse error and a final answer."""

import copy
import json
from pathlib import Path

import pytest

from ai_quality_lab.agent.trace_format import (
    TRACE_VERSION,
    TraceFormatError,
    trace_version,
    upgrade_trace,
)

FIXTURES = Path(__file__).resolve().parent / "fixtures" / "agent_traces"


def load(version):
    return json.loads((FIXTURES / f"trace_v{version}.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("version", [1, 2, 3])
def test_each_fixture_is_detected_as_its_version(version):
    assert trace_version(load(version)) == version


def test_a_v1_trace_upgrades_with_its_missing_evidence_named():
    original = load(1)
    upgraded = upgrade_trace(original)
    assert upgraded["trace_version"] == TRACE_VERSION == 3
    assert upgraded["source_trace_version"] == 1
    assert (upgraded["run_id"], upgraded["scenario_id"], upgraded["run_id_source"]) == (None,) * 3
    assert all(s["retry"] is None and s["execution"] is None for s in upgraded["steps"])
    assert upgraded["missing_evidence"] == [
        "run_id", "scenario_id", "run_id_source", "steps[].run_id", "steps[].scenario_id",
        "steps[].retry", "steps[].execution"]
    assert upgraded["legacy"] == {"policy_enforcement": True, "unauthorized_execution_steps": []}
    assert "policy_enforcement" not in upgraded
    assert all("unauthorized_execution" not in s for s in upgraded["steps"])
    assert original == load(1)  # the input is never modified


def test_a_v2_trace_upgrades_with_no_missing_evidence():
    upgraded = upgrade_trace(load(2))
    assert upgraded["source_trace_version"] == 2 and upgraded["missing_evidence"] == []
    assert (upgraded["run_id"], upgraded["scenario_id"]) == ("fixture-run-001", "FIXTURE-1")
    assert upgraded["steps"][3]["execution"]["replayed"] is True
    assert upgraded["steps"][2]["execution"]["timeout"] == "after_effect"
    assert upgraded["legacy"] == {"policy_enforcement": True, "unauthorized_execution_steps": []}


def test_a_v3_trace_passes_through_unchanged_apart_from_the_upgrade_markers():
    original = load(3)
    upgraded = upgrade_trace(original)
    markers = {"source_trace_version": 3, "missing_evidence": [], "legacy": {}}
    assert {k: upgraded[k] for k in markers} == markers
    assert {k: v for k, v in upgraded.items() if k not in markers} == original


def test_legacy_policy_off_markers_are_preserved_not_dropped():
    v2 = load(2)
    v2["policy_enforcement"] = False
    v2["steps"][1]["unauthorized_execution"] = True
    assert upgrade_trace(v2)["legacy"] == {"policy_enforcement": False,
                                           "unauthorized_execution_steps": [1]}


def core(trace):
    """What the agent did and what happened: identical across versions for the same scenario."""
    return {
        "status": trace["status"], "final_message": trace["final_message"],
        "contract_fingerprint": trace["contract_fingerprint"],
        "steps": [(s["action"], [(x["stage"], x["ok"], x["code"]) for x in s["stages"]],
                   s["decision"], s["executed"], s["effects"], s["repeated"]) for s in trace["steps"]],
        "effects": trace["final_state"]["effects"],
        "tickets": trace["final_state"]["tickets"],
        "resets": trace["final_state"]["password_resets"],
        "grants": trace["final_state"]["grants"],
    }


def test_the_same_scenario_behaves_identically_in_versions_1_2_and_3():
    v1, v2, v3 = (upgrade_trace(load(v)) for v in (1, 2, 3))
    assert core(v1) == core(v2) == core(v3)
    denied = v3["steps"][1]
    assert (denied["decision"]["rule_id"], denied["decision"]["reason"]) == ("R-READ-SCOPE",
                                                                             "out_of_scope")


FIXTURE_TICKET = {"request_id": "r-1", "user_id": "U-100", "summary": "VPN down", "priority": "P1"}
FIXTURE_ACTIONS = [
    {"type": "tool_call", "tool": "get_user", "arguments": {"user_id": "U-100"}},
    {"type": "tool_call", "tool": "get_user", "arguments": {"user_id": "U-101"}},
    {"type": "tool_call", "tool": "create_ticket", "arguments": FIXTURE_TICKET},
    {"type": "tool_call", "tool": "create_ticket", "arguments": FIXTURE_TICKET},
    "not json",
    {"type": "final", "status": "resolved", "message": "Ticket T-1001 created."},
]


def test_the_v3_fixture_is_exactly_what_the_runtime_produces_now(replay):
    """Guards against silent drift: regenerate the fixture only with a stated reason."""
    from ai_quality_lab.agent.faults import Fault, FaultPlan
    from ai_quality_lab.agent.runtime import run_agent
    from ai_quality_lab.agent.sandbox import ApprovalResponse, Role, Sandbox, Ticket, User

    sandbox = Sandbox(
        requester_id="U-100",
        users=[User("U-100", "Alex", Role.EMPLOYEE, "U-200"),
               User("U-101", "Blair", Role.EMPLOYEE, "U-200"),
               User("U-200", "Morgan", Role.MANAGER)],
        tickets=[Ticket("T-100", "U-100", "Laptop is slow")],
        approval_script=[ApprovalResponse("approved", "U-200")])
    run = run_agent(replay(FIXTURE_ACTIONS), sandbox, "VPN is down",
                    faults=FaultPlan(per_call={"create_ticket": [Fault.TIMEOUT_AFTER_EFFECT]}),
                    run_id="fixture-run-001", scenario_id="FIXTURE-1")
    assert json.loads(json.dumps(run.to_dict())) == load(3)


@pytest.mark.parametrize("data, fragment", [
    ({"trace_version": 4, "steps": []}, "unsupported trace_version"),
    ({"trace_version": "3", "steps": []}, "unsupported trace_version"),
    ({"status": "resolved"}, "not an agent trace"),
    ([], "not an agent trace"),
])
def test_unknown_or_malformed_traces_are_refused(data, fragment):
    with pytest.raises(TraceFormatError, match=fragment):
        upgrade_trace(data)


def test_upgraded_traces_are_json_ready():
    for version in (1, 2, 3):
        upgraded = upgrade_trace(copy.deepcopy(load(version)))
        assert json.loads(json.dumps(upgraded)) == upgraded
