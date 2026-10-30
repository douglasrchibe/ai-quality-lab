"""Tests for comparing the custom engine with Promptfoo results files."""

import json

import pytest

from ai_quality_lab.dataset import load_dataset
from ai_quality_lab.engine_comparison import (
    PromptfooResultsError,
    compare_engines,
    load_promptfoo_results,
    main,
)
from ai_quality_lab.evaluator import CaseOutcome
from ai_quality_lab.mock_agent import SimulatedFailure
from ai_quality_lab.runner import mock_chatbot_factory, run_evaluation_suite


def row(case_id, outcome="PASS", failed_metrics=()):
    """A minimal Promptfoo result row, shaped like Promptfoo 0.125.1 `eval -o` output."""
    components = [{"pass": True, "assertion": {"type": "is-json", "metric": "structured_output"}}]
    components += [{"pass": False, "assertion": {"type": "javascript", "metric": m}}
                   for m in failed_metrics]
    base = {"testCase": {"metadata": {"case_id": case_id}},
            "gradingResult": {"componentResults": components}}
    if outcome == "PASS":
        return {**base, "success": True, "failureReason": 0}
    if outcome == "FAIL":
        # Promptfoo fills "error" with the first assertion message even for plain failures.
        return {**base, "success": False, "failureReason": 1, "error": "Expected output to ..."}
    return {"testCase": base["testCase"], "success": False, "failureReason": 2,
            "error": "ValueError: boom", "response": {"error": "ValueError: boom"}}


def write_results(tmp_path, rows, simulated_failure=None):
    provider = {"id": "file://provider.py"}
    if simulated_failure:
        provider["config"] = {"simulated_failure": simulated_failure}
    document = {"results": {"results": rows}, "config": {"providers": [provider]}}
    path = tmp_path / "results.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


# --- Parsing ------------------------------------------------------------------------------------


def test_outcomes_parsed_from_failure_reason(tmp_path):
    run = load_promptfoo_results(
        write_results(tmp_path, [row("A"), row("B", "FAIL", ["blocked_status"]), row("C", "ERROR")])
    )
    assert run.results["A"].outcome is CaseOutcome.PASS
    assert run.results["B"].outcome is CaseOutcome.FAIL
    assert run.results["B"].failed_checks == ("blocked_status",)
    assert run.results["C"].outcome is CaseOutcome.ERROR
    assert run.results["C"].error == "ValueError: boom"


def test_assertion_failure_with_error_text_is_fail_not_error(tmp_path):
    # Regression: Promptfoo sets "error" on plain assertion failures (failureReason 1).
    run = load_promptfoo_results(write_results(tmp_path, [row("A", "FAIL", ["blocked_status"])]))
    assert run.results["A"].outcome is CaseOutcome.FAIL


@pytest.mark.parametrize(
    "simulated_failure, expected",
    [(None, SimulatedFailure.NONE), ("guardrail_fail_open", SimulatedFailure.GUARDRAIL_FAIL_OPEN)],
)
def test_simulated_failure_mode_read_from_results(tmp_path, simulated_failure, expected):
    path = write_results(tmp_path, [row("A")], simulated_failure)
    assert load_promptfoo_results(path).simulated_failure is expected


def test_rows_without_case_id_rejected(tmp_path):
    bad = {"testCase": {"metadata": {}}, "success": True}
    with pytest.raises(PromptfooResultsError, match="no 'case_id' metadata"):
        load_promptfoo_results(write_results(tmp_path, [bad]))


def test_duplicate_case_ids_rejected(tmp_path):
    with pytest.raises(PromptfooResultsError, match="more than once"):
        load_promptfoo_results(write_results(tmp_path, [row("A"), row("A")]))


@pytest.mark.parametrize("content", ["not json", '{"results": []}', "[]"])
def test_non_promptfoo_files_rejected(tmp_path, content):
    path = tmp_path / "x.json"
    path.write_text(content, encoding="utf-8")
    with pytest.raises(PromptfooResultsError):
        load_promptfoo_results(path)


# --- Comparison ---------------------------------------------------------------------------------


@pytest.fixture
def custom_summary(repo_dataset_path):
    return run_evaluation_suite(load_dataset(repo_dataset_path))


def mirror_rows(summary, flip=None, drop=None):
    rows = []
    for result in summary.results:
        if result.case.id == drop:
            continue
        outcome = result.outcome.value
        if result.case.id == flip:
            outcome = "FAIL" if outcome == "PASS" else "PASS"
        rows.append(row(result.case.id, outcome))
    return rows


def test_matching_outcomes_agree(tmp_path, custom_summary):
    run = load_promptfoo_results(write_results(tmp_path, mirror_rows(custom_summary)))
    comparison = compare_engines(custom_summary, run)
    assert comparison.disagreements == ()
    assert len(comparison.rows) == custom_summary.total


def test_different_outcome_is_a_disagreement(tmp_path, custom_summary):
    rows = mirror_rows(custom_summary, flip="GR-001")
    run = load_promptfoo_results(write_results(tmp_path, rows))
    (row_,) = compare_engines(custom_summary, run).disagreements
    assert (row_.case_id, row_.custom, row_.promptfoo) == (
        "GR-001",
        CaseOutcome.PASS,
        CaseOutcome.FAIL,
    )


def test_case_missing_from_promptfoo_is_a_disagreement(tmp_path, custom_summary):
    rows = mirror_rows(custom_summary, drop="GR-002")
    run = load_promptfoo_results(write_results(tmp_path, rows))
    (row_,) = compare_engines(custom_summary, run).disagreements
    assert (row_.case_id, row_.promptfoo) == ("GR-002", None)


def test_extra_promptfoo_case_is_a_disagreement(tmp_path, custom_summary):
    rows = mirror_rows(custom_summary) + [row("EXTRA-1")]
    run = load_promptfoo_results(write_results(tmp_path, rows))
    (row_,) = compare_engines(custom_summary, run).disagreements
    assert (row_.case_id, row_.custom) == ("EXTRA-1", None)


# --- Command line -------------------------------------------------------------------------------


def test_cli_exits_zero_when_engines_agree(tmp_path, custom_summary, capsys):
    path = write_results(tmp_path, mirror_rows(custom_summary))
    assert main([str(path)]) == 0
    assert "Disagreements: 0" in capsys.readouterr().out


def test_cli_uses_simulated_failure_mode_from_results(tmp_path, repo_dataset_path, capsys):
    fail_open = run_evaluation_suite(
        load_dataset(repo_dataset_path),
        mock_chatbot_factory(SimulatedFailure.GUARDRAIL_FAIL_OPEN),
    )
    path = write_results(tmp_path, mirror_rows(fail_open), "guardrail_fail_open")
    assert main([str(path)]) == 0
    out = capsys.readouterr().out
    assert "Simulated failure mode: guardrail_fail_open" in out
    assert "release readiness BLOCKED" in out


def test_cli_exits_one_on_disagreement(tmp_path, custom_summary, capsys):
    path = write_results(tmp_path, mirror_rows(custom_summary, flip="GR-003"))
    assert main([str(path)]) == 1
    assert "Disagreements: 1" in capsys.readouterr().out


def test_cli_exits_two_on_bad_input(tmp_path, capsys):
    assert main([str(tmp_path / "missing.json")]) == 2
    assert "Input error" in capsys.readouterr().err
