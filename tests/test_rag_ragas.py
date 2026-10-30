"""Tests for the advisory Ragas runner using fake metrics (Ragas itself lives in .venv-ragas)."""

import asyncio
import json

import pytest

from ai_quality_lab.rag import ragas_eval
from ai_quality_lab.rag.ragas_eval import (
    CALIBRATION,
    METRIC_INPUTS,
    applicable_metrics,
    run_calibration,
    score_metric,
    score_results,
    summarize,
    verdict,
)


class FakeResult:
    def __init__(self, value):
        self.value = value


class FakeMetric:
    def __init__(self, value=None, error=None, delay=0.0, by_label=None):
        self.value, self.error, self.delay, self.by_label = value, error, delay, by_label
        self.calls = []

    async def ascore(self, **inputs):
        self.calls.append(inputs)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.error:
            raise self.error
        if self.by_label is not None:
            return FakeResult(self.by_label(inputs))
        return FakeResult(self.value)


def run(coro):
    return asyncio.run(coro)


def test_score_metric_returns_value():
    assert run(score_metric(FakeMetric(0.87654), {}, 5)) == {"value": 0.8765, "error": None}


@pytest.mark.parametrize(
    "metric, fragment",
    [(FakeMetric(float("nan")), "NaN"), (FakeMetric(error=ValueError("bad json")), "bad json"),
     (FakeMetric(None), "TypeError"), (FakeMetric(1.0, delay=0.5), "TimeoutError")],
)
def test_uncomputable_metric_is_an_error_not_a_pass(metric, fragment):
    scored = run(score_metric(metric, {}, 0.1))
    assert scored["value"] is None and fragment in scored["error"]
    assert verdict("faithfulness", scored) == "ERROR"


def test_verdict_uses_advisory_thresholds():
    assert verdict("faithfulness", {"value": 0.7, "error": None}) == "PASS"
    assert verdict("faithfulness", {"value": 0.69, "error": None}) == "FAIL"


def test_calibration_covers_every_metric_with_good_and_bad_examples():
    for name in METRIC_INPUTS:
        expectations = {e["should_pass"] for e in CALIBRATION if e["metric"] == name}
        assert expectations == {True, False}, name
    for example in CALIBRATION:
        assert set(example["inputs"]) == set(METRIC_INPUTS[example["metric"]])


def perfect_judge():
    should = {json.dumps(e["inputs"], sort_keys=True): e["should_pass"] for e in CALIBRATION}
    return FakeMetric(by_label=lambda i: 1.0 if should[json.dumps(i, sort_keys=True)] else 0.0)


def test_calibration_trusts_a_discriminating_judge():
    metrics = {name: perfect_judge() for name in METRIC_INPUTS}
    result = run(run_calibration(metrics, 5))
    assert result["status"] == "TRUSTED" and result["correct"] == len(CALIBRATION)


def test_calibration_rejects_a_judge_that_passes_everything():
    metrics = {name: FakeMetric(1.0) for name in METRIC_INPUTS}
    result = run(run_calibration(metrics, 5))
    assert result["status"] == "UNTRUSTED"
    assert set(result["per_metric"].values()) == {"UNTRUSTED"}


def test_calibration_counts_errors_as_incorrect():
    metrics = {name: perfect_judge() for name in METRIC_INPUTS}
    metrics["faithfulness"] = FakeMetric(error=RuntimeError("judge down"))
    result = run(run_calibration(metrics, 5))
    assert result["per_metric"]["faithfulness"] == "UNTRUSTED"
    assert result["per_metric"]["context_recall"] == "TRUSTED"


def rag_result(**overrides):
    base = {"id": "RAG-001", "expected_behavior": "answer", "outcome": "PASS",
            "response": {"blocked": False},
            "ragas_inputs": {"user_input": "Q", "response": "A", "retrieved_contexts": ["C"],
                             "reference": "R", "reference_contexts": ["R"]}}
    base.update(overrides)
    return base


@pytest.mark.parametrize(
    "overrides, expected",
    [({}, list(METRIC_INPUTS)),
     ({"expected_behavior": "refuse"}, []),
     ({"response": {"blocked": True}}, []),
     ({"ragas_inputs": {"user_input": "Q", "response": None, "retrieved_contexts": []}}, []),
     ({"ragas_inputs": {"user_input": "Q", "response": "A", "retrieved_contexts": [],
                        "reference": "R"}}, ["answer_relevancy"])],
)
def test_applicable_metrics(overrides, expected):
    names, _ = applicable_metrics(rag_result(**overrides))
    assert names == expected


def test_scoring_passes_only_required_inputs_and_summarizes():
    metrics = {name: FakeMetric(0.9) for name in METRIC_INPUTS}
    metrics["context_recall"] = FakeMetric(error=RuntimeError("x"))
    rows = run(score_results([rag_result()], metrics, 5))
    assert set(metrics["answer_relevancy"].calls[0]) == {"user_input", "response"}
    assert set(metrics["context_precision"].calls[0]) == {"user_input", "reference",
                                                          "retrieved_contexts"}
    summary = summarize(rows)
    assert summary["faithfulness"] == {"evaluated": 1, "errors": 0, "below_threshold": 0,
                                       "mean": 0.9}
    assert summary["context_recall"]["errors"] == 1 and summary["context_recall"]["mean"] is None


def test_telemetry_opt_out(monkeypatch):
    monkeypatch.delenv("RAGAS_DO_NOT_TRACK", raising=False)
    ragas_eval.prepare_ragas_environment()
    import os

    assert os.environ["RAGAS_DO_NOT_TRACK"] == "true"


def test_cli_without_ragas_installed_exits_two(monkeypatch, tmp_path, capsys):
    pytest.importorskip("openai")
    try:
        import ragas  # noqa: F401
        pytest.skip("ragas is installed in this environment")
    except ImportError:
        pass
    results = tmp_path / "r.json"
    results.write_text(json.dumps({"mode": "rag", "results": []}), encoding="utf-8")
    monkeypatch.setattr(ragas_eval, "check_model_available", lambda settings: None)
    monkeypatch.chdir(tmp_path)
    assert ragas_eval.main(["--results", str(results)]) == 2
    assert ".venv-ragas" in capsys.readouterr().err


def test_cli_rejects_retrieval_only_results(monkeypatch, tmp_path, capsys):
    results = tmp_path / "r.json"
    results.write_text(json.dumps({"mode": "retrieval", "results": []}), encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    assert ragas_eval.main(["--results", str(results)]) == 2
    assert "--mode rag" in capsys.readouterr().err
