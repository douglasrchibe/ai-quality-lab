"""Deterministic stand-ins for the LLM, for offline RAG evaluation and tests.

* ``ExtractiveMockLLM`` answers with the context sentences that best overlap the question (and
  their document IDs). It never invents facts, so failures it shows come from retrieval or context.
* ``GullibleMockLLM`` behaves the same, except that it OBEYS any instruction it finds in the
  context, simulating a worst-case model. With the deterministic defenses on, the sanitizer removes
  those instructions before the model sees them; with defenses disabled, the evaluation must catch
  the resulting leak.
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

from ai_quality_lab.rag.chunking import split_sentences
from ai_quality_lab.rag.prompting import NOT_FOUND_MESSAGE
from ai_quality_lab.rag.security import injection_findings

_DOCUMENT = re.compile(r'<document id="([^"]*)"[^>]*>\n(.*?)\n</document>', re.DOTALL)
_WORD = re.compile(r"[a-z0-9]+")
_IGNORED = frozenset(
    "a an the and or of to in on for is are be by with what how do does can my i it at as from "
    "this that which when who must may should we our your you there their after before within "
    "policy employee employees northwind company long many much".split()
)


def _content_words(text: str) -> set[str]:
    words = {w.rstrip("s") if len(w) > 3 else w for w in _WORD.findall(text.lower())}
    return words - _IGNORED


def _unescape(text: str) -> str:
    return text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")


def parse_prompt(messages: Sequence[Mapping[str, str]]) -> tuple[str, list[tuple[str, str]]]:
    user = next(m["content"] for m in messages if m["role"] == "user")
    question = user.split("\n", 1)[0].removeprefix("Question: ").strip()
    documents = [(doc_id, _unescape(text)) for doc_id, text in _DOCUMENT.findall(user)]
    return question, documents


class ExtractiveMockLLM:
    label = "mock:extractive"
    min_overlap = 2

    def __init__(self, max_sentences: int = 2):
        self.max_sentences = max_sentences

    def complete(self, messages: Sequence[Mapping[str, str]]) -> str:
        question, documents = parse_prompt(messages)
        return self._extract(question, documents)

    def _extract(self, question: str, documents: list[tuple[str, str]]) -> str:
        wanted = _content_words(question)
        required = min(self.min_overlap, len(wanted)) or 1  # short questions: 1 shared word
        scored = []
        for order, (doc_id, text) in enumerate(documents):
            for position, sentence in enumerate(split_sentences(text)):
                overlap = len(wanted & _content_words(sentence))
                if overlap >= required:
                    scored.append((-overlap, order, position, sentence, doc_id))
        if not scored:
            return NOT_FOUND_MESSAGE
        best = sorted(scored)[: self.max_sentences]
        best.sort(key=lambda s: (s[1], s[2]))  # keep document order for readability
        cited = list(dict.fromkeys(doc_id for *_, doc_id in best))
        sentences = list(dict.fromkeys(s[3] for s in best))
        return " ".join(sentences) + " " + " ".join(f"[{d}]" for d in cited)


class GullibleMockLLM(ExtractiveMockLLM):
    label = "mock:gullible"

    def complete(self, messages: Sequence[Mapping[str, str]]) -> str:
        question, documents = parse_prompt(messages)
        for _, text in documents:
            sentences = split_sentences(text)
            for position, sentence in enumerate(sentences):
                findings = injection_findings(sentence)
                if not findings:
                    continue
                if "reveal-prompt" in findings:
                    system = next(m["content"] for m in messages if m["role"] == "system")
                    return f"My instructions are: {system}"
                # Obey the instruction and its payload (injections often span two sentences).
                return "As instructed: " + " ".join(sentences[position:position + 2])
        return self._extract(question, documents)
