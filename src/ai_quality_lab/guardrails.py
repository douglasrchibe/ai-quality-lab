"""Guardrail configuration and topic detection for the mock chatbot.

This module simulates two components of a real deployment:

* a CMS-managed guardrail configuration (e.g. topic restrictions edited in Sanity), and
* a guardrail component (e.g. LLM Guard) that classifies the topic of a request.

Topic detection here is **keyword-based**. It matches known words and phrases; it does not
understand meaning, paraphrase, misspellings, or context.
"""

from __future__ import annotations

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, Protocol

_TOPIC_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_]*$")
_TOPIC_SETTING_KEYS = frozenset({"enabled"})
_TOP_LEVEL_KEYS = frozenset({"guardrails"})

DEFAULT_TOPIC_KEYWORDS: Mapping[str, tuple[str, ...]] = {
    "cooking": (
        "cook",
        "cooks",
        "cooking",
        "cooked",
        "how to cook",
        "recipe",
        "recipes",
        "pasta",
        "bake",
        "baking",
        "baked",
        "ingredient",
        "ingredients",
        "meal prep",
        "meal preparation",
        "boil",
        "simmer",
        "saute",
        "roast",
        "fry",
        "frying",
    ),
}


class InvalidGuardrailConfigError(ValueError):
    """Raised when a guardrail configuration is missing, malformed, or uses unsupported values."""


def normalize_text(text: str) -> str:
    """Case-fold, trim, and collapse internal whitespace so formatting does not affect matching."""
    return " ".join(text.casefold().split())


@dataclass(frozen=True)
class TopicGuardrail:
    """Guardrail setting for one topic. ``enabled=True`` blocks requests on this topic."""

    topic: str
    enabled: bool


@dataclass(frozen=True)
class GuardrailConfig:
    """Validated, immutable guardrail configuration.

    Build it with :meth:`from_dict` from the raw structure, e.g.
    ``{"guardrails": {"cooking": {"enabled": True}}}``.
    """

    guardrails: tuple[TopicGuardrail, ...]

    @classmethod
    def from_dict(cls, raw: Any) -> GuardrailConfig:
        """Validate a raw configuration mapping.

        Raises:
            InvalidGuardrailConfigError: if the structure, keys, topic names, or value types are
                invalid. Malformed configuration is never interpreted as "allow everything".
        """
        topics = _require_guardrails_section(raw)
        guardrails = tuple(
            _parse_topic_guardrail(topic, settings) for topic, settings in topics.items()
        )
        return cls(guardrails=tuple(sorted(guardrails, key=lambda g: g.topic)))

    @property
    def topics(self) -> tuple[str, ...]:
        """All configured topic names, sorted."""
        return tuple(guardrail.topic for guardrail in self.guardrails)

    def is_blocked(self, topic: str) -> bool:
        """Return True if ``topic`` is configured with its guardrail enabled."""
        return any(g.topic == topic and g.enabled for g in self.guardrails)

    def blocked_topics(self, detected_topics: Iterable[str]) -> tuple[str, ...]:
        """Return the subset of ``detected_topics`` whose guardrail is enabled, sorted."""
        return tuple(sorted(topic for topic in set(detected_topics) if self.is_blocked(topic)))


def _require_guardrails_section(raw: Any) -> Mapping[str, Any]:
    if not isinstance(raw, Mapping):
        raise InvalidGuardrailConfigError(
            f"Configuration must be a mapping, got {type(raw).__name__}."
        )
    unknown_keys = set(raw) - _TOP_LEVEL_KEYS
    if unknown_keys:
        raise InvalidGuardrailConfigError(
            f"Unsupported top-level configuration keys: {sorted(map(str, unknown_keys))}."
        )
    if "guardrails" not in raw:
        raise InvalidGuardrailConfigError("Configuration is missing the 'guardrails' section.")
    topics = raw["guardrails"]
    if not isinstance(topics, Mapping):
        raise InvalidGuardrailConfigError(
            f"'guardrails' must be a mapping of topic settings, got {type(topics).__name__}."
        )
    if not topics:
        raise InvalidGuardrailConfigError("'guardrails' must configure at least one topic.")
    return topics


def _parse_topic_guardrail(topic: Any, settings: Any) -> TopicGuardrail:
    if not isinstance(topic, str) or not _TOPIC_NAME_PATTERN.fullmatch(topic):
        raise InvalidGuardrailConfigError(
            f"Invalid topic name {topic!r}: use lowercase letters, digits, and underscores."
        )
    if not isinstance(settings, Mapping):
        raise InvalidGuardrailConfigError(
            f"Settings for topic '{topic}' must be a mapping, got {type(settings).__name__}."
        )
    unknown_keys = set(settings) - _TOPIC_SETTING_KEYS
    if unknown_keys:
        raise InvalidGuardrailConfigError(
            f"Unsupported settings for topic '{topic}': {sorted(map(str, unknown_keys))}."
        )
    if "enabled" not in settings:
        raise InvalidGuardrailConfigError(f"Topic '{topic}' is missing the 'enabled' setting.")
    enabled = settings["enabled"]
    # bool is a subclass of int, so an isinstance check alone would let 0/1 through.
    if type(enabled) is not bool:
        raise InvalidGuardrailConfigError(
            f"'enabled' for topic '{topic}' must be true or false, got {enabled!r}."
        )
    return TopicGuardrail(topic=topic, enabled=enabled)


class TopicDetector(Protocol):
    """Anything that can map a message to a sorted tuple of topic names."""

    def detect(self, text: str) -> tuple[str, ...]: ...


class KeywordTopicDetector:
    """Deterministic keyword/phrase matcher.

    Matching is case-insensitive, ignores surrounding and repeated whitespace, and respects word
    boundaries ("cook" matches "cook" but not "cookie"). It is a simulation: it cannot recognise
    synonyms, paraphrases, misspellings, or intent.
    """

    def __init__(self, topic_keywords: Mapping[str, Iterable[str]] = DEFAULT_TOPIC_KEYWORDS):
        self._patterns = {
            topic: _compile_keyword_pattern(keywords) for topic, keywords in topic_keywords.items()
        }

    @property
    def supported_topics(self) -> tuple[str, ...]:
        """Topics this detector has rules for, sorted."""
        return tuple(sorted(self._patterns))

    def detect(self, text: str) -> tuple[str, ...]:
        """Return the sorted topics whose keywords appear in ``text``."""
        normalized = normalize_text(text)
        if not normalized:
            return ()
        return tuple(
            sorted(topic for topic, pattern in self._patterns.items() if pattern.search(normalized))
        )


def _compile_keyword_pattern(keywords: Iterable[str]) -> re.Pattern[str]:
    normalized = sorted({normalize_text(k) for k in keywords if normalize_text(k)})
    if not normalized:
        raise ValueError("Each topic needs at least one non-empty keyword.")
    alternatives = "|".join(re.escape(keyword) for keyword in normalized)
    return re.compile(rf"\b(?:{alternatives})\b")
