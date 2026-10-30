"""Deterministic mock chatbot with configurable topic guardrails.

This is **not** an AI model. Every reply is a fixed string chosen by simple rules, so tests and
future evaluations get identical results for the same message, configuration, and failure mode.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

from ai_quality_lab.guardrails import (
    GuardrailConfig,
    KeywordTopicDetector,
    TopicDetector,
    normalize_text,
)

SAMPLE_PASTA_RECIPE = (
    "Here is a simple pasta recipe: boil 200 g of spaghetti in salted water for 10 minutes. "
    "Meanwhile, warm 2 tablespoons of olive oil with 2 sliced garlic cloves. Drain the pasta, "
    "toss it with the garlic oil, and finish with grated parmesan and black pepper."
)
REFUSAL_TEMPLATE = "Sorry, I can't help with {topics} requests."
FALLBACK_RESPONSE = "I'm a mock assistant and don't have a prepared answer for that question."
INVALID_INPUT_RESPONSE = "Please enter a question. The message was empty."

# Keys are normalized questions with trailing punctuation removed.
KNOWN_ANSWERS: Mapping[str, str] = {
    "what is the capital of france": "The capital of France is Paris.",
    "what is 2 + 2": "2 + 2 equals 4.",
    "hello": "Hello! How can I help you today?",
    "what can you do": "I'm a mock assistant that answers a small set of predefined questions.",
}


class ResponseOutcome(str, Enum):
    """Machine-readable classification of a response, for assertions and future evaluation."""

    BLOCKED = "blocked"
    ANSWERED = "answered"
    FALLBACK = "fallback"
    INVALID_INPUT = "invalid_input"


class SimulatedFailure(str, Enum):
    """Test-only switches that simulate defects so evaluations can prove they catch them.

    ``GUARDRAIL_FAIL_OPEN`` makes the guardrail report that nothing is blocked, so a request that
    should be refused is answered. The response carries no marker of the failure. It looks like a
    normal answer, as a real silent guardrail failure would.
    """

    NONE = "none"
    GUARDRAIL_FAIL_OPEN = "guardrail_fail_open"


@dataclass(frozen=True)
class ChatResponse:
    """Structured result of :meth:`MockChatbot.respond`."""

    message: str
    response: str
    blocked: bool
    detected_topics: tuple[str, ...]
    reason: str
    outcome: ResponseOutcome


class MockChatbot:
    """Rule-based chatbot whose topic restrictions come from a guardrail configuration.

    Args:
        config: Raw configuration such as ``{"guardrails": {"cooking": {"enabled": True}}}``.
            ``enabled: True`` blocks that topic; ``False`` allows it.
        detector: Topic detector; defaults to :class:`KeywordTopicDetector`.
        simulated_failure: Test-only defect simulation. Defaults to ``SimulatedFailure.NONE``.

    Raises:
        InvalidGuardrailConfigError: if ``config`` is invalid.
    """

    def __init__(
        self,
        config: Mapping[str, Any],
        *,
        detector: TopicDetector | None = None,
        simulated_failure: SimulatedFailure = SimulatedFailure.NONE,
    ) -> None:
        self._config = GuardrailConfig.from_dict(config)
        self._detector = detector if detector is not None else KeywordTopicDetector()
        self._simulated_failure = SimulatedFailure(simulated_failure)

    @property
    def config(self) -> GuardrailConfig:
        return self._config

    @property
    def simulated_failure(self) -> SimulatedFailure:
        return self._simulated_failure

    def respond(self, message: str) -> ChatResponse:
        """Return a deterministic response to ``message``.

        Raises:
            TypeError: if ``message`` is not a ``str``.
        """
        if not isinstance(message, str):
            raise TypeError(f"message must be a str, got {type(message).__name__}.")
        if not message.strip():
            return ChatResponse(
                message=message,
                response=INVALID_INPUT_RESPONSE,
                blocked=False,
                detected_topics=(),
                reason="Empty or whitespace-only message.",
                outcome=ResponseOutcome.INVALID_INPUT,
            )

        detected_topics = self._detector.detect(message)
        blocked_topics = self._blocked_topics(detected_topics)
        if blocked_topics:
            return self._refusal(message, detected_topics, blocked_topics)
        return self._answer(message, detected_topics)

    def _blocked_topics(self, detected_topics: tuple[str, ...]) -> tuple[str, ...]:
        # The single place where the simulated guardrail failure applies.
        if self._simulated_failure is SimulatedFailure.GUARDRAIL_FAIL_OPEN:
            return ()
        return self._config.blocked_topics(detected_topics)

    @staticmethod
    def _refusal(
        message: str, detected_topics: tuple[str, ...], blocked_topics: tuple[str, ...]
    ) -> ChatResponse:
        topic_list = ", ".join(blocked_topics)
        return ChatResponse(
            message=message,
            response=REFUSAL_TEMPLATE.format(topics=topic_list),
            blocked=True,
            detected_topics=detected_topics,
            reason=f"Blocked by active guardrail: {topic_list}.",
            outcome=ResponseOutcome.BLOCKED,
        )

    @staticmethod
    def _answer(message: str, detected_topics: tuple[str, ...]) -> ChatResponse:
        if "cooking" in detected_topics:
            text, outcome = SAMPLE_PASTA_RECIPE, ResponseOutcome.ANSWERED
            reason = "No active guardrail blocked the request; returned the sample recipe."
        elif (known := KNOWN_ANSWERS.get(_question_key(message))) is not None:
            text, outcome = known, ResponseOutcome.ANSWERED
            reason = "Matched a predefined question."
        else:
            text, outcome = FALLBACK_RESPONSE, ResponseOutcome.FALLBACK
            reason = "No predefined answer for this question."
        return ChatResponse(
            message=message,
            response=text,
            blocked=False,
            detected_topics=detected_topics,
            reason=reason,
            outcome=outcome,
        )


def _question_key(message: str) -> str:
    return normalize_text(message).rstrip("?!. ")
