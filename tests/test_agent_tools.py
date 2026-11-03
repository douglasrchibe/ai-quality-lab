"""The bounded tool contract (AC1) and stages 1–3: parse, tool selection, schema."""

import dataclasses
import json

import pytest

from ai_quality_lab.agent import policy, tools
from ai_quality_lab.agent.policy import RULES
from ai_quality_lab.agent.tools import (
    REGISTRY,
    FinalAction,
    StageError,
    ToolCall,
    ToolClass,
    contract_fingerprint,
    parse_action,
    select_tool,
    validate_schema,
)

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
    call = parse_action(json.dumps({"type": "tool_call", "tool": "get_user",
                                    "arguments": {"user_id": "U-100"}}))
    assert call == ToolCall("get_user", {"user_id": "U-100"})
    final = parse_action('{"type": "final", "status": "escalated", "message": "Sent to IT."}')
    assert final == FinalAction("escalated", "Sent to IT.")


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
])
def test_parse_rejects_anything_outside_the_action_format(raw):
    with pytest.raises(StageError) as err:
        parse_action(raw)
    assert err.value.code == "parse_error"


# --- Stage 2: tool selection ---------------------------------------------------------------------

def test_select_rejects_a_tool_not_in_the_registry():
    assert select_tool(ToolCall("get_user", {})).name == "get_user"
    with pytest.raises(StageError) as err:
        select_tool(ToolCall("delete_user", {"user_id": "U-100"}))
    assert err.value.code == "unknown_tool"


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
    with pytest.raises(StageError) as err:
        validate_schema(REGISTRY[tool], arguments)
    assert err.value.code == "schema_error" and fragment in err.value.detail


def test_schema_accepts_valid_arguments_and_an_absent_optional_parameter():
    validate_schema(REGISTRY["escalate_to_human"], {"reason": "Out of scope"})
    validate_schema(REGISTRY["escalate_to_human"], {"reason": "Out of scope", "ticket_id": "T-100"})
    validate_schema(REGISTRY["create_ticket"], {"request_id": "r-1", "user_id": "U-100",
                                                "summary": "VPN down", "priority": "P1"})
