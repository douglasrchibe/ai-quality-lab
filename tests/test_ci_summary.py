"""Tests for the CI skip audit, job summary, and quality gate."""

import json
from pathlib import Path

import pytest

from ai_quality_lab.ci_summary import (
    NeedsError,
    audit_skips,
    build_summary,
    main,
    overall_outcome,
    parse_needs,
)

ROOT = Path(__file__).resolve().parents[1]
JUNIT = """<?xml version="1.0" encoding="utf-8"?>
<testsuites><testsuite name="pytest" tests="5" failures="1" errors="0" skipped="2">
  <testcase classname="tests.test_a" name="test_ok"/>
  <testcase classname="tests.test_a" name="test_bad"><failure message="boom"/></testcase>
  <testcase classname="tests.test_llm_live" name="test_live"><skipped message="opt-in"/></testcase>
  {extra}
</testsuite></testsuites>"""


def write_junit(tmp_path, extra=""):
    path = tmp_path / "junit.xml"
    path.write_text(JUNIT.format(extra=extra), encoding="utf-8")
    return path


# --- Skip audit ---------------------------------------------------------------------------------


def test_live_test_skips_are_allowed(tmp_path):
    audit = audit_skips(write_junit(tmp_path))
    assert audit["allowed_skips"] == 1 and audit["unexpected_skips"] == []
    assert (audit["tests"], audit["failures"], audit["passed"]) == (5, 1, 2)


def test_other_skips_are_flagged(tmp_path):
    extra = ('<testcase classname="tests.test_llm_eval" name="test_wiring">'
             '<skipped message="could not import deepeval"/></testcase>')
    audit = audit_skips(write_junit(tmp_path, extra))
    assert audit["unexpected_skips"] == [
        {"test": "tests.test_llm_eval::test_wiring", "reason": "could not import deepeval"}]


def test_skips_cli_exit_codes(tmp_path):
    out = tmp_path / "out"
    assert main(["skips", "--junit", str(write_junit(tmp_path)), "--out", str(out)]) == 0
    assert json.loads((out / "skip-audit.json").read_text(encoding="utf-8"))["allowed_skips"] == 1
    bad = write_junit(tmp_path, '<testcase classname="tests.x" name="t"><skipped/></testcase>')
    assert main(["skips", "--junit", str(bad), "--out", str(out)]) == 1
    assert main(["skips", "--junit", str(tmp_path / "missing.xml"), "--out", str(out)]) == 2


# --- Gate ---------------------------------------------------------------------------------------


def status(name, outcome, expected="PASS", job="custom-engine"):
    return {"name": name, "outcome": outcome, "expected": expected,
            "expectation_met": outcome == expected, "exit_code": 0, "job": job}


ALL_OK = {"python-tests": "success", "custom-engine": "success"}


def test_gate_passes_only_when_everything_met_expectations():
    assert overall_outcome(ALL_OK, [status("a", "PASS"), status("demo", "BLOCKED", "BLOCKED")]) \
        == ("PASS", [])


def test_blocked_quality_check_blocks():
    outcome, reasons = overall_outcome({**ALL_OK, "custom-engine": "failure"},
                                       [status("custom-engine", "BLOCKED")])
    assert outcome == "BLOCKED" and "expected PASS, got BLOCKED" in reasons[0]


def test_demo_that_stopped_blocking_blocks():
    outcome, _ = overall_outcome({**ALL_OK, "custom-engine": "failure"},
                                 [status("demo", "PASS", expected="BLOCKED")])
    assert outcome == "BLOCKED"


@pytest.mark.parametrize(
    "jobs, statuses",
    [
        ({**ALL_OK, "custom-engine": "failure"}, [status("x", "ERROR")]),  # could not run
        ({**ALL_OK, "python-tests": "failure"}, []),  # failed before any check (e.g. install)
        ({**ALL_OK, "python-tests": "cancelled"}, []),
        ({"custom-engine": "failure", "python-tests": "failure"},
         [status("x", "BLOCKED", job="custom-engine")]),  # one blocked, one unexplained
        ({}, []),  # nothing at all
    ],
)
def test_execution_problems_are_errors_not_quality_failures(jobs, statuses):
    assert overall_outcome(jobs, statuses)[0] == "ERROR"


def test_parse_needs():
    needs = json.dumps({"a": {"result": "success", "outputs": {}}, "b": {"result": "failure"}})
    assert parse_needs(needs) == {"a": "success", "b": "failure"}


@pytest.mark.parametrize("raw", ["", "  ", "not json", "{}", '{"a": "success"}', "[1]"])
def test_unreadable_job_results_are_rejected_not_guessed(raw):
    with pytest.raises(NeedsError):
        parse_needs(raw)


def test_gate_without_job_results_is_an_error_even_if_checks_passed(artifacts, capsys):
    # Regression: unreadable needs JSON used to become {} and the gate could report PASS while a
    # job that failed before writing a status (e.g. a failed install) went unnoticed.
    assert main(["gate", "--artifacts", str(artifacts), "--needs-json", "garbled"]) == 2
    assert "ERROR" in capsys.readouterr().out


def test_gate_reads_needs_from_file(artifacts, tmp_path):
    path = tmp_path / "needs.json"
    path.write_text(json.dumps({"custom-engine": {"result": "success"}}), encoding="utf-8")
    assert main(["gate", "--artifacts", str(artifacts), "--needs-file", str(path)]) == 0


def test_summary_marks_missing_job_results_as_error(artifacts, tmp_path):
    output = tmp_path / "s.md"
    assert main(["summary", "--artifacts", str(artifacts), "--output", str(output),
                 "--needs-json", ""]) == 0  # the summary itself never fails the job
    text = output.read_text(encoding="utf-8")
    assert "⚠️ ERROR" in text and "job results unavailable" in text


# --- Summary ------------------------------------------------------------------------------------


@pytest.fixture
def artifacts(tmp_path):
    root = tmp_path / "downloaded"
    (root / "ci-custom-engine").mkdir(parents=True)
    (root / "ci-custom-engine" / "custom-engine.status.json").write_text(
        json.dumps(status("custom-engine", "PASS")), encoding="utf-8")
    (root / "ci-custom-engine" / "custom-engine-report.json").write_text(
        json.dumps({"total": 33, "passed": 33, "release_readiness": "READY"}), encoding="utf-8")
    (root / "ci-rag-deterministic").mkdir()
    (root / "ci-rag-deterministic" / "rag-mock.json").write_text(json.dumps({
        "total": 25, "passed": 17, "critical_failures": [], "release_readiness": "READY",
        "configuration": {"top_k": 4},
        "retrieval_metrics": {"document": {"hit_rate": 0.95, "recall": 0.92}},
        "release_gates": {"document_hit_rate": {"passed": True}},
        "failure_classifications": {"generation_failure": 5, "retrieval_failure": 0}}),
        encoding="utf-8")
    (root / "ci-promptfoo").mkdir()
    (root / "ci-promptfoo" / "promptfoo-results.json").write_text("{corrupt", encoding="utf-8")
    return root


def test_summary_reports_actual_values_and_unavailable_reports(artifacts):
    text = build_summary(artifacts, {"custom-engine": "success"},
                         ROOT / "ci" / "known_defects.json")
    assert "AI Quality Lab CI: ✅ PASS" in text
    assert "33/33 passed, release readiness READY" in text
    assert "17/25 passed" in text and "generation_failure" in text
    assert "**pytest:** report unavailable" in text
    assert "**Promptfoo:** report unavailable" in text  # corrupt JSON is not guessed at


def test_summary_always_lists_open_known_defects(artifacts):
    text = build_summary(artifacts, {}, ROOT / "ci" / "known_defects.json")
    assert "D-6.1" in text
    for case in ("RAG-001", "RAG-009", "RAG-010", "RAG-015", "RAG-023", "RAG-025"):
        assert case in text
    assert "not proof that the AI system is safe" in text


def test_summary_survives_missing_known_defects_file(artifacts, tmp_path):
    text = build_summary(artifacts, {}, tmp_path / "missing.json")
    assert "known defects file unavailable" in text


def test_gate_cli_exit_codes(artifacts):
    ok = json.dumps({"custom-engine": {"result": "success"}})
    bad = json.dumps({"custom-engine": {"result": "failure"}})
    assert main(["gate", "--artifacts", str(artifacts), "--needs-json", ok]) == 0
    assert main(["gate", "--artifacts", str(artifacts), "--needs-json", bad]) == 2  # unexplained


def test_known_defects_file_is_complete():
    data = json.loads((ROOT / "ci" / "known_defects.json").read_text(encoding="utf-8"))
    by_id = {d["id"]: d for d in data["defects"]}
    assert by_id["D-6.1"]["status"] == "open"
    assert by_id["D-6.1"]["cases"] == ["RAG-001", "RAG-009", "RAG-010", "RAG-015", "RAG-023",
                                       "RAG-025"]
    for defect in data["defects"]:
        assert {"id", "title", "severity", "status", "cases", "last_observed",
                "ci_visibility"} <= set(defect)


# --- Malformed artifacts never crash the summary or turn into a pass ----------------------------


def test_summary_survives_malformed_reports(artifacts, tmp_path):
    (artifacts / "ci-python-tests").mkdir()
    (artifacts / "ci-python-tests" / "skip-audit.json").write_text('{"skipped": 0}',
                                                                   encoding="utf-8")
    (artifacts / "ci-custom-engine" / "custom-engine-report.json").write_text("[1]",
                                                                              encoding="utf-8")
    defects = tmp_path / "defects.json"
    defects.write_text('{"defects": [{"id": "D-x", "status": "open"}]}', encoding="utf-8")
    text = build_summary(artifacts, {"custom-engine": "success"}, defects)
    assert "**pytest:** report unreadable (KeyError)" in text
    assert "**Custom engine:** report unreadable (TypeError)" in text
    assert "known defects file unavailable or unreadable" in text
    output = tmp_path / "summary.md"
    needs = json.dumps({"custom-engine": {"result": "success"}})
    assert main(["summary", "--artifacts", str(artifacts), "--output", str(output),
                 "--needs-json", needs]) == 0


def test_status_file_that_is_not_an_object_is_an_error(artifacts):
    (artifacts / "ci-custom-engine" / "odd.status.json").write_text("[]", encoding="utf-8")
    needs = json.dumps({"custom-engine": {"result": "success"}})
    assert main(["gate", "--artifacts", str(artifacts), "--needs-json", needs]) == 2


def test_report_problem_is_shown_as_the_reason():
    bad = {**status("demo", "ERROR", "BLOCKED"), "report_problem": "report has errored cases"}
    outcome, reasons = overall_outcome({"custom-engine": "failure"}, [bad])
    assert outcome == "ERROR" and "report has errored cases" in reasons[0]


def test_summary_shows_regression_result(artifacts):
    (artifacts / "ci-observability").mkdir()
    (artifacts / "ci-observability" / "regression.json").write_text(json.dumps({
        "status": "BLOCKED", "problems": [],
        "comparisons": [{"evaluation_type": "custom_engine", "status": "BLOCKED"}]}),
        encoding="utf-8")
    text = build_summary(artifacts, {"custom-engine": "success"},
                         ROOT / "ci" / "known_defects.json")
    assert "**Regression vs baseline:** BLOCKED against the approved baselines " \
           "(custom_engine BLOCKED)" in text


def test_summary_without_regression_report_says_unavailable(artifacts):
    text = build_summary(artifacts, {"custom-engine": "success"},
                         ROOT / "ci" / "known_defects.json")
    assert "**Regression vs baseline:** report unavailable" in text
    assert "D-6.4" in text  # every open known defect is listed, including ones without cases
