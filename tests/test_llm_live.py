"""Live smoke tests against the configured real LLM (Ollama by default).

Skipped unless AIQL_RUN_LIVE_TESTS=1, so routine `pytest` runs stay offline, fast, and free:

    $env:AIQL_RUN_LIVE_TESTS = "1"; python -m pytest -m live_llm
"""

import os

import pytest

from ai_quality_lab.llm_agent import llm_chatbot_factory
from ai_quality_lab.llm_client import LLMSettings, check_model_available, load_env_file

pytestmark = [
    pytest.mark.live_llm,
    pytest.mark.skipif(
        os.environ.get("AIQL_RUN_LIVE_TESTS") != "1",
        reason="live LLM tests are opt-in: set AIQL_RUN_LIVE_TESTS=1",
    ),
]

ON = {"guardrails": {"cooking": {"enabled": True}}}


@pytest.fixture(scope="module")
def settings():
    load_env_file()
    settings = LLMSettings.from_env()
    check_model_available(settings)
    return settings


def test_real_model_answers_a_simple_question(settings):
    reply = llm_chatbot_factory(settings)(ON).respond("What is the capital of France?")
    assert reply.blocked is False
    assert "paris" in reply.response.lower()


def test_keyword_guardrail_still_blocks_before_the_model(settings):
    reply = llm_chatbot_factory(settings)(ON).respond("Give me a pasta recipe.")
    assert reply.blocked is True


def test_deepeval_metric_produces_a_score(settings):
    pytest.importorskip("deepeval")
    from ai_quality_lab.llm_dataset import parse_llm_dataset
    from ai_quality_lab.llm_eval import build_judge, deepeval_metric_factory, run_llm_case

    case = parse_llm_dataset({"schema_version": 1, "cases": [{
        "id": "LIVE-1", "name": "live", "category": "informational", "severity": "low",
        "input": "What is the capital of France?", "configuration": ON,
        "expected_behavior": "Says Paris.", "metrics": ["answer_relevancy"],
    }]})[0]
    judge = build_judge(LLMSettings.judge_from_env())
    result = run_llm_case(case, llm_chatbot_factory(settings), deepeval_metric_factory(judge))
    (metric,) = result.metrics
    assert metric.error is None, metric.error
    assert 0.0 <= metric.score <= 1.0
