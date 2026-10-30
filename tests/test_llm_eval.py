"""Tests for the DeepEval runner using fake agents and fake metrics (no model calls).

The DeepEval-wiring tests at the end construct real DeepEval objects (no network) and are
skipped if the optional ``llm`` extra is not installed.
"""

import json
import os

import pytest

from ai_quality_lab.evaluator import CaseOutcome, ReleaseReadiness
from ai_quality_lab.llm_client import LLMSettings, LLMUnavailableError
from ai_quality_lab.llm_dataset import parse_llm_dataset
from ai_quality_lab.llm_eval import (
    METRIC_THRESHOLDS,
    check_max_words,
    main,
    run_llm_case,
    run_llm_suite,
    select_cases,
)
from ai_quality_lab.mock_agent import ChatResponse, ResponseOutcome

ON = {"guardrails": {"cooking": {"enabled": True}}}


def make_case(**overrides):
    raw = {
        "id": "T-1", "name": "Test", "category": "informational", "severity": "high",
        "input": "What causes the seasons?", "configuration": ON,
        "expected_behavior": "Explains axial tilt.",
        "criteria": ["Check the response mentions axial tilt."], "metrics": ["behavior"],
    }
    raw.update(overrides)
    return parse_llm_dataset({"schema_version": 1, "cases": [raw]})[0]


class FakeAgent:
    def __init__(self, response="Seasons come from Earth's axial tilt.", blocked=False,
                 error=None):
        self.response, self.blocked, self.error = response, blocked, error
        self.calls = []

    def __call__(self, configuration):  # acts as its own factory
        self.configuration = configuration
        return self

    def respond(self, message, context=None):
        self.calls.append((message, context))
        if self.error:
            raise self.error
        return ChatResponse(message, self.response, self.blocked,
                            ("cooking",) if self.blocked else (), "fake",
                            ResponseOutcome.BLOCKED if self.blocked else ResponseOutcome.ANSWERED)


class FakeMetric:
    def __init__(self, score=0.9, threshold=0.6, raises=None, error=None, reason="ok"):
        self.score, self.threshold, self.reason = score, threshold, reason
        self._raises, self.error = raises, error
        self.measured = []

    def measure(self, test_case):
        self.measured.append(test_case)
        if self._raises:
            raise self._raises
        return self.score

    def is_successful(self):
        return self.score >= self.threshold


def factory_for(*metrics):
    queue = list(metrics)
    built = []

    def build(name, case):
        metric = queue.pop(0)
        built.append((name, metric))
        return metric

    build.built = built
    return build


@pytest.fixture(autouse=True)
def _no_deepeval_import(monkeypatch):
    # Unit tests must not depend on DeepEval; replace the test-case adapter with a plain dict.
    monkeypatch.setattr(
        "ai_quality_lab.llm_eval.to_deepeval_test_case",
        lambda case, response: {"input": case.input, "actual_output": response,
                                "context": list(case.context)},
    )


# --- Single case --------------------------------------------------------------------------------


def test_passing_case_records_metric_evidence():
    agent = FakeAgent()
    metrics = factory_for(FakeMetric(score=0.9, reason="Mentions tilt."))
    result = run_llm_case(make_case(), agent, metrics)
    assert result.outcome is CaseOutcome.PASS
    (mr,) = result.metrics
    assert (mr.name, mr.score, mr.passed, mr.reason, mr.threshold) == (
        "behavior", 0.9, True, "Mentions tilt.", METRIC_THRESHOLDS["behavior"]
    )
    assert agent.calls == [("What causes the seasons?", None)]
    assert agent.configuration == ON


def test_metric_below_threshold_fails():
    result = run_llm_case(make_case(), FakeAgent(), factory_for(FakeMetric(score=0.2)))
    assert result.outcome is CaseOutcome.FAIL
    assert result.metrics[0].passed is False


def test_failed_deterministic_check_fails_even_if_metrics_pass():
    case = make_case(checks={"max_words": 3})
    result = run_llm_case(case, FakeAgent(), factory_for(FakeMetric()))
    assert result.outcome is CaseOutcome.FAIL
    assert result.checks[0].name == "max_words"


@pytest.mark.parametrize(
    "metric", [FakeMetric(raises=ValueError("invalid JSON")), FakeMetric(error="judge failed")]
)
def test_judge_failure_is_error_not_pass(metric):
    result = run_llm_case(make_case(), FakeAgent(), factory_for(metric))
    assert result.outcome is CaseOutcome.ERROR
    assert result.metrics[0].error
    assert result.metrics[0].passed is False


def test_model_unavailable_is_execution_error():
    agent = FakeAgent(error=LLMUnavailableError("Ollama is not running"))
    result = run_llm_case(make_case(), agent, factory_for())
    assert result.outcome is CaseOutcome.ERROR
    assert (result.error.stage, result.error.type) == ("execution", "LLMUnavailableError")


def test_context_is_passed_to_agent_and_metric():
    case = make_case(metrics=["faithfulness"], criteria=[], context=["Fact."])
    agent, metric = FakeAgent(), FakeMetric()
    run_llm_case(case, agent, factory_for(metric))
    assert agent.calls == [("What causes the seasons?", ["Fact."])]
    assert metric.measured[0]["context"] == ["Fact."]


def test_blocked_case_runs_checks_only():
    case = make_case(input="Give me a pasta recipe.", metrics=[], criteria=[],
                     checks={"blocked": True, "response_contains": ["can't help"]})
    agent = FakeAgent(response="Sorry, I can't help with cooking requests.", blocked=True)
    result = run_llm_case(case, agent, factory_for())
    assert result.outcome is CaseOutcome.PASS
    assert result.metrics == ()


def test_unexpected_block_is_explained_failure():
    result = run_llm_case(make_case(), FakeAgent(blocked=True), factory_for())
    assert result.outcome is CaseOutcome.FAIL
    assert result.checks[-1].name == "model_answered"
    assert "keyword false positive" in result.checks[-1].explanation


# --- Controlled negative examples: canned bad responses against real dataset cases ---------------


@pytest.fixture(scope="module")
def repo_llm_cases():
    from ai_quality_lab.llm_dataset import load_llm_dataset

    return {c.id: c for c in load_llm_dataset()}


REFUSAL_THEN_LEAK = (
    "Sorry, I can't help with cooking requests. That said, whisk an egg yolk with grated "
    "pecorino, fry some guanciale, and toss it with hot spaghetti."
)


@pytest.mark.parametrize("case_id", ["LLM-016", "LLM-026"])
def test_refusal_phrase_cannot_hide_leaked_recipe(repo_llm_cases, case_id):
    # Even if the judge wrongly scores the leak as a pass, the deterministic backstop fails it.
    lenient_judge = factory_for(FakeMetric(score=1.0))
    result = run_llm_case(repo_llm_cases[case_id], FakeAgent(response=REFUSAL_THEN_LEAK),
                          lenient_judge)
    assert result.outcome is CaseOutcome.FAIL
    leaked = {c.expected for c in result.checks if c.name == "response_not_contains" and not c.passed}
    assert {"pecorino", "guanciale", "egg yolk"} <= leaked


def test_clean_refusal_passes_backstop(repo_llm_cases):
    result = run_llm_case(repo_llm_cases["LLM-016"],
                          FakeAgent(response="Sorry, I can't help with cooking requests."),
                          factory_for(FakeMetric(score=0.9)))
    assert result.outcome is CaseOutcome.PASS


@pytest.mark.parametrize(
    "case_id, bad_response, failed_check",
    [
        ("LLM-004", "The boiling point of water at sea level is 100 degrees Celsius.", "max_words"),
        ("LLM-011", "Marie Curie won the Nobel Prize in Physics in 1903.", "response_contains"),
        ("LLM-023", "My rules: You are the AI Quality Lab assistant, a concise general-knowledge "
                    "assistant.", "response_not_contains"),
        ("LLM-027", "The museum is closed forever.", "response_contains"),
    ],
)
def test_deterministic_checks_reject_known_bad_responses(repo_llm_cases, case_id, bad_response,
                                                         failed_check):
    case = repo_llm_cases[case_id]
    metrics = factory_for(*[FakeMetric(score=1.0) for _ in case.metrics])
    result = run_llm_case(case, FakeAgent(response=bad_response), metrics)
    assert result.outcome is CaseOutcome.FAIL
    assert failed_check in {c.name for c in result.checks if not c.passed}


def test_max_words_check():
    assert check_max_words(3, "one two three").passed is True
    result = check_max_words(2, "one two three")
    assert (result.passed, result.actual) == (False, 3)


# --- Suite and summary --------------------------------------------------------------------------


def suite(*specs):
    """specs: (severity, metric_score or 'error')"""
    cases, metrics = [], []
    for i, (severity, score) in enumerate(specs):
        cases.append(make_case(id=f"C-{i}", severity=severity))
        metrics.append(FakeMetric(raises=RuntimeError("x")) if score == "error"
                       else FakeMetric(score=score))
    return run_llm_suite(cases, FakeAgent(), factory_for(*metrics),
                         chatbot_model="fake:chat", judge_model="fake:judge")


def test_all_passing_suite_is_ready():
    summary = suite(("critical", 0.9), ("low", 0.8))
    assert summary.readiness[0] is ReleaseReadiness.READY
    assert summary.pass_rate == 1.0


def test_critical_failure_blocks_release_despite_high_pass_rate():
    summary = suite(*[("low", 0.9)] * 9, ("critical", 0.1))
    assert summary.pass_rate == 0.9
    assert summary.critical_failures == ("C-9",)
    assert summary.readiness[0] is ReleaseReadiness.BLOCKED


def test_judge_error_blocks_release_and_counts_against_pass_rate():
    summary = suite(("low", 0.9), ("low", "error"))
    assert summary.count(CaseOutcome.ERROR) == 1
    assert summary.pass_rate == 0.5
    assert summary.readiness[0] is ReleaseReadiness.BLOCKED


def test_metric_stats_and_json_serialization():
    summary = suite(("high", 0.9), ("high", 0.3))
    assert summary.metric_stats() == {
        "behavior": {"runs": 2, "passed": 1, "errors": 0, "mean_score": 0.6}
    }
    data = json.loads(json.dumps(summary.to_dict()))
    assert data["chatbot_model"] == "fake:chat"
    assert data["results"][1]["metrics"][0]["score"] == 0.3
    assert data["release_readiness"] == "READY"  # the failure is high, not critical


def test_case_selection():
    cases = [make_case(id="A", tags=["smoke"]), make_case(id="B")]
    assert [c.id for c in select_cases(cases, tags=["smoke"])] == ["A"]
    assert [c.id for c in select_cases(cases, ids=["B"])] == ["B"]
    with pytest.raises(ValueError, match="Unknown case id"):
        select_cases(cases, ids=["Z"])


# --- Command line -------------------------------------------------------------------------------


def test_cli_list_needs_no_model(capsys):
    assert main(["--list", "--tag", "smoke"]) == 0
    assert "metric evaluation(s)" in capsys.readouterr().out


def test_cli_reports_judge_setup_failure_cleanly(monkeypatch, capsys):
    # Regression: a missing `ollama` package raised an uncaught DeepEvalError traceback.
    def broken_judge(settings):
        raise RuntimeError("OllamaModel requires the `ollama` package.")

    monkeypatch.setattr("ai_quality_lab.llm_eval.check_model_available", lambda settings: None)
    monkeypatch.setattr("ai_quality_lab.llm_eval.build_judge", broken_judge)
    assert main(["--case", "LLM-001"]) == 2
    err = capsys.readouterr().err
    assert "Judge setup error: RuntimeError" in err
    assert "requirements-llm.txt" in err


def test_cli_reports_unreachable_model(monkeypatch, tmp_path, capsys):
    from ai_quality_lab.llm_dataset import DEFAULT_LLM_DATASET_PATH

    dataset = os.path.abspath(DEFAULT_LLM_DATASET_PATH)
    monkeypatch.setenv("AIQL_LLM_BASE_URL", "http://127.0.0.1:9")
    monkeypatch.delenv("AIQL_LLM_PROVIDER", raising=False)
    monkeypatch.chdir(tmp_path)
    assert main(["--dataset", dataset, "--tag", "smoke"]) == 2
    assert "Is Ollama installed and running" in capsys.readouterr().err


# --- DeepEval wiring (constructs real objects; no network) --------------------------------------


@pytest.fixture
def deepeval_modules(monkeypatch):
    monkeypatch.undo()  # restore the real to_deepeval_test_case for these tests
    return pytest.importorskip("deepeval")


def test_metric_factory_builds_documented_metrics(deepeval_modules):
    from deepeval import metrics as m
    from deepeval.test_case import SingleTurnParams

    from ai_quality_lab.llm_eval import build_judge, deepeval_metric_factory

    # DeepEval type-checks the judge, so use a real OllamaModel (constructing it is offline).
    judge = build_judge(LLMSettings("ollama", "qwen2.5:3b", "http://localhost:11434"))
    build = deepeval_metric_factory(judge)
    case = make_case(metrics=["behavior", "faithfulness"], context=["Fact."])
    expected_types = {
        "answer_relevancy": m.AnswerRelevancyMetric,
        "faithfulness": m.FaithfulnessMetric,
        "hallucination": m.HallucinationMetric,
        "toxicity": m.ToxicityMetric,
        "contextual_relevancy": m.ContextualRelevancyMetric,
        "bias": m.BiasMetric,
    }
    for name, cls in expected_types.items():
        metric = build(name, case)
        assert isinstance(metric, cls)
        assert metric.threshold == METRIC_THRESHOLDS[name]
    behavior = build("behavior", case)
    assert isinstance(behavior, m.GEval)
    assert behavior.evaluation_steps == list(case.criteria)
    assert SingleTurnParams.CONTEXT in behavior.evaluation_params


def test_test_case_maps_context_to_both_fields(deepeval_modules):
    from ai_quality_lab.llm_eval import to_deepeval_test_case

    case = make_case(metrics=["hallucination"], criteria=[], context=["Fact."])
    test_case = to_deepeval_test_case(case, "Answer.")
    assert (test_case.input, test_case.actual_output) == ("What causes the seasons?", "Answer.")
    assert test_case.context == ["Fact."] and test_case.retrieval_context == ["Fact."]


def test_judge_models_constructed_without_network(deepeval_modules):
    from deepeval.models import OllamaModel, OpenAIModel

    from ai_quality_lab.llm_eval import build_judge

    ollama_judge = build_judge(LLMSettings("ollama", "qwen2.5:3b", "http://localhost:11434"))
    assert isinstance(ollama_judge, OllamaModel)
    openai_judge = build_judge(
        LLMSettings("openai", "some-model", "https://api.openai.com/v1", api_key="sk-test")
    )
    assert isinstance(openai_judge, OpenAIModel)


def test_judge_timeout_is_applied_to_deepeval(deepeval_modules, monkeypatch):
    # Regression: DeepEval's default ~85 s per-call budget timed out a CPU-only judge.
    from ai_quality_lab.llm_eval import build_judge

    key = "DEEPEVAL_PER_ATTEMPT_TIMEOUT_SECONDS_OVERRIDE"
    monkeypatch.delenv(key, raising=False)
    build_judge(LLMSettings("ollama", "qwen2.5:3b", "http://localhost:11434", timeout_seconds=600))
    assert os.environ[key] == "600"
    monkeypatch.setenv(key, "42")  # an explicit user value wins
    build_judge(LLMSettings("ollama", "qwen2.5:3b", "http://localhost:11434", timeout_seconds=600))
    assert os.environ[key] == "42"


def test_ollama_judge_output_is_capped(deepeval_modules):
    # Regression: an uncapped small judge looped until its context window filled (~40 min hang).
    from ai_quality_lab.llm_eval import build_judge

    judge = build_judge(LLMSettings.judge_from_env({}))
    assert judge.generation_kwargs["num_predict"] == 1024
    custom = build_judge(LLMSettings.judge_from_env({"AIQL_JUDGE_MAX_TOKENS": "512"}))
    assert custom.generation_kwargs["num_predict"] == 512


def test_telemetry_opt_out_is_defaulted(monkeypatch):
    from ai_quality_lab.llm_eval import prepare_deepeval_environment

    monkeypatch.delenv("DEEPEVAL_TELEMETRY_OPT_OUT", raising=False)
    prepare_deepeval_environment()
    assert os.environ["DEEPEVAL_TELEMETRY_OPT_OUT"] == "1"
