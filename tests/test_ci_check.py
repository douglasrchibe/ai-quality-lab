"""Tests for the CI check wrapper: exit-code mapping, expectations, and recorded artifacts."""

import json
import sys

import pytest

from ai_quality_lab.ci_check import classify, main, run_check, wrapper_exit_code


@pytest.mark.parametrize(
    "codes, exit_code, outcome",
    [
        ("project", 0, "PASS"), ("project", 1, "BLOCKED"), ("project", 2, "ERROR"),
        ("pytest", 0, "PASS"), ("pytest", 1, "BLOCKED"), ("pytest", 4, "ERROR"),
        ("pytest", 5, "ERROR"),  # "no tests collected" must never look like a pass
        ("promptfoo", 0, "PASS"), ("promptfoo", 100, "BLOCKED"), ("promptfoo", 1, "ERROR"),
        ("pip", 0, "PASS"), ("pip", 1, "BLOCKED"),
        ("project", None, "ERROR"),  # command could not start
    ],
)
def test_exit_codes_map_to_outcomes(codes, exit_code, outcome):
    assert classify(exit_code, codes) == outcome


@pytest.mark.parametrize(
    "outcome, expected, code",
    [("PASS", "PASS", 0), ("BLOCKED", "BLOCKED", 0), ("BLOCKED", "PASS", 1),
     ("PASS", "BLOCKED", 1), ("ERROR", "PASS", 2), ("ERROR", "BLOCKED", 2)],
)
def test_wrapper_exit_codes(outcome, expected, code):
    assert wrapper_exit_code(outcome, expected) == code


def py(code: str) -> list[str]:
    return [sys.executable, "-c", code]


def test_run_check_records_status_log_and_stdout(tmp_path):
    status = run_check("demo", py("print('hello'); import sys; sys.exit(1)"), "project",
                       "blocked", tmp_path, stdout_file=tmp_path / "out.txt")
    observed = (status["exit_code"], status["outcome"], status["expectation_met"])
    assert observed == (1, "BLOCKED", True)
    saved = json.loads((tmp_path / "demo.status.json").read_text(encoding="utf-8"))
    assert saved == status
    assert "hello" in (tmp_path / "demo.log").read_text(encoding="utf-8")
    assert (tmp_path / "out.txt").read_text(encoding="utf-8").strip() == "hello"


def test_failure_demo_that_stops_blocking_fails_the_check(tmp_path):
    # A deliberate failure demonstration that suddenly passes means detection is broken.
    code = main(["--name", "demo", "--codes", "project", "--expect", "blocked",
                 "--out", str(tmp_path), "--", *py("pass")])
    assert code == 1


def test_quality_failure_and_execution_error_are_distinguished(tmp_path):
    blocked = main(["--name", "a", "--codes", "project", "--out", str(tmp_path), "--",
                    *py("import sys; sys.exit(1)")])
    error = main(["--name", "b", "--codes", "project", "--out", str(tmp_path), "--",
                  *py("import sys; sys.exit(2)")])
    assert (blocked, error) == (1, 2)


def test_missing_command_is_an_error_with_a_status_file(tmp_path):
    code = main(["--name", "ghost", "--codes", "project", "--out", str(tmp_path), "--",
                 "definitely-not-a-real-command-aiql"])
    assert code == 2
    status = json.loads((tmp_path / "ghost.status.json").read_text(encoding="utf-8"))
    assert status["outcome"] == "ERROR" and status["exit_code"] is None


def test_usage_errors(tmp_path):
    assert main(["--name", "x", "--codes", "project", "--out", str(tmp_path)]) == 2
    assert main(["--name", "x", "--codes", "project", "--out", str(tmp_path), "--"]) == 2


# --- --report: the exit code must agree with the tool's own JSON report -------------------------


def write_report(tmp_path, **fields):
    report = tmp_path / "report.json"
    report.write_text(json.dumps({"total": 33, "errored": 0, **fields}), encoding="utf-8")
    return report


def demo(tmp_path, report, exit_code=1, expect="blocked"):
    return main(["--name", "demo", "--codes", "project", "--expect", expect, "--out",
                 str(tmp_path / "out"), "--report", str(report), "--",
                 *py(f"import sys; sys.exit({exit_code})")])


def test_crashing_demo_is_an_error_not_an_expected_block(tmp_path):
    # A traceback exits 1 too; without a report it would look like "blocked as expected".
    code = main(["--name", "demo", "--codes", "project", "--expect", "blocked", "--out",
                 str(tmp_path), "--report", str(tmp_path / "never-written.json"), "--",
                 *py("raise RuntimeError('simulated crash')")])
    assert code == 2
    status = json.loads((tmp_path / "demo.status.json").read_text(encoding="utf-8"))
    assert status["outcome"] == "ERROR" and "missing or unreadable" in status["report_problem"]


@pytest.mark.parametrize(
    "fields, problem",
    [({"errored": 33, "release_readiness": "BLOCKED"}, "errored cases"),  # blocked by errors
     ({"total": 0, "release_readiness": "BLOCKED"}, "no executed cases"),
     ({"release_readiness": "READY"}, "release_readiness is 'READY'")],
)
def test_demo_blocked_for_the_wrong_reason_is_an_error(tmp_path, fields, problem):
    assert demo(tmp_path, write_report(tmp_path, **fields)) == 2
    status = json.loads((tmp_path / "out" / "demo.status.json").read_text(encoding="utf-8"))
    assert problem in status["report_problem"]


def test_demo_blocked_by_detected_failures_meets_expectation(tmp_path):
    assert demo(tmp_path, write_report(tmp_path, failed=16, release_readiness="BLOCKED")) == 0


def test_pass_must_agree_with_report(tmp_path):
    assert demo(tmp_path, write_report(tmp_path, release_readiness="READY"), 0, "pass") == 0
    assert demo(tmp_path, write_report(tmp_path, release_readiness="BLOCKED"), 0, "pass") == 2


def test_report_that_is_not_an_object_is_an_error(tmp_path):
    report = tmp_path / "report.json"
    report.write_text("[]", encoding="utf-8")
    assert demo(tmp_path, report) == 2


def test_report_requires_project_codes(tmp_path):
    assert main(["--name", "x", "--codes", "pytest", "--out", str(tmp_path), "--report",
                 str(tmp_path / "r.json"), "--", *py("pass")]) == 2


def test_real_fail_open_demo_is_confirmed_by_its_report(tmp_path):
    report = tmp_path / "fail-open.json"
    code = main(["--name", "fail-open", "--codes", "project", "--expect", "blocked", "--out",
                 str(tmp_path), "--stdout-file", str(report), "--report", str(report), "--",
                 sys.executable, "-m", "ai_quality_lab.runner", "--json",
                 "--simulate-failure", "guardrail_fail_open"])
    assert code == 0
    status = json.loads((tmp_path / "fail-open.status.json").read_text(encoding="utf-8"))
    assert status["report_problem"] is None
