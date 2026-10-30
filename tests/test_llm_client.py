"""Tests for LLM settings, .env loading, and the OpenAI-compatible HTTP client.

HTTP behaviour is tested against a throwaway local server, so no real provider is contacted.
"""

import json
import threading
import time
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from ai_quality_lab.llm_client import (
    DEFAULT_OLLAMA_HOST,
    DEFAULT_OLLAMA_MODEL,
    LLMConfigurationError,
    LLMResponseError,
    LLMSettings,
    LLMUnavailableError,
    OpenAICompatibleClient,
    check_model_available,
    load_env_file,
)

# --- Settings -----------------------------------------------------------------------------------


def test_defaults_to_local_ollama():
    settings = LLMSettings.from_env({})
    assert (settings.provider, settings.model, settings.base_url) == (
        "ollama", DEFAULT_OLLAMA_MODEL, DEFAULT_OLLAMA_HOST
    )
    assert settings.api_key is None
    assert settings.chat_completions_url == "http://localhost:11434/v1/chat/completions"


@pytest.mark.parametrize(
    "base_url", ["http://localhost:11434/v1", "http://localhost:11434/v1/", "http://localhost:11434/"]
)
def test_ollama_base_url_accepts_openai_compatible_form(base_url):
    # Regression: ".../v1" used to become ".../v1/v1/chat/completions" and 404.
    settings = LLMSettings.from_env({"AIQL_LLM_BASE_URL": base_url})
    assert settings.base_url == "http://localhost:11434"
    assert settings.chat_completions_url == "http://localhost:11434/v1/chat/completions"


def test_openai_requires_model_and_key():
    with pytest.raises(LLMConfigurationError, match="AIQL_LLM_MODEL is required"):
        LLMSettings.from_env({"AIQL_LLM_PROVIDER": "openai"})
    with pytest.raises(LLMConfigurationError, match="OPENAI_API_KEY is not set"):
        LLMSettings.from_env({"AIQL_LLM_PROVIDER": "openai", "AIQL_LLM_MODEL": "m"})


def test_openai_settings_and_key_never_in_repr():
    settings = LLMSettings.from_env(
        {"AIQL_LLM_PROVIDER": "openai", "AIQL_LLM_MODEL": "m", "OPENAI_API_KEY": "sk-secret"}
    )
    assert settings.chat_completions_url == "https://api.openai.com/v1/chat/completions"
    assert settings.api_key == "sk-secret"
    assert "sk-secret" not in repr(settings)


@pytest.mark.parametrize(
    "env, fragment",
    [
        ({"AIQL_LLM_PROVIDER": "gemini"}, "must be one of"),
        ({"AIQL_LLM_TIMEOUT": "soon"}, "must be a number"),
        ({"AIQL_LLM_MAX_TOKENS": "0"}, "greater than 0"),
    ],
)
def test_invalid_settings_rejected(env, fragment):
    with pytest.raises(LLMConfigurationError, match=fragment):
        LLMSettings.from_env(env)


def test_judge_defaults_to_chatbot_model_but_has_its_own_timeout():
    env = {"AIQL_LLM_MODEL": "llama3.2:3b", "AIQL_LLM_TIMEOUT": "60"}
    judge = LLMSettings.judge_from_env(env)
    # Judge prompts are much longer than chat prompts, so the chat timeout is not inherited.
    assert (judge.provider, judge.model, judge.timeout_seconds) == ("ollama", "llama3.2:3b", 600)
    assert LLMSettings.judge_from_env({**env, "AIQL_JUDGE_TIMEOUT": "900"}).timeout_seconds == 900


def test_judge_overrides_and_does_not_inherit_across_providers():
    env = {
        "AIQL_LLM_MODEL": "qwen2.5:3b",
        "AIQL_JUDGE_PROVIDER": "openai",
        "AIQL_JUDGE_MODEL": "judge-model",
        "OPENAI_API_KEY": "k",
    }
    judge = LLMSettings.judge_from_env(env)
    assert (judge.provider, judge.model, judge.base_url) == (
        "openai", "judge-model", "https://api.openai.com/v1"
    )
    with pytest.raises(LLMConfigurationError, match="MODEL is required"):
        LLMSettings.judge_from_env({"AIQL_LLM_MODEL": "x", "AIQL_JUDGE_PROVIDER": "openai",
                                    "OPENAI_API_KEY": "k"})


# --- .env loading -------------------------------------------------------------------------------


def test_load_env_file_parses_and_never_overrides(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text(
        "# comment\n\nAIQL_LLM_MODEL=from-file\nexport A=\"quoted value\"\nB='single'\n"
        "EXISTING=file\nnot a pair\n",
        encoding="utf-8",
    )
    environ = {"EXISTING": "shell"}
    loaded = load_env_file(env_file, environ)
    assert loaded == ["AIQL_LLM_MODEL", "A", "B"]
    assert environ == {"EXISTING": "shell", "AIQL_LLM_MODEL": "from-file", "A": "quoted value",
                       "B": "single"}


def test_missing_env_file_is_not_an_error(tmp_path):
    assert load_env_file(tmp_path / "absent.env", {}) == []


# --- HTTP client --------------------------------------------------------------------------------


@contextmanager
def fake_server(status=200, body=None, raw=None, routes=None, delay=0.0):
    """Serve canned responses; records each request as (path, headers, json body)."""
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def _reply(self, code, payload):
            data = payload.encode() if isinstance(payload, str) else json.dumps(payload).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):  # noqa: N802
            requests.append((self.path, dict(self.headers), None))
            self._reply(200, (routes or {}).get(self.path, {}))

        def do_POST(self):  # noqa: N802
            length = int(self.headers["Content-Length"])
            requests.append((self.path, dict(self.headers), json.loads(self.rfile.read(length))))
            time.sleep(delay)
            try:
                self._reply(status, raw if raw is not None else body)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client gave up (timeout test)

        def log_message(self, *args):
            pass

    server = HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_port}", requests
    finally:
        server.shutdown()
        server.server_close()


def ollama(url, model="qwen2.5:3b", timeout=5):
    return LLMSettings("ollama", model, url, timeout_seconds=timeout)


CHAT_OK = {"choices": [{"message": {"role": "assistant", "content": "  Paris.  "}}]}
MESSAGES = [{"role": "user", "content": "Capital of France?"}]


def test_successful_completion_and_request_shape():
    with fake_server(body=CHAT_OK) as (url, requests):
        assert OpenAICompatibleClient(ollama(url)).complete(MESSAGES) == "Paris."
    (path, headers, payload), = requests
    assert path == "/v1/chat/completions"
    assert "Authorization" not in headers
    assert payload == {"model": "qwen2.5:3b", "messages": MESSAGES, "temperature": 0, "seed": 42,
                       "max_tokens": 300, "stream": False}


def test_api_key_sent_as_bearer_token_for_openai():
    with fake_server(body=CHAT_OK) as (url, requests):
        settings = LLMSettings("openai", "m", f"{url}/v1", api_key="sk-test")
        OpenAICompatibleClient(settings).complete(MESSAGES)
    (path, headers, _), = requests
    assert path == "/v1/chat/completions"
    assert headers["Authorization"] == "Bearer sk-test"


@pytest.mark.parametrize(
    "status, error, fragment",
    [
        (401, LLMConfigurationError, "authentication failed"),
        (404, LLMUnavailableError, "ollama pull qwen2.5:3b"),
        (429, LLMUnavailableError, "rate limited"),
        (500, LLMResponseError, "HTTP 500"),
    ],
)
def test_http_errors_are_mapped(status, error, fragment):
    with fake_server(status=status, body={"error": "x"}) as (url, _):
        with pytest.raises(error, match=fragment):
            OpenAICompatibleClient(ollama(url)).complete(MESSAGES)


@pytest.mark.parametrize("raw", ["not json", json.dumps({"choices": []})])
def test_unusable_bodies_raise_response_error(raw):
    with fake_server(raw=raw) as (url, _):
        with pytest.raises(LLMResponseError):
            OpenAICompatibleClient(ollama(url)).complete(MESSAGES)


def test_slow_model_times_out_with_actionable_message():
    with fake_server(body=CHAT_OK, delay=1.5) as (url, _):
        settings = LLMSettings("ollama", "qwen2.5:3b", url, timeout_seconds=0.5)
        with pytest.raises(LLMUnavailableError, match="did not answer within 0.5s"):
            OpenAICompatibleClient(settings).complete(MESSAGES)


def test_unreachable_ollama_explains_how_to_fix():
    settings = ollama("http://127.0.0.1:9", timeout=3)  # nothing listens on port 9
    with pytest.raises(LLMUnavailableError, match="Is Ollama installed and running"):
        OpenAICompatibleClient(settings).complete(MESSAGES)


def test_model_availability_check():
    tags = {"/api/tags": {"models": [{"name": "qwen2.5:3b"}, {"name": "llama3.2:latest"}]}}
    with fake_server(routes=tags) as (url, _):
        check_model_available(ollama(url))
        check_model_available(ollama(url, model="llama3.2"))  # implicit :latest
        with pytest.raises(LLMUnavailableError, match="ollama pull phi3"):
            check_model_available(ollama(url, model="phi3"))


def test_model_availability_check_makes_no_request_for_openai():
    check_model_available(LLMSettings("openai", "m", "http://127.0.0.1:9", api_key="k"))
