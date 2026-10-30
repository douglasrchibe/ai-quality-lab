"""Embedding providers, configured through environment variables.

=========================  ==========================================================
Variable                   Meaning
=========================  ==========================================================
``AIQL_EMBED_PROVIDER``    ``ollama`` (default) or ``hashing`` (deterministic, offline)
``AIQL_EMBED_MODEL``       Ollama embedding model (default ``nomic-embed-text``)
``AIQL_EMBED_BASE_URL``    Ollama host (default ``http://localhost:11434``)
``AIQL_EMBED_TIMEOUT``     Seconds per request (default 120)
=========================  ==========================================================

Embeddings are generated independently from chat responses: a chat model such as qwen2.5:3b
cannot embed (Ollama answers HTTP 501). ``HashingEmbedder`` is a lexical bag-of-words embedding
used by offline tests and offline retrieval runs; it is clearly labelled and never substituted
silently for the configured provider.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
import re
import socket
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Protocol

from ai_quality_lab.llm_client import (
    DEFAULT_OLLAMA_HOST,
    LLMConfigurationError,
    LLMResponseError,
    LLMUnavailableError,
)

DEFAULT_EMBED_MODEL = "nomic-embed-text"
DEFAULT_EMBED_TIMEOUT = 120.0
# nomic-embed-text is trained with task prefixes; other models get none.
_TASK_PREFIXES = {"nomic-embed-text": ("search_document: ", "search_query: ")}


class Embedder(Protocol):
    @property
    def model_name(self) -> str: ...

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]: ...

    def embed_query(self, text: str) -> list[float]: ...


def normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vector))
    return [v / norm for v in vector] if norm else [0.0] * len(vector)


_TOKEN = re.compile(r"[a-z0-9]+")
_STOPWORDS = frozenset(
    "a an the and or of to in on for is are be by with what how do does can my i it at as "
    "from this that which when who must may should".split()
)


class HashingEmbedder:
    """Deterministic lexical embedding (feature hashing of word unigrams and bigrams)."""

    def __init__(self, dimension: int = 512):
        self.dimension = dimension

    @property
    def model_name(self) -> str:
        return f"hashing-{self.dimension}"

    def _vector(self, text: str) -> list[float]:
        tokens = [t for t in _TOKEN.findall(text.lower()) if t not in _STOPWORDS]
        tokens = [t[:-1] if len(t) > 3 and t.endswith("s") else t for t in tokens]
        features = tokens + [f"{a}_{b}" for a, b in zip(tokens, tokens[1:])]
        vector = [0.0] * self.dimension
        for feature in features:
            digest = hashlib.md5(feature.encode()).digest()
            index = int.from_bytes(digest[:4], "little") % self.dimension
            vector[index] += 1.0 if digest[4] % 2 else -1.0
        return normalize(vector)

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        return [self._vector(t) for t in texts]

    def embed_query(self, text: str) -> list[float]:
        return self._vector(text)


@dataclass(frozen=True)
class OllamaEmbedder:
    """Calls Ollama's ``POST /api/embed`` with the standard library."""

    model: str = DEFAULT_EMBED_MODEL
    base_url: str = DEFAULT_OLLAMA_HOST
    timeout_seconds: float = DEFAULT_EMBED_TIMEOUT

    @property
    def model_name(self) -> str:
        return f"ollama:{self.model}"

    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        prefix = _TASK_PREFIXES.get(self.model.split(":")[0], ("", ""))[0]
        return self._embed([prefix + t for t in texts])

    def embed_query(self, text: str) -> list[float]:
        prefix = _TASK_PREFIXES.get(self.model.split(":")[0], ("", ""))[1]
        return self._embed([prefix + text])[0]

    def _embed(self, inputs: list[str]) -> list[list[float]]:
        if not inputs:
            return []
        url = f"{self.base_url.rstrip('/')}/api/embed"
        body = json.dumps({"model": self.model, "input": inputs}).encode()
        request = urllib.request.Request(
            url, data=body, headers={"Content-Type": "application/json"}, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout_seconds) as reply:
                data = json.loads(reply.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace")[:200]
            if exc.code == 404 or "not found" in detail:
                raise LLMUnavailableError(
                    f"Ollama has no embedding model {self.model!r}. Run: ollama pull {self.model}"
                ) from None
            if exc.code == 501:
                raise LLMConfigurationError(
                    f"{self.model!r} does not support embeddings (HTTP 501). Set "
                    f"AIQL_EMBED_MODEL to an embedding model such as {DEFAULT_EMBED_MODEL}."
                ) from None
            raise LLMResponseError(f"Ollama embed HTTP {exc.code}: {detail}") from None
        except (TimeoutError, socket.timeout):
            raise LLMUnavailableError(
                f"Ollama embeddings did not answer within {self.timeout_seconds:g}s."
            ) from None
        except urllib.error.URLError as exc:
            raise LLMUnavailableError(
                f"Cannot reach Ollama at {self.base_url} ({exc.reason}). Is Ollama running?"
            ) from None
        vectors = data.get("embeddings") if isinstance(data, dict) else None
        if not isinstance(vectors, list) or len(vectors) != len(inputs):
            raise LLMResponseError("Ollama returned an unexpected embeddings payload.")
        dimensions = {len(v) for v in vectors}
        if len(dimensions) != 1 or 0 in dimensions:
            raise LLMResponseError(f"Inconsistent embedding dimensions: {sorted(dimensions)}")
        return [normalize(v) for v in vectors]


def embedder_from_env(env: Mapping[str, str] | None = None) -> Embedder:
    env = os.environ if env is None else env
    provider = env.get("AIQL_EMBED_PROVIDER", "ollama").strip().lower()
    if provider == "hashing":
        return HashingEmbedder()
    if provider != "ollama":
        raise LLMConfigurationError(
            f"AIQL_EMBED_PROVIDER must be 'ollama' or 'hashing', got {provider!r}."
        )
    raw_timeout = env.get("AIQL_EMBED_TIMEOUT", "").strip()
    try:
        timeout = float(raw_timeout) if raw_timeout else DEFAULT_EMBED_TIMEOUT
    except ValueError:
        raise LLMConfigurationError(f"AIQL_EMBED_TIMEOUT must be a number, got {raw_timeout!r}.")
    base_url = env.get("AIQL_EMBED_BASE_URL", "").strip() or DEFAULT_OLLAMA_HOST
    return OllamaEmbedder(
        model=env.get("AIQL_EMBED_MODEL", "").strip() or DEFAULT_EMBED_MODEL,
        base_url=base_url.rstrip("/").removesuffix("/v1"),
        timeout_seconds=timeout,
    )
