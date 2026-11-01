"""Unit and integration tests for ai_quality_lab.runner."""

import json
from dataclasses import dataclass

import pytest

from ai_quality_lab.dataset import DatasetValidationError, Severity, load_dataset
from ai_quality_lab.evaluator import CaseOutcome, ReleaseReadiness
from ai_quality_lab.mock_agent import SimulatedFailure
from ai_quality_lab.runner import (
    AgentContractError,
    main,
    mock_chatbot_factory,
    run_case,
    run_dataset,
    run_evaluation_suite,
)


@dataclass
class FakeReply:
    response: str = "Sorry, I can't help with cooking requests."
    blocked: bool = True
    detected_topics: tuple = ("cooking",)


class SpyAgent:
    """Records what the runner sends; returns a fixed reply."""

    def __init__(self, configuration, reply=None):
        self.configuration = configuration
        self.messages = []
        self._reply = reply or FakeReply()

    def respond(self, message):
        self.messages.append(message)
        return self._reply


class ExplodingAgent:
    def respond(self, message):
        raise RuntimeError("agent crashed")


class CrashOnInput:
    """Delegates to a real agent but raises for one specific message."""

    def __init__(self, agent, trigger):
        self._agent = agent
        self._trigger = trigger

    def respond(self, message):
        if message == self._trigger:
            raise RuntimeError("agent crashed")
        return self._agent.respond(message)


# --- Basic execution ----------------------------------------------------------------------------


def test_passing_scenario_produces_pass(make_case):
    result = run_case(make_case(), mock_chatbot_factory())
    assert result.outcome is CaseOutcome.PASS
    assert result.error is None
    assert all(a.passed for a in result.assertions)
    assert result.duration_ms is not None and result.duration_ms >= 0


def test_failed_assertion_produces_fail(make_case):
    case = make_case(configuration={"guardrails": {"cooking": {"enabled": False}}})
    result = run_case(case, mock_chatbot_factory())
    assert result.outcome is CaseOutcome.FAIL
    assert [a.name for a in result.failed_assertions] == ["blocked"]
    assert result.observed is not None and result.observed.blocked is False


def test_exception_during_setup_produces_error(make_case):
    def broken_factory(configuration):
        raise ConnectionError("backend unavailable")

    result = run_case(make_case(), broken_factory)
    assert result.outcome is CaseOutcome.ERROR
    assert (result.error.stage, result.error.type) == ("setup", "ConnectionError")
    assert result.error.message == "backend unavailable"
    assert result.assertions == ()
    assert result.observed is None


def test_exception_during_execution_produces_error(make_case):
    result = run_case(make_case(), lambda config: ExplodingAgent())
    assert result.outcome is CaseOutcome.ERROR
    assert (result.error.stage, result.error.type) == ("execution", "RuntimeError")


@pytest.mark.parametrize(
    "bad_reply",
    [
        FakeReply(response=None),
        FakeReply(blocked="yes"),
        FakeReply(detected_topics="cooking"),
        FakeReply(detected_topics=[1]),
        object(),
    ],
)
def test_reply_violating_agent_contract_produces_error(make_case, bad_reply):
    result = run_case(make_case(), lambda config: SpyAgent(config, bad_reply))
    assert result.outcome is CaseOutcome.ERROR
    assert result.error.type == AgentContractError.__name__


def test_failures_and_errors_do_not_stop_the_suite(make_case):
    cases = [
        make_case(id="A"),
        make_case(id="B", configuration={"guardrails": {"cooking": {"enabled": False}}}),
        make_case(id="C", input="crash"),
        make_case(id="D"),
    ]
    healthy = mock_chatbot_factory()
    summary = run_evaluation_suite(cases, lambda config: CrashOnInput(healthy(config), "crash"))
    assert [r.case.id for r in summary.results] == ["A", "B", "C", "D"]
    assert [r.outcome for r in summary.results] == [
        CaseOutcome.PASS,
        CaseOutcome.FAIL,
        CaseOutcome.ERROR,
        CaseOutcome.PASS,
    ]


def test_runner_uses_public_interface_with_case_configuration(make_case):
    config = {"guardrails": {"cooking": {"enabled": True}, "politics": {"enabled": False}}}
    case = make_case(input="Give me a pasta recipe.", configuration=config)
    agents = []

    def factory(configuration):
        agents.append(SpyAgent(configuration))
        return agents[-1]

    result = run_case(case, factory)
    assert result.outcome is CaseOutcome.PASS
    (agent,) = agents
    assert agent.messages == ["Give me a pasta recipe."]
    assert agent.configuration == config


def test_each_case_gets_a_fresh_agent_and_an_isolated_config_copy(make_case):
    cases = [make_case(id="A"), make_case(id="B")]
    seen = []

    def mutating_factory(configuration):
        seen.append(configuration)
        configuration["guardrails"]["cooking"]["enabled"] = False  # agent misbehaves
        return SpyAgent(configuration)

    run_evaluation_suite(cases, mutating_factory)
    assert len(seen) == 2 and seen[0] is not seen[1]
    assert cases[0].configuration["guardrails"]["cooking"]["enabled"] is True


def test_duplicate_case_ids_rejected_before_running(make_case):
    with pytest.raises(DatasetValidationError, match="Duplicate case id"):
        run_evaluation_suite([make_case(id="X"), make_case(id="X")])


def test_empty_suite_is_blocked():
    summary = run_evaluation_suite([])
    assert summary.pass_rate is None
    assert summary.release_readiness is ReleaseReadiness.BLOCKED


# --- Full dataset against the mock chatbot ------------------------------------------------------


def test_healthy_chatbot_passes_full_dataset(repo_dataset_path):
    summary = run_dataset(repo_dataset_path)
    failures = {r.case.id: [a.explanation for a in r.failed_assertions] for r in summary.results
                if r.outcome is not CaseOutcome.PASS}
    assert failures == {}
    assert summary.total >= 20
    assert summary.pass_rate == 1.0
    assert summary.release_readiness is ReleaseReadiness.READY


@pytest.fixture
def fail_open_summary(repo_dataset_path):
    factory = mock_chatbot_factory(SimulatedFailure.GUARDRAIL_FAIL_OPEN)
    return run_dataset(repo_dataset_path, factory)


def test_simulated_guardrail_failure_is_detected(repo_dataset_path, fail_open_summary):
    cases = load_dataset(repo_dataset_path)
    expected_to_block = {c.id for c in cases if c.expected.blocked is True}
    failed = {r.case.id for r in fail_open_summary.results if r.outcome is CaseOutcome.FAIL}
    assert expected_to_block, "dataset must contain blocking scenarios"
    assert failed == expected_to_block
    assert fail_open_summary.errored == 0


def test_simulated_failure_reports_expected_vs_actual_blocked_status(fail_open_summary):
    result = next(r for r in fail_open_summary.results if r.case.id == "GR-001")
    blocked = next(a for a in result.failed_assertions if a.name == "blocked")
    assert (blocked.expected, blocked.actual) == (True, False)
    assert "should have blocked it" in blocked.explanation


def test_simulated_failure_uses_dataset_severity(repo_dataset_path, fail_open_summary):
    raw = json.loads(repo_dataset_path.read_text(encoding="utf-8"))
    severity_by_id = {c["id"]: c["severity"] for c in raw["cases"]}
    failed = [r for r in fail_open_summary.results if r.outcome is CaseOutcome.FAIL]
    assert all(r.case.severity.value == severity_by_id[r.case.id] for r in failed)
    expected_critical = tuple(r.case.id for r in failed if r.case.severity is Severity.CRITICAL)
    assert expected_critical
    assert fail_open_summary.critical_failures == expected_critical
    assert fail_open_summary.release_readiness is ReleaseReadiness.BLOCKED


# --- Command line -------------------------------------------------------------------------------


def test_cli_reports_ready_and_exits_zero(repo_dataset_path, capsys):
    assert main(["--dataset", str(repo_dataset_path)]) == 0
    out = capsys.readouterr().out
    assert "Release readiness: READY" in out
    assert "Critical failures: none" in out


def test_cli_with_simulated_failure_reports_blocked_and_exits_one(repo_dataset_path, capsys):
    code = main(["--dataset", str(repo_dataset_path), "--simulate-failure", "guardrail_fail_open"])
    out = capsys.readouterr().out
    assert code == 1
    assert "WARNING: simulated failure 'guardrail_fail_open' is ENABLED" in out
    assert "CRITICAL FAILURE" in out
    assert "Release readiness: BLOCKED" in out


def test_cli_json_output_is_valid_json(repo_dataset_path, capsys):
    assert main(["--dataset", str(repo_dataset_path), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["release_readiness"] == "READY"
    assert len(data["results"]) == data["total"]


def test_cli_invalid_dataset_exits_two(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{oops", encoding="utf-8")
    assert main(["--dataset", str(bad)]) == 2
    assert "Dataset error" in capsys.readouterr().err


def test_cli_missing_dataset_exits_two(tmp_path, capsys):
    assert main(["--dataset", str(tmp_path / "nope.json")]) == 2
    assert "Dataset error" in capsys.readouterr().err


@pytest.mark.parametrize("failure", ["none", "guardrail_fail_open"])
def test_json_output_records_the_simulated_failure(repo_dataset_path, capsys, failure):
    # Quality observability must never mistake a seeded-defect report for a normal run.
    main(["--dataset", str(repo_dataset_path), "--json", "--simulate-failure", failure])
    data = json.loads(capsys.readouterr().out)
    assert data["simulated_failure"] == failure and data["total"] == 33


def test_output_option_writes_a_utf8_report(repo_dataset_path, tmp_path, capsys):
    # Reliable on Windows PowerShell, where '>' redirection may change the encoding (D-8.3).
    out = tmp_path / "nested" / "report.json"
    assert main(["--dataset", str(repo_dataset_path), "--output", str(out)]) == 0
    data = json.loads(out.read_bytes().decode("utf-8"))  # strict UTF-8, no BOM
    assert data["simulated_failure"] == "none" and data["total"] == 33
    assert "Dataset:" in capsys.readouterr().out  # console summary unchanged without --json
