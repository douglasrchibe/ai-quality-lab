"""Tests for GuardedLLMChatbot with a fake chat client (no model calls)."""

import pytest

from ai_quality_lab.guardrails import InvalidGuardrailConfigError
from ai_quality_lab.llm_agent import GuardedLLMChatbot, build_system_prompt, main
from ai_quality_lab.llm_client import LLMUnavailableError
from ai_quality_lab.mock_agent import (
    INVALID_INPUT_RESPONSE,
    ChatResponse,
    ResponseOutcome,
    SimulatedFailure,
)

ON = {"guardrails": {"cooking": {"enabled": True}}}
OFF = {"guardrails": {"cooking": {"enabled": False}}}


class FakeClient:
    label = "fake:model"

    def __init__(self, reply="A model answer.", error=None):
        self.calls = []
        self._reply = reply
        self._error = error

    def complete(self, messages):
        self.calls.append(messages)
        if self._error:
            raise self._error
        return self._reply


@pytest.fixture
def client():
    return FakeClient()


def test_blocked_topic_is_refused_without_calling_the_model(client):
    reply = GuardedLLMChatbot(ON, client).respond("Give me a pasta recipe.")
    assert isinstance(reply, ChatResponse)
    assert reply.blocked is True
    assert reply.response == "Sorry, I can't help with cooking requests."
    assert reply.outcome is ResponseOutcome.BLOCKED
    assert client.calls == []


def test_allowed_message_is_answered_by_the_model(client):
    reply = GuardedLLMChatbot(ON, client).respond("What is the capital of France?")
    assert (reply.response, reply.blocked, reply.outcome) == (
        "A model answer.", False, ResponseOutcome.ANSWERED
    )
    assert reply.reason == "Answered by fake:model."
    (messages,) = client.calls
    assert [m["role"] for m in messages] == ["system", "user"]
    assert messages[1]["content"] == "What is the capital of France?"


def test_system_prompt_restates_enabled_restrictions_only(client):
    GuardedLLMChatbot(ON, client).respond("Hello")
    GuardedLLMChatbot(OFF, client).respond("Hello")
    restricted, unrestricted = (calls[0]["content"] for calls in client.calls)
    assert "restricted topics: cooking" in restricted
    assert "restricted topics" not in unrestricted


def test_system_prompt_formats_topic_names():
    assert "medical advice" in build_system_prompt(["medical_advice"])


def test_cooking_allowed_when_guardrail_disabled_goes_to_model(client):
    reply = GuardedLLMChatbot(OFF, client).respond("Give me a pasta recipe.")
    assert reply.blocked is False
    assert reply.detected_topics == ("cooking",)
    assert len(client.calls) == 1


def test_context_is_given_to_the_model_as_reference_text(client):
    GuardedLLMChatbot(ON, client).respond("When?", context=["Fact one.", "Fact two."])
    user = client.calls[0][1]["content"]
    assert user == "Context:\n- Fact one.\n- Fact two.\n\nQuestion: When?"


@pytest.mark.parametrize("message", ["", "   "])
def test_empty_input_never_reaches_the_model(client, message):
    reply = GuardedLLMChatbot(ON, client).respond(message)
    assert reply.response == INVALID_INPUT_RESPONSE
    assert client.calls == []


def test_non_string_input_raises(client):
    with pytest.raises(TypeError):
        GuardedLLMChatbot(ON, client).respond(None)


def test_invalid_configuration_rejected(client):
    with pytest.raises(InvalidGuardrailConfigError):
        GuardedLLMChatbot({"guardrails": {"cooking": {"enabled": "yes"}}}, client)


def test_fail_open_simulation_sends_restricted_request_to_model(client):
    bot = GuardedLLMChatbot(ON, client, simulated_failure=SimulatedFailure.GUARDRAIL_FAIL_OPEN)
    reply = bot.respond("Give me a pasta recipe.")
    assert reply.blocked is False
    assert len(client.calls) == 1
    # The model layer is still told about the restriction: defense in depth.
    assert "restricted topics: cooking" in client.calls[0][0]["content"]


def test_model_errors_propagate_for_the_runner_to_report():
    bot = GuardedLLMChatbot(ON, FakeClient(error=LLMUnavailableError("down")))
    with pytest.raises(LLMUnavailableError):
        bot.respond("What is 2 + 2?")


def test_cli_reports_unreachable_model(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("AIQL_LLM_PROVIDER", "ollama")
    monkeypatch.setenv("AIQL_LLM_BASE_URL", "http://127.0.0.1:9")  # nothing listens here
    monkeypatch.setenv("AIQL_LLM_TIMEOUT", "3")
    monkeypatch.chdir(tmp_path)  # no .env file in this directory
    assert main(["What is 2 + 2?"]) == 2
    assert "Is Ollama installed and running" in capsys.readouterr().err
