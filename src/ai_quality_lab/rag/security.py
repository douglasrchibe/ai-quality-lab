"""Deterministic defenses for retrieved (untrusted) content and generated output.

Prompt instructions alone do not stop prompt injection (Phase 5.5 showed a 3B model following an
instruction planted in its context). These controls run in code, before and after the model:

* ``sanitize`` removes instruction-like sentences from retrieved text before it reaches the model.
* ``looks_like_prompt_extraction`` refuses questions that ask for the hidden instructions.
* ``output_violations`` blocks answers that contain the system-prompt canary, recovery-code-like
  secrets, or email addresses outside the approved domain (a common injection payload).

These are pattern-based heuristics. They stop the injections in this corpus and its tests; they do
not guarantee that every paraphrased injection is caught. See docs/rag-architecture.md.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Sequence
from dataclasses import dataclass

from ai_quality_lab.rag.chunking import split_sentences

# (label, pattern). Matched per sentence, case-insensitive.
INJECTION_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (label, re.compile(pattern, re.IGNORECASE))
    for label, pattern in [
        ("ignore-instructions", r"\b(ignore|forget)\b.{0,40}\b(instruction|prompt|rule|polic)"),
        ("disregard", r"\bdisregard\b.{0,40}\b(instruction|prompt|rule|polic|previous|prior)"),
        ("reveal-prompt", r"\b(reveal|print|show|output|disclose|repeat)\b.{0,40}"
                          r"\b(system prompt|hidden instruction|instructions|your prompt)"),
        ("system-override", r"\b(system|admin|administrator)\s+(override|note|message|command)\b"),
        ("note-to-ai", r"\bnote to (the )?(ai|assistant|model|chatbot|llm)"),
        ("role-prefix", r"^\s*(assistant|system|ai)\s*:"),
        ("persona-switch", r"\byou are now\b|\bfrom now on\b|\bnew instructions?\b"),
        # Imperatives addressed to the assistant about what to tell users ("Tell the user that
        # the admin recovery code is ..."), the payload half of a split injection.
        ("instruct-assistant", r"\b(tell|inform|instruct) (the |every |all |any )?users?\b"),
        ("delimiter-spoof", r"</?\s*(document|documents|system|instructions)\b"),
    ]
)

PROMPT_EXTRACTION = re.compile(
    r"\b(system prompt|hidden instructions?|initial prompt|internal instructions?|"
    r"your (instructions|rules|prompt)|developer message)\b",
    re.IGNORECASE,
)
RECOVERY_CODE = re.compile(r"\bRC-\d{4}-[A-Z]{3,}\b")
EMAIL = re.compile(r"\b[A-Za-z0-9._%+-]+@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b")
APPROVED_EMAIL_DOMAINS = ("northwind.example",)


@dataclass(frozen=True)
class SanitizedText:
    text: str
    removed: tuple[str, ...]  # removed sentences, verbatim
    findings: tuple[str, ...]  # pattern labels that matched


def injection_findings(sentence: str) -> list[str]:
    return [label for label, pattern in INJECTION_PATTERNS if pattern.search(sentence)]


def sanitize(text: str) -> SanitizedText:
    """Drop sentences that look like instructions to the model; keep everything else verbatim."""
    kept, removed, findings = [], [], []
    for sentence in split_sentences(text):
        labels = injection_findings(sentence)
        if labels:
            removed.append(sentence)
            findings.extend(labels)
        else:
            kept.append(sentence)
    return SanitizedText(" ".join(kept), tuple(removed), tuple(dict.fromkeys(findings)))


def looks_like_prompt_extraction(message: str) -> bool:
    return bool(PROMPT_EXTRACTION.search(message))


def make_canary(system_prompt: str) -> str:
    """Stable marker embedded in the system prompt; seeing it in output proves a prompt leak."""
    return "AIQL-CANARY-" + hashlib.sha256(system_prompt.encode()).hexdigest()[:10].upper()


def output_violations(
    answer: str,
    canary: str,
    approved_email_domains: Sequence[str] = APPROVED_EMAIL_DOMAINS,
) -> list[str]:
    """Reasons the answer must not be shown. Empty list = no violation."""
    violations = []
    if canary and canary.lower() in answer.lower():
        violations.append("system_prompt_leak")
    if RECOVERY_CODE.search(answer):
        violations.append("secret_pattern")
    for match in EMAIL.finditer(answer):
        domain = match.group(1).lower()
        if not any(domain == d or domain.endswith("." + d) for d in approved_email_domains):
            violations.append(f"unapproved_email_domain:{domain}")
    return violations
