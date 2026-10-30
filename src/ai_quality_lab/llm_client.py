"""Configuration and a minimal HTTP client for real LLM providers.

The client speaks the OpenAI-compatible ``/chat/completions`` API, which both OpenAI and a local
Ollama server (``http://localhost:11434/v1``) implement, so one small standard-library client covers
both. No provider SDK is required to *chat*; DeepEval (optional extra) is only needed to *judge*.

Configuration comes from environment variables, optionally loaded from a git-ignored ``.env`` file:

==========================  =================================================================
Variable                    Meaning
==========================  =================================================================
``AIQL_LLM_PROVIDER``       ``ollama`` (default) or ``openai``
``AIQL_LLM_MODEL``          Model name. Default ``qwen2.5:3b`` for Ollama; required for OpenAI
``AIQL_LLM_BASE_URL``       Override the server URL (Ollama host or OpenAI-compatible base URL)
``AIQL_LLM_TIMEOUT``        Seconds per request (default 180; local CPU inference is slow)
``AIQL_LLM_MAX_TOKENS``     Maximum tokens in a reply (default 300)
``OPENAI_API_KEY``          Required only when a provider is ``openai``
``AIQL_JUDGE_*``            Same keys for the DeepEval judge model; provider/model/URL default to
                            the ``AIQL_LLM_*`` values; ``AIQL_JUDGE_TIMEOUT`` defaults to 600
                            and ``AIQL_JUDGE_MAX_TOKENS`` to 1024
==========================  =================================================================
"""

from __future__ import annotations

import json
import os
import socket
import urllib.error
import urllib.request
from collections.abc import Mapping, MutableMapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Protocol

PROVIDERS = ("ollama", "openai")
DEFAULT_OLLAMA_HOST = "http://localhost:11434"
DEFAULT_OLLAMA_MODEL = "qwen2.5:3b"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_TIMEOUT_SECONDS = 180.0
DEFAULT_JUDGE_TIMEOUT_SECONDS = 600.0
"""Per judge call. DeepEval metric prompts are long; a CPU-only local judge can take minutes."""
DEFAULT_MAX_TOKENS = 300
DEFAULT_JUDGE_MAX_TOKENS = 1024
"""Cap on judge output. Small local models constrained to JSON can loop without stopping; the cap
turns that hang into a fast, reported judge ERROR."""
GENERATION_SEED = 42
"""Fixed seed with temperature 0 for the most repeatable output a provider allows (not exact)."""


# --- Errors -------------------------------------------------------------------------------------


class LLMError(RuntimeError):
    """Base class for LLM configuration and provider failures."""


class LLMConfigurationError(LLMError):
    """Settings are missing or invalid (e.g. unknown provider, missing API key)."""


class LLMUnavailableError(LLMError):
    """The provider could not be reached, or the model is not available."""


class LLMResponseError(LLMError):
    """The provider answered with an error status or an unusable body."""


# --- Settings -----------------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMSettings:
    provider: str
    model: str
    base_url: str
    api_key: str | None = field(default=None, repr=False)  # never shown in repr or logs
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS
    max_tokens: int = DEFAULT_MAX_TOKENS

    @property
    def chat_completions_url(self) -> str:
        root = self.base_url.rstrip("/")
        if self.provider == "ollama":
            return f"{root}/v1/chat/completions"
        return f"{root}/chat/completions"

    @property
    def label(self) -> str:
        return f"{self.provider}:{self.model}"

    @classmethod
    def from_env(
        cls, env: Mapping[str, str] | None = None, *, prefix: str = "AIQL_LLM_"
    ) -> LLMSettings:
        """Build settings from environment variables with the given prefix.

        Raises:
            LLMConfigurationError: with an actionable message if a value is missing or invalid.
        """
        env = os.environ if env is None else env
        provider = env.get(f"{prefix}PROVIDER", "ollama").strip().lower()
        if provider not in PROVIDERS:
            raise LLMConfigurationError(
                f"{prefix}PROVIDER must be one of {list(PROVIDERS)}, got {provider!r}."
            )
        model = env.get(f"{prefix}MODEL", "").strip()
        base_url = env.get(f"{prefix}BASE_URL", "").strip()
        api_key = None
        if provider == "ollama":
            model = model or DEFAULT_OLLAMA_MODEL
            # Accept the OpenAI-compatible form (".../v1") too; the host is what both the chat
            # client and DeepEval's OllamaModel need, and "/v1/v1/..." would 404 misleadingly.
            base_url = (base_url or DEFAULT_OLLAMA_HOST).rstrip("/").removesuffix("/v1")
        else:
            if not model:
                raise LLMConfigurationError(
                    f"{prefix}MODEL is required for provider 'openai' (choose a model from your "
                    "provider's model list and set it in .env)."
                )
            base_url = base_url or DEFAULT_OPENAI_BASE_URL
            api_key = env.get("OPENAI_API_KEY", "").strip() or None
            if api_key is None:
                raise LLMConfigurationError(
                    "OPENAI_API_KEY is not set. Add it to your local .env file (see .env.example); "
                    "never commit it."
                )
        return cls(
            provider=provider,
            model=model,
            base_url=base_url,
            api_key=api_key,
            timeout_seconds=_positive_number(env, f"{prefix}TIMEOUT", DEFAULT_TIMEOUT_SECONDS),
            max_tokens=int(_positive_number(env, f"{prefix}MAX_TOKENS", DEFAULT_MAX_TOKENS)),
        )

    @classmethod
    def judge_from_env(cls, env: Mapping[str, str] | None = None) -> LLMSettings:
        """Judge settings: ``AIQL_JUDGE_*`` values.

        Provider, model and URL fall back to the chatbot's ``AIQL_LLM_*`` values (model and URL only
        when the provider is the same). The timeout does not: judge prompts are much longer than
        chat prompts, so it defaults to ``DEFAULT_JUDGE_TIMEOUT_SECONDS``.
        """
        env = os.environ if env is None else env
        chatbot_provider = env.get("AIQL_LLM_PROVIDER", "ollama")
        judge_provider = env.get("AIQL_JUDGE_PROVIDER") or chatbot_provider
        merged = {k: v for k, v in env.items() if not k.startswith("AIQL_LLM_")}
        merged["AIQL_LLM_PROVIDER"] = judge_provider
        merged["AIQL_LLM_TIMEOUT"] = env.get("AIQL_JUDGE_TIMEOUT") or str(
            DEFAULT_JUDGE_TIMEOUT_SECONDS
        )
        merged["AIQL_LLM_MAX_TOKENS"] = env.get("AIQL_JUDGE_MAX_TOKENS") or str(
            DEFAULT_JUDGE_MAX_TOKENS
        )
        for key in ("MODEL", "BASE_URL"):
            value = env.get(f"AIQL_JUDGE_{key}")
            # A judge on a different provider must not inherit the chatbot's model or URL.
            if not value and judge_provider == chatbot_provider:
                value = env.get(f"AIQL_LLM_{key}")
            if value:
                merged[f"AIQL_LLM_{key}"] = value
        return cls.from_env(merged)


def _positive_number(env: Mapping[str, str], key: str, default: float) -> float:
    raw = env.get(key, "").strip()
    if not raw:
        return default
    try:
        value = float(raw)
    except ValueError:
        raise LLMConfigurationError(f"{key} must be a number, got {raw!r}.") from None
    if value <= 0:
        raise LLMConfigurationError(f"{key} must be greater than 0, got {raw!r}.")
    return value


def load_env_file(
    path: str | Path = ".env", environ: MutableMapping[str, str] | None = None
) -> list[str]:
    """Load ``KEY=VALUE`` lines from a .env file without overriding existing variables.

    Supports comments (``#``), blank lines, an optional ``export`` prefix, and single/double
    quotes. Returns the names that were set (never the values). A missing file is not an error.
    """
    environ = os.environ if environ is None else environ
    file = Path(path)
    if not file.is_file():
        return []
    loaded = []
    for line in file.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.removeprefix("export ").split("=", 1)
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        if key and key not in environ:
            environ[key] = value
            loaded.append(key)
    return loaded


# --- Client -------------------------------------------------------------------------------------


class ChatClient(Protocol):
    """Anything that can turn chat messages into reply text."""

    @property
    def label(self) -> str: ...

    def complete(self, messages: Sequence[Mapping[str, str]]) -> str: ...


class OpenAICompatibleClient:
    """Calls an OpenAI-compatible ``/chat/completions`` endpoint with the standard library."""

    def __init__(self, settings: LLMSettings):
        self.settings = settings

    @property
    def label(self) -> str:
        return self.settings.label

    def complete(self, messages: Sequence[Mapping[str, str]]) -> str:
        payload = {
            "model": self.settings.model,
            "messages": list(messages),
            "temperature": 0,
            "seed": GENERATION_SEED,
            "max_tokens": self.settings.max_tokens,
            "stream": False,
        }
        body = self._post(self.settings.chat_completions_url, payload)
        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError):
            raise LLMResponseError(
                f"{self.label} returned an unexpected response shape: {_snippet(body)}"
            ) from None
        if not isinstance(content, str):
            raise LLMResponseError(f"{self.label} returned non-text content.")
        return content.strip()

    def _post(self, url: str, payload: dict[str, Any]) -> Any:
        headers = {"Content-Type": "application/json"}
        if self.settings.api_key:
            headers["Authorization"] = f"Bearer {self.settings.api_key}"
        request = urllib.request.Request(
            url, data=json.dumps(payload).encode("utf-8"), headers=headers, method="POST"
        )
        try:
            with urllib.request.urlopen(request, timeout=self.settings.timeout_seconds) as reply:
                raw = reply.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            raise self._http_error(exc) from None
        except (TimeoutError, socket.timeout):
            raise LLMUnavailableError(
                f"{self.label} did not answer within {self.settings.timeout_seconds:g}s. Local "
                "models on a CPU can be slow; raise AIQL_LLM_TIMEOUT or use a smaller model."
            ) from None
        except urllib.error.URLError as exc:
            raise LLMUnavailableError(self._unreachable_message(exc.reason)) from None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            raise LLMResponseError(f"{self.label} returned invalid JSON: {raw[:200]!r}") from None

    def _http_error(self, exc: urllib.error.HTTPError) -> LLMError:
        detail = exc.read().decode("utf-8", errors="replace")[:300]
        if exc.code in (401, 403):
            return LLMConfigurationError(
                f"{self.label}: authentication failed (HTTP {exc.code}). Check OPENAI_API_KEY."
            )
        if exc.code == 404 and self.settings.provider == "ollama":
            return LLMUnavailableError(
                f"Ollama has no model {self.settings.model!r}. Run: ollama pull "
                f"{self.settings.model}"
            )
        if exc.code == 429:
            return LLMUnavailableError(f"{self.label}: rate limited or out of quota (HTTP 429).")
        return LLMResponseError(f"{self.label}: HTTP {exc.code}: {detail}")

    def _unreachable_message(self, reason: object) -> str:
        if self.settings.provider == "ollama":
            return (
                f"Cannot reach Ollama at {self.settings.base_url} ({reason}). Is Ollama installed "
                f"and running? Install from https://ollama.com/download/windows, then run: "
                f"ollama pull {self.settings.model}"
            )
        return f"Cannot reach {self.settings.base_url} ({reason})."


def _snippet(value: Any) -> str:
    return json.dumps(value)[:200] if not isinstance(value, str) else value[:200]


def list_ollama_models(settings: LLMSettings) -> list[str]:
    """Names of models pulled into the Ollama server (``GET /api/tags``)."""
    url = f"{settings.base_url.rstrip('/')}/api/tags"
    try:
        with urllib.request.urlopen(url, timeout=10) as reply:
            data = json.loads(reply.read().decode("utf-8"))
    except (urllib.error.URLError, TimeoutError, socket.timeout) as exc:
        reason = getattr(exc, "reason", exc)
        raise LLMUnavailableError(OpenAICompatibleClient(settings)._unreachable_message(reason))
    return [m.get("name", "") for m in data.get("models", [])]


def check_model_available(settings: LLMSettings) -> None:
    """Fail fast with an actionable message if the configured model cannot be used.

    For Ollama this checks the server is running and the model has been pulled. For OpenAI it only
    checks configuration (no request is made, so no cost is incurred).
    """
    if settings.provider != "ollama":
        return
    names = list_ollama_models(settings)
    wanted = settings.model if ":" in settings.model else f"{settings.model}:latest"
    if wanted not in names:
        raise LLMUnavailableError(
            f"Ollama is running but model {settings.model!r} is not pulled. Run: ollama pull "
            f"{settings.model}  (available: {', '.join(names) or 'none'})"
        )
