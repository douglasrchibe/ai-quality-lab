"""The bounded tool contract (AC1) and stages 1–3 (parse, tool selection, schema), tested through
the public stage functions, which return a ``StageOutcome``."""

import dataclasses
import json

import pytest

from ai_quality_lab.agent import policy, tools
from ai_quality_lab.agent.policy import RULES
from ai_quality_lab.agent.tools import (
    REGISTRY,
    FinalAction,
    Stage,
    StageOutcome,
    ToolCall,
    ToolClass,
    contract_fingerprint,
    parse_stage,
    schema_stage,
    select_stage,
)


def rejected(outcome, stage, code):
    """A rejected outcome: the stage, not ok, the classification, a detail, and no value."""
    assert isinstance(outcome, StageOutcome)
    assert (outcome.stage, outcome.ok, outcome.code, outcome.value) == (stage, False, code, None)
    assert outcome.detail
    return outcome.detail

EXPECTED_TOOLS = {
    "search_kb": "read", "get_user": "read", "get_ticket": "read", "verify_identity": "read",
    "create_ticket": "write", "add_comment": "write", "request_approval": "write",
    "escalate_to_human": "write", "reset_password": "consequential",
    "grant_access": "consequential",
}


def test_registry_is_exactly_the_ten_planned_tools_with_their_classes():
    assert {name: spec.tool_class.value for name, spec in REGISTRY.items()} == EXPECTED_TOOLS
    assert tools.CONSEQUENTIAL_TOOLS == ("reset_password", "grant_access")


def test_every_consequential_tool_requires_an_approval():
    for spec in tools.TOOLS:
        needs_approval = "R-APPROVAL" in spec.rules
        assert needs_approval == (spec.tool_class is ToolClass.CONSEQUENTIAL), spec.name
        assert (spec.param("approval_id") is not None) == needs_approval, spec.name


def test_every_rule_used_by_a_tool_is_defined_and_has_exactly_one_check():
    used = {rule for spec in tools.TOOLS for rule in spec.rules}
    assert used == set(RULES)
    assert set(policy._RULE_CHECKS) == set(RULES)


def test_contract_fingerprint_is_stable_and_tracks_tools_and_rule_text(monkeypatch):
    first = contract_fingerprint(RULES)
    assert first == contract_fingerprint(dict(RULES)) and len(first) == 16
    assert contract_fingerprint({**RULES, "R-SELF": "a changed rule"}) != first
    changed = tuple(dataclasses.replace(t, rules=t.rules[:-1]) if t.name == "grant_access" else t
                    for t in tools.TOOLS)
    monkeypatch.setattr(tools, "TOOLS", changed)
    assert contract_fingerprint(RULES) != first


def test_contract_fingerprint_refuses_an_undefined_rule():
    with pytest.raises(ValueError, match="undefined rules"):
        contract_fingerprint({k: v for k, v in RULES.items() if k != "R-SELF"})


# --- Stage 1: parse ------------------------------------------------------------------------------

def test_parse_accepts_the_two_action_types():
    call = parse_stage(json.dumps({"type": "tool_call", "tool": "get_user",
                                   "arguments": {"user_id": "U-100"}}))
    assert (call.stage, call.ok, call.code) == (Stage.PARSE, True, "ok")
    assert call.value == ToolCall("get_user", {"user_id": "U-100"})
    final = parse_stage('{"type": "final", "status": "escalated", "message": "Sent to IT."}')
    assert final.ok and final.value == FinalAction("escalated", "Sent to IT.")
    assert final.to_dict() == {"stage": "parse", "ok": True, "code": "ok", "detail": ""}


@pytest.mark.parametrize("raw", [
    "reset my password please",  # prose, not JSON
    "[1, 2]",  # JSON, not an object
    '{"type": "think", "text": "hmm"}',  # unknown action type
    '{"type": "tool_call", "tool": "get_user"}',  # missing arguments
    '{"type": "tool_call", "tool": "get_user", "arguments": {}, "reason": "x"}',  # extra key
    '{"type": "tool_call", "tool": 7, "arguments": {}}',
    '{"type": "tool_call", "tool": "get_user", "arguments": "U-100"}',
    '{"type": "final", "status": "done", "message": "ok"}',  # status outside the enum
    '{"type": "final", "status": "resolved", "message": 3}',
    "",
    "[" * 100_000,  # nested too deeply for the JSON parser: still a rejection, not a crash
])
def test_parse_rejects_anything_outside_the_action_format(raw):
    rejected(parse_stage(raw), Stage.PARSE, "parse_error")


# --- Stage 2: tool selection ---------------------------------------------------------------------

def test_select_returns_the_spec_or_rejects_a_tool_not_in_the_registry():
    selected = select_stage(ToolCall("get_user", {}))
    assert (selected.stage, selected.ok, selected.value) == (Stage.SELECT, True, REGISTRY["get_user"])
    detail = rejected(select_stage(ToolCall("delete_user", {"user_id": "U-100"})), Stage.SELECT,
                      "unknown_tool")
    assert "not in the tool registry" in detail and "delete_user" not in detail  # planner text


# --- Stage 3: schema -----------------------------------------------------------------------------

@pytest.mark.parametrize("tool, arguments, fragment", [
    ("get_user", {}, "missing 'user_id'"),
    ("get_user", {"user_id": "U-100", "admin": "yes"}, "unknown parameter"),
    ("get_user", {"user_id": 100}, "non-empty string"),
    ("get_user", {"user_id": "   "}, "non-empty string"),
    ("add_comment", {"ticket_id": "T-100", "text": "x" * 501}, "at most 500"),
    ("create_ticket", {"request_id": "r-1", "user_id": "U-100", "summary": "VPN down",
                       "priority": "urgent!!!"}, "must be one of"),
    ("verify_identity", {"user_id": "U-100", "method": "manager_confirmation"}, "must be one of"),
    ("request_approval", {"tool": "reset_password", "arguments": "U-100",
                          "justification": "x"}, "must be an object"),
])
def test_schema_rejects_missing_extra_mistyped_and_out_of_enum_parameters(tool, arguments,
                                                                         fragment):
    detail = rejected(schema_stage(REGISTRY[tool], arguments), Stage.SCHEMA, "schema_error")
    assert fragment in detail


def test_schema_accepts_valid_arguments_and_an_absent_optional_parameter():
    for tool, arguments in [
        ("escalate_to_human", {"reason": "Out of scope"}),
        ("escalate_to_human", {"reason": "Out of scope", "ticket_id": "T-100"}),
        ("create_ticket", {"request_id": "r-1", "user_id": "U-100", "summary": "VPN down",
                           "priority": "P1"}),
    ]:
        outcome = schema_stage(REGISTRY[tool], arguments)
        assert (outcome.stage, outcome.ok, outcome.code) == (Stage.SCHEMA, True, "ok")


def test_rejection_details_never_echo_planner_text():
    secret = "hunter2-do-not-log"  # identifier-shaped, so a format check alone wouldn't catch it
    outcomes = [
        schema_stage(REGISTRY["get_user"], {"user_id": "U-100", secret: "x"}),
        schema_stage(REGISTRY["get_user"], {"user_id": secret * 40}),
        select_stage(ToolCall(secret, {})),
        parse_stage(json.dumps({"type": secret})),
    ]
    for outcome in outcomes:
        assert not outcome.ok and "hunter2" not in outcome.detail, outcome
    assert "allowed: ['user_id']" in outcomes[0].detail  # still useful


@pytest.mark.parametrize("value, known, shown", [
    ("U-100", (), "'U-100'"), ("T-1001", (), "'T-1001'"), ("APR-3", (), "'APR-3'"),
    ("domain-admin", ("domain-admin",), "'domain-admin'"),
    ("domain-admin", (), "<redacted, 12 chars>"), ("U-100 and my password", (), "<redacted, 21 chars>"),
    (42, (), "<redacted int>"),
])
def test_safe_repr_shows_only_sandbox_ids_and_known_names(value, known, shown):
    assert tools.safe_repr(value, known) == shown


def test_a_programming_error_inside_a_stage_propagates_instead_of_becoming_a_rejection(
        monkeypatch):
    def broken(*args, **kwargs):
        raise RuntimeError("bug")

    monkeypatch.setattr(tools, "_validate_schema", broken)
    with pytest.raises(RuntimeError, match="bug"):
        schema_stage(REGISTRY["get_user"], {"user_id": "U-100"})
    monkeypatch.setattr(tools, "_parse_action", broken)
    with pytest.raises(RuntimeError, match="bug"):
        parse_stage("{}")
