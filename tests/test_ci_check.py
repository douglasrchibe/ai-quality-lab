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
