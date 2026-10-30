"""Unit tests for the mock chatbot and its guardrail/topic-detection components."""

import dataclasses

import pytest

from ai_quality_lab.guardrails import (
    GuardrailConfig,
    InvalidGuardrailConfigError,
    KeywordTopicDetector,
)
from ai_quality_lab.mock_agent import (
    FALLBACK_RESPONSE,
    INVALID_INPUT_RESPONSE,
    KNOWN_ANSWERS,
    SAMPLE_PASTA_RECIPE,
    ChatResponse,
    MockChatbot,
    ResponseOutcome,
    SimulatedFailure,
)

PASTA_QUESTION = "Give me a pasta recipe."
COOKING_QUESTIONS = [
    PASTA_QUESTION,
    "How to cook rice?",
    "What ingredients do I need for pancakes?",
    "Any tips for baking bread?",
    "Help me with meal preparation for the week.",
    "I love cooking on weekends.",
]
UNRELATED_QUESTIONS = [
    "What is the capital of France?",
    "What is 2 + 2?",
    "Explain how TCP handshakes work.",
    "Who won the 1998 World Cup?",
    "I ate a cookie today.",  # "cookie" must not match the "cook" keyword
]


def config(cooking_enabled: bool) -> dict:
    return {"guardrails": {"cooking": {"enabled": cooking_enabled}}}


@pytest.fixture
def blocking_bot() -> MockChatbot:
    return MockChatbot(config(cooking_enabled=True))


@pytest.fixture
def allowing_bot() -> MockChatbot:
    return MockChatbot(config(cooking_enabled=False))


@pytest.fixture
def fail_open_bot() -> MockChatbot:
    return MockChatbot(
        config(cooking_enabled=True), simulated_failure=SimulatedFailure.GUARDRAIL_FAIL_OPEN
    )


@pytest.fixture
def detector() -> KeywordTopicDetector:
    return KeywordTopicDetector()


# --- Guardrail behavior -------------------------------------------------------------------------


@pytest.mark.parametrize("question", COOKING_QUESTIONS)
def test_cooking_blocked_when_guardrail_enabled(blocking_bot, question):
    result = blocking_bot.respond(question)
    assert result.blocked is True
    assert result.outcome is ResponseOutcome.BLOCKED
    assert "cooking" in result.detected_topics
    assert "cooking" in result.reason


@pytest.mark.parametrize("question", COOKING_QUESTIONS)
def test_cooking_allowed_when_guardrail_disabled(allowing_bot, question):
    result = allowing_bot.respond(question)
    assert result.blocked is False
    assert result.outcome is ResponseOutcome.ANSWERED
    assert "cooking" in result.detected_topics


@pytest.mark.parametrize("cooking_enabled", [True, False])
@pytest.mark.parametrize("question", UNRELATED_QUESTIONS)
def test_unrelated_questions_not_blocked_regardless_of_setting(question, cooking_enabled):
    result = MockChatbot(config(cooking_enabled)).respond(question)
    assert result.blocked is False
    assert result.detected_topics == ()


def test_fail_open_mode_incorrectly_allows_blocked_topic(fail_open_bot):
    result = fail_open_bot.respond(PASTA_QUESTION)
    # The guardrail should have blocked this, but the simulated failure lets it through.
    assert result.blocked is False
    assert result.response == SAMPLE_PASTA_RECIPE
    assert "cooking" in result.detected_topics


def test_fail_open_mode_is_indistinguishable_from_a_legitimate_allow(fail_open_bot, allowing_bot):
    # The failure is only detectable by comparing behavior to the configured expectation.
    assert fail_open_bot.respond(PASTA_QUESTION) == allowing_bot.respond(PASTA_QUESTION)


def test_fail_open_mode_violates_an_expected_refusal(fail_open_bot):
    # Demonstrates that a refusal assertion catches the simulated regression.
    with pytest.raises(AssertionError):
        assert fail_open_bot.respond(PASTA_QUESTION).blocked is True


def test_failure_mode_disabled_by_default(blocking_bot):
    assert blocking_bot.simulated_failure is SimulatedFailure.NONE
    assert blocking_bot.respond(PASTA_QUESTION).blocked is True


def test_unknown_failure_mode_rejected():
    with pytest.raises(ValueError):
        MockChatbot(config(True), simulated_failure="random_failures")


# --- Topic detection ----------------------------------------------------------------------------


@pytest.mark.parametrize(
    "text",
    [
        "cooking",
        "recipe",
        "pasta",
        "baking",
        "ingredients",
        "how to cook",
        "meal preparation",
        "Can you share a recipe?",
    ],
)
def test_cooking_keywords_detected(detector, text):
    assert detector.detect(text) == ("cooking",)


@pytest.mark.parametrize("text", ["PASTA", "Recipe", "How To Cook", "BaKiNg"])
def test_detection_is_case_insensitive(detector, text):
    assert detector.detect(text) == ("cooking",)


@pytest.mark.parametrize("text", ["  pasta  ", "\tbaking\n", "meal    preparation"])
def test_detection_ignores_surrounding_and_repeated_whitespace(detector, text):
    assert detector.detect(text) == ("cooking",)


@pytest.mark.parametrize("text", UNRELATED_QUESTIONS)
def test_unrelated_text_has_no_cooking_detection(detector, text):
    assert "cooking" not in detector.detect(text)


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_detector_handles_empty_input(detector, text):
    assert detector.detect(text) == ()


def test_detection_is_deterministic(detector):
    assert {detector.detect(PASTA_QUESTION) for _ in range(20)} == {("cooking",)}


@pytest.mark.parametrize("text", ["", "   ", "\n\t"])
def test_chatbot_returns_validation_response_for_empty_input(blocking_bot, text):
    result = blocking_bot.respond(text)
    assert result.outcome is ResponseOutcome.INVALID_INPUT
    assert result.response == INVALID_INPUT_RESPONSE
    assert result.blocked is False
    assert result.detected_topics == ()
    assert result.message == text


@pytest.mark.parametrize("bad_input", [None, 42, ["pasta"], b"pasta"])
def test_chatbot_rejects_non_string_input(blocking_bot, bad_input):
    with pytest.raises(TypeError):
        blocking_bot.respond(bad_input)


# --- Response behavior --------------------------------------------------------------------------


def test_blocked_response_is_clear_refusal(blocking_bot):
    result = blocking_bot.respond(PASTA_QUESTION)
    assert result.response == "Sorry, I can't help with cooking requests."
    assert SAMPLE_PASTA_RECIPE not in result.response


def test_allowed_cooking_question_returns_sample_recipe(allowing_bot):
    result = allowing_bot.respond(PASTA_QUESTION)
    assert result.response == SAMPLE_PASTA_RECIPE


@pytest.mark.parametrize("question, expected", sorted(KNOWN_ANSWERS.items()))
def test_known_questions_return_fixed_answers(allowing_bot, question, expected):
    result = allowing_bot.respond(question.upper() + "?")
    assert result.response == expected
    assert result.outcome is ResponseOutcome.ANSWERED


def test_capital_of_france_scenario(blocking_bot):
    result = blocking_bot.respond("What is the capital of France?")
    assert result.response == "The capital of France is Paris."
    assert result.blocked is False


def test_unknown_question_gets_fallback(blocking_bot):
    result = blocking_bot.respond("What is the airspeed of an unladen swallow?")
    assert result.response == FALLBACK_RESPONSE
    assert result.outcome is ResponseOutcome.FALLBACK
    assert result.blocked is False


def test_response_contains_all_required_fields(blocking_bot):
    result = blocking_bot.respond(PASTA_QUESTION)
    assert isinstance(result, ChatResponse)
    field_names = {f.name for f in dataclasses.fields(result)}
    assert {"message", "response", "blocked", "detected_topics", "reason"} <= field_names
    assert result.message == PASTA_QUESTION
    assert isinstance(result.detected_topics, tuple)
    assert result.reason


def test_response_is_immutable(blocking_bot):
    result = blocking_bot.respond(PASTA_QUESTION)
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.blocked = False


def test_same_input_produces_identical_response(blocking_bot):
    assert blocking_bot.respond(PASTA_QUESTION) == blocking_bot.respond(PASTA_QUESTION)


# --- Configuration validation -------------------------------------------------------------------


@pytest.mark.parametrize("enabled", [True, False])
def test_valid_configuration_initializes(enabled):
    bot = MockChatbot(config(enabled))
    assert bot.config.topics == ("cooking",)
    assert bot.config.is_blocked("cooking") is enabled


@pytest.mark.parametrize("raw", [None, {}, {"guardrails": {}}])
def test_missing_configuration_rejected(raw):
    with pytest.raises(InvalidGuardrailConfigError):
        MockChatbot(raw)


@pytest.mark.parametrize("enabled", ["yes", "true", 1, 0, None, 100])
def test_invalid_enabled_type_rejected(enabled):
    with pytest.raises(InvalidGuardrailConfigError, match="enabled"):
        MockChatbot({"guardrails": {"cooking": {"enabled": enabled}}})


@pytest.mark.parametrize(
    "raw",
    [
        "cooking=true",
        ["cooking"],
        {"guardrails": ["cooking"]},
        {"guardrails": {"cooking": True}},
        {"guardrails": {"cooking": {}}},
        {"guardrails": {"cooking": {"enabled": True, "threshold": 100}}},
        {"guardrails": {"Cooking": {"enabled": True}}},
        {"guardrails": {"": {"enabled": True}}},
        {"guardrail": {"cooking": {"enabled": True}}},
        {"guardrails": {"cooking": {"enabled": True}}, "extra": 1},
    ],
)
def test_malformed_configuration_rejected(raw):
    with pytest.raises(InvalidGuardrailConfigError):
        MockChatbot(raw)


def test_invalid_config_error_is_a_value_error():
    assert issubclass(InvalidGuardrailConfigError, ValueError)


def test_multiple_topics_can_be_configured():
    raw = {
        "guardrails": {
            "cooking": {"enabled": True},
            "politics": {"enabled": True},
            "medical_advice": {"enabled": False},
        }
    }
    bot = MockChatbot(raw)
    assert bot.config.topics == ("cooking", "medical_advice", "politics")
    assert bot.config.is_blocked("politics") is True
    assert bot.config.is_blocked("medical_advice") is False
    # Only cooking has detection rules so far; other topics are configured but cannot fire yet.
    assert bot.respond(PASTA_QUESTION).blocked is True
    assert bot.respond("Who should I vote for?").blocked is False


def test_blocked_topics_only_returns_enabled_detected_topics():
    cfg = GuardrailConfig.from_dict(
        {"guardrails": {"cooking": {"enabled": True}, "politics": {"enabled": False}}}
    )
    assert cfg.blocked_topics(["politics", "cooking", "sports"]) == ("cooking",)
