"""Tests for observability records: building from reports, metrics, fingerprints, validation."""

import copy
import json
from pathlib import Path

import pytest

from ai_quality_lab.dataset import load_dataset
from ai_quality_lab.mock_agent import SimulatedFailure
from ai_quality_lab.observability.records import (
    RecordError,
    build_pytest,
    build_ragas_advisory,
    build_record,
    dataset_fingerprint,
    git_source,
    metric,
    rate,
    validate_record,
)
from ai_quality_lab.rag.evaluate import main as rag_main
from ai_quality_lab.runner import mock_chatbot_factory, run_evaluation_suite

ROOT = Path(__file__).resolve().parents[1]
GUARDRAIL = ROOT / "evals" / "guardrail_cases.json"
RAG_DATASET = ROOT / "evals" / "rag_cases.json"
KB = ROOT / "data" / "knowledge_base"
SOURCE = {"commit": "abc1234", "branch": "main", "origin": "local", "ci_run_id": None}


def engine_report(tmp_path, failure=SimulatedFailure.NONE, name="engine.json"):
    summary = run_evaluation_suite(load_dataset(GUARDRAIL), mock_chatbot_factory(failure))
    path = tmp_path / name
    path.write_text(json.dumps({**summary.to_dict(), "simulated_failure": failure.value}),
                    encoding="utf-8")
    return path


@pytest.fixture(scope="session")
def rag_reports(tmp_path_factory):
    """Real deterministic RAG reports (hashing embeddings, gullible mock model)."""
    folder = tmp_path_factory.mktemp("rag")
    reports = {}
    with pytest.MonkeyPatch.context() as mp:
        mp.chdir(folder)  # the index cache stays in tmp
        for name, extra in {"retrieval": ["--mode", "retrieval"],
                            "mock": ["--mode", "rag", "--llm", "mock-gullible"]}.items():
            out = folder / f"{name}.json"
            rag_main(["--embedder", "hashing", "--docs", str(KB), "--dataset", str(RAG_DATASET),
                      "--output", str(out), *extra])
            reports[name] = json.loads(out.read_text(encoding="utf-8"))
    return reports


def write(tmp_path, data, name="report.json"):
    path = tmp_path / name
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


def rag_record(tmp_path, report):
    return build_record("rag", write(tmp_path, report), dataset=RAG_DATASET, knowledge_base=KB,
                        source=SOURCE)


# --- Building from real reports -----------------------------------------------------------------


def test_custom_engine_record_keeps_numerators_and_denominators(tmp_path):
    record = build_record("custom_engine", engine_report(tmp_path), dataset=GUARDRAIL,
                          source=SOURCE)
    assert validate_record(record) == []
    m = record["metrics"]
    assert (m["pass_rate"]["value"], m["pass_rate"]["numerator"],
            m["pass_rate"]["denominator"]) == (1.0, 33, 33)
    assert (m["critical_failures"]["value"], m["critical_failures"]["denominator"]) == (0, 9)
    assert m["safety_failures"]["denominator"] == 16  # cases that must be blocked
    assert all(v["kind"] == "deterministic" for v in m.values())
    assert record["status"] == "PASS" and record["parameters"]["simulated_failure"] == "none"
    assert record["dataset"]["case_count"] == 33 and len(record["cases"]) == 33


def test_seeded_failure_is_recorded_not_hidden(tmp_path):
    path = engine_report(tmp_path, SimulatedFailure.GUARDRAIL_FAIL_OPEN)
    record = build_record("custom_engine", path, dataset=GUARDRAIL, source=SOURCE)
    assert record["status"] == "BLOCKED"
    assert record["parameters"]["simulated_failure"] == "guardrail_fail_open"
    assert record["metrics"]["critical_failures"]["value"] == 9
    assert record["metrics"]["safety_failures"]["value"] == 16
    # Same dataset and agent: comparable with a normal run (the seeded defect is a "change").
    normal = build_record("custom_engine", engine_report(tmp_path), dataset=GUARDRAIL,
                          source=SOURCE)
    assert record["comparability_key"] == normal["comparability_key"]


def test_old_report_without_simulated_failure_is_rejected(tmp_path):
    data = json.loads(engine_report(tmp_path).read_text(encoding="utf-8"))
    del data["simulated_failure"]
    with pytest.raises(RecordError, match="simulated_failure"):
        build_record("custom_engine", write(tmp_path, data), dataset=GUARDRAIL, source=SOURCE)


def test_report_from_another_dataset_is_rejected(tmp_path):
    data = json.loads(engine_report(tmp_path).read_text(encoding="utf-8"))
    data["results"] = data["results"][:-3]  # a stale report with fewer cases
    with pytest.raises(RecordError, match="do not match dataset"):
        build_record("custom_engine", write(tmp_path, data), dataset=GUARDRAIL, source=SOURCE)


@pytest.mark.parametrize("content", ["{oops", "[]", ""])
def test_unreadable_reports_are_errors(tmp_path, content):
    path = tmp_path / "bad.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(RecordError):
        build_record("custom_engine", path, dataset=GUARDRAIL, source=SOURCE)
    with pytest.raises(RecordError):
        build_record("custom_engine", tmp_path / "missing.json", dataset=GUARDRAIL, source=SOURCE)


def test_retrieval_only_run_marks_generation_metrics_unmeasured_not_zero(tmp_path, rag_reports):
    record = rag_record(tmp_path, rag_reports["retrieval"])
    assert record["evaluation_type"] == "rag_retrieval"
    for name in ("safety_failures", "answerable_abstention_rate", "over_abstention_rate"):
        assert name not in record["metrics"] and "no answers" in record["unmeasured"][name]
    assert record["metrics"]["doc_hit_rate"]["denominator"] == 19  # applicable questions
    assert record["identity"]["embedder"] == "hashing-512"
    assert record["identity"]["corpus_fingerprint"]


def test_mock_rag_run_measures_safety_and_abstention(tmp_path, rag_reports):
    record = rag_record(tmp_path, rag_reports["mock"])
    assert record["evaluation_type"] == "rag_mock" and record["deterministic"] is True
    m = record["metrics"]
    assert m["answerable_abstention_rate"]["denominator"] == 19
    assert m["safety_failures"]["value"] == 0 and m["safety_failures"]["denominator"] == 25


def test_live_rag_run_is_classified_from_its_configuration(tmp_path, rag_reports):
    report = copy.deepcopy(rag_reports["mock"])
    report["configuration"].update(llm="ollama:qwen2.5:3b", embedder="ollama:nomic-embed-text")
    record = rag_record(tmp_path, report)
    assert record["evaluation_type"] == "rag_live" and record["deterministic"] is False
    assert record["identity"]["llm"] == "ollama:qwen2.5:3b"


def test_over_abstention_is_distinguished_from_hedged_answers(tmp_path, rag_reports):
    report = copy.deepcopy(rag_reports["mock"])
    answerable = [r for r in report["results"] if r["expected_behavior"] == "answer"]
    for r in answerable:
        r["response"]["response"], r["outcome"], r["response"]["blocked"] = "Answer.", "PASS", False
    answerable[0]["response"]["response"] = "I could not find this information."
    answerable[0]["outcome"] = "FAIL"  # D-6.1 shape: abstained and failed
    answerable[1]["response"]["response"] = "I could not find it. However, the answer is X."
    answerable[2]["response"]["response"] = "I could not find this information."
    answerable[2]["response"]["blocked"] = True  # a guardrail block is a refusal, not abstention
    m = rag_record(tmp_path, report)["metrics"]
    assert (m["answerable_abstention_rate"]["numerator"],
            m["over_abstention_rate"]["numerator"]) == (2, 1)
    assert m["over_abstention_rate"]["denominator"] == 19


def test_rag_report_missing_configuration_field_is_rejected(tmp_path, rag_reports):
    report = copy.deepcopy(rag_reports["mock"])
    del report["configuration"]["embedder"]
    with pytest.raises(RecordError, match="embedder"):
        rag_record(tmp_path, report)


def test_pytest_record_statuses():
    audit = {"tests": 10, "passed": 8, "failures": 0, "errors": 0, "skipped": 2,
             "allowed_skips": 2, "unexpected_skips": []}
    assert build_pytest(audit)["status"] == "PASS"
    bad = {**audit, "allowed_skips": 1, "unexpected_skips": [{"test": "t", "reason": "r"}]}
    assert build_pytest(bad)["status"] == "BLOCKED"
    assert build_pytest({**audit, "tests": 0, "passed": 0, "skipped": 0,
                         "allowed_skips": 0})["status"] == "ERROR"


def test_ragas_metrics_are_advisory():
    parts = build_ragas_advisory({"judge": "ollama:x", "summary": {
        "faithfulness": {"evaluated": 4, "errors": 0, "mean": 0.5},
        "answer_relevancy": {"evaluated": 0, "errors": 0, "mean": None}}})
    assert parts["metrics"]["ragas_faithfulness"]["kind"] == "advisory"
    assert "ragas_answer_relevancy" in parts["unmeasured"]


# --- Missing versus zero, fingerprints, sources -------------------------------------------------


def test_zero_denominator_is_unmeasured_not_zero():
    metrics, unmeasured = {}, {}
    rate(0, 0, "over_abstention_rate", metrics, unmeasured)
    assert metrics == {} and "denominator is 0" in unmeasured["over_abstention_rate"]
    rate(0, 5, "x", metrics, unmeasured)
    assert metrics["x"]["value"] == 0.0 and metrics["x"]["denominator"] == 5


def test_dataset_fingerprint_ignores_line_endings_and_formatting(tmp_path):
    data = {"schema_version": 1, "cases": [{"id": "A", "x": [1, 2]}]}
    lf, crlf = tmp_path / "lf.json", tmp_path / "crlf.json"
    lf.write_bytes(json.dumps(data, indent=2).encode())
    crlf.write_bytes(json.dumps(data, indent=4).replace("\n", "\r\n").encode())
    assert dataset_fingerprint(lf) == dataset_fingerprint(crlf)
    changed = tmp_path / "changed.json"
    changed.write_text(json.dumps({**data, "cases": []}), encoding="utf-8")
    assert dataset_fingerprint(changed) != dataset_fingerprint(lf)


def test_record_paths_are_portable(tmp_path):
    record = build_record("custom_engine", engine_report(tmp_path), dataset=GUARDRAIL,
                          source=SOURCE)
    assert "\\" not in record["dataset"]["path"] and "\\" not in record["source"]["report_file"]


def test_github_actions_source(monkeypatch):
    for key, value in {"GITHUB_ACTIONS": "true", "GITHUB_SHA": "f00d", "GITHUB_REF_NAME": "main",
                       "GITHUB_RUN_ID": "77", "GITHUB_RUN_ATTEMPT": "2"}.items():
        monkeypatch.setenv(key, value)
    monkeypatch.delenv("GITHUB_HEAD_REF", raising=False)
    assert git_source() == {"commit": "f00d", "branch": "main", "origin": "github-actions",
                            "ci_run_id": "77-2"}


def test_recorded_at_is_utc(tmp_path):
    record = build_record("custom_engine", engine_report(tmp_path), dataset=GUARDRAIL,
                          source=SOURCE)
    assert record["recorded_at"].endswith("Z") and len(record["recorded_at"]) == 20


# --- Validation ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "mutate, message",
    [
        (lambda r: r.pop("run_id"), "missing field 'run_id'"),
        (lambda r: r.update(schema_version=2), "unsupported schema_version"),
        (lambda r: r.update(recorded_at="2026-11-01T10:00:00+01:00"), "not UTC"),
        (lambda r: r.update(recorded_at="2026-13-01T10:00:00Z"), "not a valid date"),
        (lambda r: r.update(status="OK"), "status must be"),
        (lambda r: r["metrics"].update(pass_rate=metric(1.0) | {"value": None}),
         "not a finite number"),
        (lambda r: r["metrics"].update(critical_failures=metric(3, numerator=3, denominator=2)),
         "numerator 3 >"),
        (lambda r: r["metrics"].update(pass_rate=metric(0.9, numerator=1, denominator=2)),
         "disagrees"),
        (lambda r: r["metrics"].update(pass_rate=metric(0.0, numerator=0, denominator=0)),
         "denominator 0"),
        (lambda r: r["unmeasured"].update(pass_rate="n/a"), "both measured and unmeasured"),
        (lambda r: r["identity"].update(dataset_fingerprint="other"), "comparability_key"),
        (lambda r: r["cases"].append(dict(r["cases"][0])), "duplicate case ids"),
        (lambda r: r["metrics"].update(x={"value": 1}), "fields must be"),
        (lambda r: r.update(evaluation_type="other"), "unknown evaluation_type"),
    ],
)
def test_invalid_records_are_rejected(make_record, mutate, message):
    record = make_record()
    mutate(record)
    assert any(message in e for e in validate_record(record)), validate_record(record)


def test_counts_with_zero_denominator_are_valid(make_record):
    record = make_record()
    record["metrics"]["medium_failures"] = metric(0, numerator=0, denominator=0)
    assert validate_record(record) == []


def test_non_object_record_is_rejected():
    assert validate_record([]) == ["record is not a JSON object"]


# --- Phase 8.1 remediation ----------------------------------------------------------------------
# D-8.1: a report's claimed status must agree with its own results.


def test_ready_report_with_a_critical_failure_is_refused(tmp_path):
    data = json.loads(engine_report(tmp_path).read_text(encoding="utf-8"))
    data["results"][0]["outcome"] = "FAIL"  # GR-001 is critical; release_readiness still READY
    with pytest.raises(RecordError, match="claims READY but .*1 critical failure"):
        build_record("custom_engine", write(tmp_path, data), dataset=GUARDRAIL, source=SOURCE)


def test_ready_report_with_an_errored_case_is_refused(tmp_path):
    data = json.loads(engine_report(tmp_path).read_text(encoding="utf-8"))
    medium = next(r for r in data["results"] if r["severity"] == "medium")
    medium["outcome"] = "ERROR"
    with pytest.raises(RecordError, match="claims READY but .*1 errored case"):
        build_record("custom_engine", write(tmp_path, data), dataset=GUARDRAIL, source=SOURCE)


def test_blocked_report_without_any_blocking_result_is_refused(tmp_path):
    data = json.loads(engine_report(tmp_path).read_text(encoding="utf-8"))
    data["release_readiness"] = "BLOCKED"
    with pytest.raises(RecordError, match="claims BLOCKED"):
        build_record("custom_engine", write(tmp_path, data), dataset=GUARDRAIL, source=SOURCE)


def test_consistent_ready_and_blocked_reports_are_recorded(tmp_path):
    ready = build_record("custom_engine", engine_report(tmp_path), dataset=GUARDRAIL,
                         source=SOURCE)
    blocked = build_record("custom_engine",
                           engine_report(tmp_path, SimulatedFailure.GUARDRAIL_FAIL_OPEN),
                           dataset=GUARDRAIL, source=SOURCE)
    assert (ready["status"], blocked["status"]) == ("PASS", "BLOCKED")


def test_ready_rag_report_with_a_failed_retrieval_gate_is_refused(tmp_path, rag_reports):
    report = copy.deepcopy(rag_reports["retrieval"])
    report["retrieval_metrics"]["document"]["recall"] = 0.5  # below the 0.8 gate
    with pytest.raises(RecordError, match="retrieval gate failed: document recall"):
        rag_record(tmp_path, report)


# D-8.2: pytest metrics are over executed tests.


def audit_of(tests, passed, failures=0, skipped=0, allowed=None, unexpected=0):
    allowed = skipped - unexpected if allowed is None else allowed
    return {"tests": tests, "passed": passed, "failures": failures, "errors": 0,
            "skipped": skipped, "allowed_skips": allowed,
            "unexpected_skips": [{"test": f"t{i}", "reason": "r"} for i in range(unexpected)]}


def test_pytest_metrics_exclude_skips_from_the_pass_rate():
    m = build_pytest(audit_of(703, 691, skipped=12))["metrics"]
    assert (m["test_count"]["value"], m["executed_tests"]["value"], m["passed_tests"]["value"],
            m["tests_failed"]["value"], m["expected_skips"]["value"],
            m["unexpected_skips"]["value"]) == (703, 691, 691, 0, 12, 0)
    rate_ = m["executed_pass_rate"]
    assert (rate_["value"], rate_["numerator"], rate_["denominator"]) == (1.0, 691, 691)
    assert "test_pass_rate" not in m  # the legacy definition (skips in the denominator) is gone


def test_zero_executed_tests_is_unmeasured_and_error():
    parts = build_pytest(audit_of(12, 0, skipped=12))
    assert parts["status"] == "ERROR"
    assert "executed_pass_rate" in parts["unmeasured"]
    assert "executed_pass_rate" not in parts["metrics"]


def test_inconsistent_skip_audit_is_refused():
    with pytest.raises(RecordError, match="internally inconsistent"):
        build_pytest(audit_of(10, 9, skipped=2))  # 9 passed + 2 skipped > 10 tests
    with pytest.raises(RecordError, match="non-negative integers"):
        build_pytest({**audit_of(10, 10), "tests": "10"})


# D-8.3: UTF-8 byte-order mark.


def test_bom_encoded_report_is_recorded(tmp_path):
    plain = engine_report(tmp_path)
    bom = tmp_path / "bom.json"
    bom.write_bytes(b"\xef\xbb\xbf" + plain.read_bytes())
    with_bom = build_record("custom_engine", bom, dataset=GUARDRAIL, source=SOURCE)
    without = build_record("custom_engine", plain, dataset=GUARDRAIL, source=SOURCE)
    assert with_bom["status"] == "PASS" and with_bom["metrics"] == without["metrics"]
    assert with_bom["source"]["report_sha256"] == without["source"]["report_sha256"]


@pytest.mark.parametrize("payload", [
    "﻿{not json".encode("utf-8"),                       # BOM + malformed JSON
    json.dumps({"total": 1}).encode("utf-16"),               # wrong encoding
    b"\xff\xfe\x00garbage\x80",                              # invalid UTF-8 bytes
])
def test_malformed_or_wrongly_encoded_reports_still_fail(tmp_path, payload):
    path = tmp_path / "bad.json"
    path.write_bytes(payload)
    with pytest.raises(RecordError, match="missing or unreadable"):
        build_record("custom_engine", path, dataset=GUARDRAIL, source=SOURCE)


# D-8.5: metric units come from the catalog, not from the value's Python type.


@pytest.mark.parametrize(
    "name, value, num, den, kind, message",
    [
        ("pass_rate", 1, 1, 4, "deterministic", "disagrees with 1/4"),  # integer rate (P8)
        ("pass_rate", 0.75, 3, None, "deterministic", "needs both numerator and denominator"),
        ("pass_rate", 0.75, None, 4, "deterministic", "needs both numerator and denominator"),
        ("pass_rate", 0.0, 0, 0, "deterministic", "denominator 0"),
        ("over_abstention_rate", 1.0, 5, 4, "deterministic", "numerator 5 > denominator 4"),
        ("critical_failures", 1.0, 1, 9, "deterministic", "must be a non-negative integer"),
        ("critical_failures", 2, 1, 9, "deterministic", "disagrees with numerator 1"),
        ("doc_recall", 0.9, 17, 19, "deterministic", "a score has no numerator"),
        ("doc_recall", 0.9, None, None, "deterministic", "number of scored items"),
        ("doc_recall", 1.2, None, 19, "deterministic", "outside [0, 1]"),
        ("ragas_faithfulness", 0.5, None, 4, "deterministic", "kind must be 'advisory'"),
        ("pass_rate", 1.0, 4, 4, "advisory", "kind must be 'deterministic'"),
        ("made_up_metric", 1, None, None, "deterministic", "unknown metric name"),
    ],
)
def test_metric_units_are_validated(make_record, name, value, num, den, kind, message):
    from ai_quality_lab.observability.records import MetricKind

    record = make_record()
    record["metrics"][name] = metric(value, MetricKind(kind), num, den)
    assert any(message in e for e in validate_record(record)), validate_record(record)


@pytest.mark.parametrize(
    "name, value, num, den, kind",
    [("critical_failures", 0, 0, 9, "deterministic"),        # integer count
     ("test_count", 703, None, None, "deterministic"),        # count without a denominator
     ("medium_failures", 0, 0, 0, "deterministic"),           # 0 of 0 is a valid count
     ("pass_rate", 0.75, 3, 4, "deterministic"),              # floating-point rate
     ("pass_rate", 1, 4, 4, "deterministic"),                 # integer value, correct ratio
     ("doc_mrr", 0.8684, None, 19, "deterministic"),          # score
     ("ragas_context_recall", 1.0, None, 4, "advisory"),
     ("test_pass_rate", 0.9829, 689, 701, "deterministic")],  # legacy, in existing baselines
)
def test_valid_metrics_by_unit(make_record, name, value, num, den, kind):
    from ai_quality_lab.observability.records import MetricKind

    record = make_record()
    record["metrics"][name] = metric(value, MetricKind(kind), num, den)
    assert validate_record(record) == []
