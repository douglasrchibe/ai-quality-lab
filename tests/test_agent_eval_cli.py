"""The agent evaluation CLI: full and partial runs, exit codes, the JSON report, the trace view,
simulated-failure labelling, and errors."""

import json

import pytest

from ai_quality_lab.agent_eval import evaluate
from ai_quality_lab.agent_eval.evaluate import main, new_run_id


def run(capsys, tmp_path, *args):
    out = tmp_path / "report.json"
    code = main(["--run-id", "cli-test", "--output", str(out), *args])
    report = json.loads(out.read_text(encoding="utf-8")) if out.exists() else None
    return code, capsys.readouterr(), report


def test_the_full_suite_is_ready_and_writes_a_complete_report(capsys, tmp_path):
    code, output, report = run(capsys, tmp_path)
    assert code == 0
    assert "28 PASS, 0 FAIL, 0 ERROR" in output.out and "Release readiness: READY" in output.out
    assert (report["report"], report["run_id"], report["scope"], report["planner"]) == (
        "agent_evaluation", "cli-test", "full", "reference")
    assert len(report["dataset_fingerprint"]) == 16 and len(report["contract_fingerprint"]) == 16
    assert (report["defined"], report["executed"], report["passed"]) == (28, 28, 28)
    assert [r["id"] for r in report["results"]] == [f"AGT-{i:03d}" for i in range(1, 29)]
    first = report["results"][0]
    assert first["run_id"] == "cli-test:AGT-001" and first["assertions"]
    assert {"expected_final_status", "run_status", "severity", "oracle", "failed_assertions"} \
        <= set(first)


def test_a_single_scenario_is_a_partial_run_without_readiness(capsys, tmp_path):
    code, output, report = run(capsys, tmp_path, "--case", "AGT-016")
    assert code == 0 and report["scope"] == "partial" and report["release_readiness"] is None
    assert "no decision (partial run)" in output.out


def test_a_failing_partial_run_exits_one(capsys, tmp_path):
    code, _, report = run(capsys, tmp_path, "--case", "AGT-003", "--simulate-failure",
                          "ignore_approval")
    assert code == 1 and report["failed"] == 1 and report["release_readiness"] is None


def test_a_simulated_failure_run_is_blocked_and_labelled(capsys, tmp_path):
    code, output, report = run(capsys, tmp_path, "--simulate-failure", "follow_injection")
    assert code == 1 and report["release_readiness"] == "BLOCKED"
    assert report["planner"] == "simulated_failure:follow_injection"
    assert report["healthy_agent_result"] is False
    assert "demonstrates detection only" in output.out


def test_trace_prints_one_scenarios_steps_after_the_summary(capsys, tmp_path):
    code, output, _ = run(capsys, tmp_path, "--trace", "AGT-019")
    assert code == 0
    summary, trace = output.out.split("Trace AGT-019", 1)
    assert "Release readiness: READY" in summary
    assert "create_ticket" in trace and "retry #2" in trace and "replayed" in trace


@pytest.mark.parametrize("args", [["--case", "AGT-999"], ["--trace", "AGT-999"],
                                  ["--case", "AGT-001", "--trace", "AGT-002"]])
def test_bad_selections_are_errors(capsys, tmp_path, args):
    code, output, report = run(capsys, tmp_path, *args)
    assert code == 2 and report is None and "ERROR" in output.err


def test_an_unreadable_dataset_is_an_error(capsys, tmp_path):
    code, output, _ = run(capsys, tmp_path, "--dataset", str(tmp_path / "missing.json"))
    assert code == 2 and "cannot read" in output.err


def test_a_case_error_exits_two_and_a_crash_is_never_blocked(capsys, tmp_path, monkeypatch):
    monkeypatch.setattr(evaluate, "run_agent", lambda *a, **k: 1 / 0)
    code, output, report = run(capsys, tmp_path, "--case", "AGT-001")
    assert code == 2 and report["errored"] == 1
    monkeypatch.setattr(evaluate, "run_suite", lambda *a, **k: (_ for _ in ()).throw(KeyError()))
    code, output, _ = run(capsys, tmp_path)
    assert code == 2 and "not a quality verdict" in output.err


def test_run_ids_are_unique_per_call_and_well_formed():
    from datetime import datetime, timezone

    first = new_run_id(datetime(2026, 11, 3, 12, 0, 0, 1, tzinfo=timezone.utc))
    second = new_run_id(datetime(2026, 11, 3, 12, 0, 0, 2, tzinfo=timezone.utc))
    assert first == "agent-20261103T120000000001Z" and first != second
