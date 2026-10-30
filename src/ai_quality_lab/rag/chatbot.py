"""RAGChatbot: answers questions from the knowledge base through the existing LLM client.

Flow (each step deterministic except the model call):
1. validate input (same contract as the other chatbots);
2. input guardrails: configured topics (Phase 2 ``GuardrailConfig``) and prompt-extraction requests;
3. retrieve chunks (classification filter, threshold, dedupe);
4. sanitize each retrieved chunk (untrusted data) and assemble context;
5. build the prompt: trusted rules in the system message, documents as escaped data;
6. call the LLM (any ``ChatClient``: Ollama/OpenAI client, or a mock);
7. output guard: canary leak, secret patterns, unapproved email domains -> answer replaced.

``MockChatbot`` and ``GuardedLLMChatbot`` are unchanged; this is a separate class with the same
``response`` / ``blocked`` / ``detected_topics`` fields, plus retrieval provenance.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from ai_quality_lab.guardrails import GuardrailConfig, KeywordTopicDetector, TopicDetector
from ai_quality_lab.llm_client import ChatClient
from ai_quality_lab.mock_agent import INVALID_INPUT_RESPONSE, REFUSAL_TEMPLATE, ResponseOutcome
from ai_quality_lab.rag.prompting import (
    NOT_FOUND_MESSAGE,
    OUTPUT_BLOCKED_MESSAGE,
    PROMPT_EXTRACTION_REFUSAL,
    ContextItem,
    build_rag_messages,
    build_system_prompt,
)
from ai_quality_lab.rag.retrieval import RetrievalResult, Retriever
from ai_quality_lab.rag.security import (
    looks_like_prompt_extraction,
    make_canary,
    output_violations,
    sanitize,
)

DEFAULT_RAG_CONFIG = {"guardrails": {"cooking": {"enabled": True}}}


class RAGSimulatedFailure(str, Enum):
    """Test-only defects, used to prove the evaluation detects them."""

    NONE = "none"
    DEFENSES_DISABLED = "defenses_disabled"  # no input guard, sanitizer, or output guard
    RETRIEVAL_DEGRADED = "retrieval_degraded"  # returns the worst-matching chunks first


@dataclass(frozen=True)
class RAGResponse:
    message: str
    response: str
    blocked: bool
    detected_topics: tuple[str, ...]
    outcome: ResponseOutcome
    reason: str
    retrieval: RetrievalResult | None = None
    context: tuple[ContextItem, ...] = ()
    guardrails_triggered: tuple[str, ...] = ()
    model_label: str = ""
    raw_model_output: str | None = field(default=None, repr=False)  # before the output guard

    @property
    def retrieved_doc_ids(self) -> tuple[str, ...]:
        return self.retrieval.doc_ids if self.retrieval else ()

    @property
    def retrieved_chunk_ids(self) -> tuple[str, ...]:
        return self.retrieval.chunk_ids if self.retrieval else ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "response": self.response,
            "blocked": self.blocked,
            "detected_topics": list(self.detected_topics),
            "outcome": self.outcome.value,
            "reason": self.reason,
            "guardrails_triggered": list(self.guardrails_triggered),
            "model": self.model_label,
            "raw_model_output": self.raw_model_output,
            "retrieval": self.retrieval.to_dict() if self.retrieval else None,
            "context": [item.to_dict() for item in self.context],
        }


class RAGChatbot:
    def __init__(
        self,
        retriever: Retriever,
        client: ChatClient,
        config: Mapping[str, Any] | None = None,
        *,
        detector: TopicDetector | None = None,
        simulated_failure: RAGSimulatedFailure = RAGSimulatedFailure.NONE,
    ):
        self._config = GuardrailConfig.from_dict(config or DEFAULT_RAG_CONFIG)
        self._retriever = retriever
        self._client = client
        self._detector = detector or KeywordTopicDetector()
        self._failure = RAGSimulatedFailure(simulated_failure)
        restricted = tuple(g.topic for g in self._config.guardrails if g.enabled)
        self.canary = make_canary(build_system_prompt(restricted, ""))
        self.system_prompt = build_system_prompt(restricted, self.canary)

    @property
    def defenses_enabled(self) -> bool:
        return self._failure is not RAGSimulatedFailure.DEFENSES_DISABLED

    def respond(self, message: str) -> RAGResponse:
        """Raises ``TypeError`` for non-string input; embedder/LLM errors propagate."""
        if not isinstance(message, str):
            raise TypeError(f"message must be a str, got {type(message).__name__}.")
        if not message.strip():
            return self._reply(message, INVALID_INPUT_RESPONSE, ResponseOutcome.INVALID_INPUT,
                               "Empty or whitespace-only message.")
        detected = self._detector.detect(message)
        if self.defenses_enabled:
            blocked_topics = self._config.blocked_topics(detected)
            if blocked_topics:
                return self._reply(
                    message, REFUSAL_TEMPLATE.format(topics=", ".join(blocked_topics)),
                    ResponseOutcome.BLOCKED, "Blocked by topic guardrail before retrieval.",
                    blocked=True, detected=detected,
                    triggered=tuple(f"input:topic:{t}" for t in blocked_topics),
                )
            if looks_like_prompt_extraction(message):
                return self._reply(
                    message, PROMPT_EXTRACTION_REFUSAL, ResponseOutcome.BLOCKED,
                    "Request for internal instructions refused before retrieval.",
                    blocked=True, detected=detected, triggered=("input:prompt_extraction",),
                )

        retrieval = self._retriever.retrieve(
            message, worst_first=self._failure is RAGSimulatedFailure.RETRIEVAL_DEGRADED
        )
        context, triggered = self._assemble(retrieval)
        if not any(item.text for item in context):
            return self._reply(message, NOT_FOUND_MESSAGE, ResponseOutcome.FALLBACK,
                               "No usable documents were retrieved; the model was not called.",
                               detected=detected, retrieval=retrieval, context=context,
                               triggered=triggered)

        answer = self._client.complete(build_rag_messages(message, context, self.system_prompt))
        if self.defenses_enabled:
            violations = output_violations(answer, self.canary)
            if violations:
                return self._reply(
                    message, OUTPUT_BLOCKED_MESSAGE, ResponseOutcome.BLOCKED,
                    f"Output guard blocked the model answer: {', '.join(violations)}.",
                    blocked=True, detected=detected, retrieval=retrieval, context=context,
                    triggered=triggered + tuple(f"output:{v}" for v in violations),
                    raw=answer,
                )
        return self._reply(message, answer, ResponseOutcome.ANSWERED,
                           f"Answered by {self._client.label} from {len(context)} chunk(s).",
                           detected=detected, retrieval=retrieval, context=context,
                           triggered=triggered, raw=answer)

    def _assemble(
        self, retrieval: RetrievalResult
    ) -> tuple[tuple[ContextItem, ...], tuple[str, ...]]:
        items, triggered = [], []
        for chunk in retrieval.chunks:
            text, removed = chunk.text, ()
            if self.defenses_enabled:
                cleaned = sanitize(chunk.text)
                text, removed = cleaned.text, cleaned.removed
                if removed:
                    triggered.append(f"context:injection_removed:{chunk.chunk_id}")
            items.append(ContextItem(
                chunk_id=chunk.chunk_id, doc_id=chunk.doc_id,
                title=chunk.metadata.get("title", ""),
                status=chunk.metadata.get("status", ""),
                effective_date=chunk.metadata.get("effective_date", ""),
                text=text, removed=removed,
            ))
        return tuple(items), tuple(triggered)

    def _reply(self, message, text, outcome, reason, *, blocked=False, detected=(),
               retrieval=None, context=(), triggered=(), raw=None) -> RAGResponse:
        return RAGResponse(
            message=message, response=text, blocked=blocked, detected_topics=tuple(detected),
            outcome=outcome, reason=reason, retrieval=retrieval, context=tuple(context),
            guardrails_triggered=tuple(triggered), model_label=self._client.label,
            raw_model_output=raw,
        )
