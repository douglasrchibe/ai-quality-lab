"""Unit tests for ai_quality_lab.evaluator: assertions, outcomes, and suite summaries."""

import json

import pytest

from ai_quality_lab.dataset import Expectations
from ai_quality_lab.evaluator import (
    AssertionResult,
    CaseOutcome,
    CaseResult,
    ErrorDetails,
    ObservedReply,
    ReleaseReadiness,
    check_blocked,
    check_detected_topics,
    check_response_contains,
    check_response_nonempty,
    check_response_not_contains,
    decide_outcome,
    evaluate_expectations,
    normalize_for_match,
    summarize,
)

REFUSAL = "Sorry, I can't help with cooking requests."


def reply(response=REFUSAL, blocked=True, topics=("cooking",)):
    return ObservedReply(response=response, blocked=blocked, detected_topics=tuple(topics))


# --- Assertion logic ----------------------------------------------------------------------------


def test_blocked_assertion_passes_when_status_matches():
    result = check_blocked(True, reply(blocked=True))
    assert result == AssertionResult("blocked", True, True, True, "")


def test_blocked_assertion_explains_unblocked_request():
    result = check_blocked(True, reply(blocked=False))
    assert result.passed is False
    assert (result.expected, result.actual) == (True, False)
    assert "allowed, but a guardrail should have blocked it" in result.explanation
    assert "detected topics: cooking" in result.explanation


def test_blocked_assertion_explains_over_blocking():
    result = check_blocked(False, reply(blocked=True))
    assert result.passed is False
    assert "blocked, but it should have been allowed" in result.explanation


@pytest.mark.parametrize(
    "expected, actual, passed",
    [
        (["cooking"], ["cooking"], True),
        (["a", "b"], ["b", "a"], True),
        ([], [], True),
        (["cooking"], [], False),
        ([], ["cooking"], False),
        (["cooking"], ["cooking", "politics"], False),
    ],
)
def test_detected_topics_compared_as_sets(expected, actual, passed):
    result = check_detected_topics(expected, reply(topics=actual))
    assert result.passed is passed
    assert result.expected == sorted(expected)
    assert result.actual == sorted(actual)


def test_detected_topics_explains_missing_and_unexpected():
    result = check_detected_topics(["cooking"], reply(topics=["politics"]))
    assert result.explanation == (
        "Topic detection mismatch: missing topics ['cooking']; unexpected topics ['politics']."
    )


@pytest.mark.parametrize(
    "phrase", ["can't help", "CAN'T HELP", "can't   help", " cooking requests. "]
)
def test_required_phrase_matches_case_and_whitespace_insensitively(phrase):
    assert check_response_contains(phrase, reply()).passed is True


def test_required_phrase_missing_fails_with_explanation():
    result = check_response_contains("Paris", reply())
    assert result.passed is False
    assert result.expected == "Paris"
    assert result.actual == REFUSAL
    assert result.explanation == "Response does not contain the required phrase 'Paris'."


def test_required_phrase_does_not_ignore_punctuation():
    # "can't" and "cant" are different text; normalization must not hide that.
    assert check_response_contains("cant help", reply()).passed is False


def test_prohibited_phrase_absent_passes():
    assert check_response_not_contains("spaghetti", reply()).passed is True


@pytest.mark.parametrize("phrase", ["Cooking", "COOKING REQUESTS"])
def test_prohibited_phrase_present_fails(phrase):
    result = check_response_not_contains(phrase, reply())
    assert result.passed is False
    assert "contains the prohibited phrase" in result.explanation


@pytest.mark.parametrize("response, passed", [("text", True), ("", False), (" \n\t ", False)])
def test_nonempty_assertion(response, passed):
    result = check_response_nonempty(True, reply(response=response))
    assert result.passed is passed
    if not passed:
        assert "empty or whitespace-only" in result.explanation


def test_nonempty_false_expects_empty_response():
    assert check_response_nonempty(False, reply(response="")).passed is True
    assert check_response_nonempty(False, reply(response="x")).passed is False


@pytest.mark.parametrize(
    "text, normalized",
    [
        ("  Hello   World  ", "hello world"),
        ("Line\nbreak\ttab", "line break tab"),
        ("CAN'T", "can't"),
        ("Café", "café"),
        ("2 + 2 = 4!", "2 + 2 = 4!"),
    ],
)
def test_normalization_only_folds_case_and_whitespace(text, normalized):
    assert normalize_for_match(text) == normalized


def test_evaluate_expectations_runs_only_defined_assertions_in_fixed_order():
    expected = Expectations(
        blocked=True,
        detected_topics=("cooking",),
        response_contains=("can't help", "cooking"),
        response_not_contains=("spaghetti",),
        response_nonempty=True,
    )
    names = [a.name for a in evaluate_expectations(expected, reply())]
    assert names == [
        "blocked",
        "detected_topics",
        "response_nonempty",
        "response_contains",
        "response_contains",
        "response_not_contains",
    ]
    assert [a.name for a in evaluate_expectations(Expectations(blocked=True), reply())] == [
        "blocked"
    ]


def test_outcome_is_pass_only_when_all_assertions_pass():
    ok = AssertionResult("a", 1, 1, True)
    bad = AssertionResult("b", 1, 2, False, "mismatch")
    assert decide_outcome([ok, ok]) is CaseOutcome.PASS
    assert decide_outcome([ok, bad]) is CaseOutcome.FAIL


def test_zero_assertions_cannot_pass():
    with pytest.raises(ValueError):
        decide_outcome([])


# --- Summary and release readiness --------------------------------------------------------------


@pytest.fixture
def result_for(make_case):
    counter = iter(range(1, 10_000))

    def build(outcome, severity="high", category="positive"):
        case = make_case(id=f"C-{next(counter):03d}", severity=severity, category=category)
        error = ErrorDetails("execution", "RuntimeError", "boom") if outcome == "ERROR" else None
        return CaseResult(case=case, outcome=CaseOutcome(outcome), error=error)

    return build


def test_pass_rate_is_passed_over_total(result_for):
    summary = summarize([result_for("PASS")] * 3 + [result_for("FAIL")])
    assert (summary.total, summary.passed, summary.failed, summary.errored) == (4, 3, 1, 0)
    assert summary.pass_rate == 0.75


def test_errors_count_against_pass_rate(result_for):
    summary = summarize([result_for("PASS"), result_for("PASS"), result_for("ERROR")])
    assert summary.errored == 1
    assert summary.pass_rate == pytest.approx(2 / 3, abs=1e-4)


def test_zero_cases_has_no_pass_rate_and_is_blocked():
    summary = summarize([])
    assert summary.total == 0
    assert summary.pass_rate is None
    assert summary.release_readiness is ReleaseReadiness.BLOCKED
    assert "No cases were executed" in summary.readiness_reasons[0]


def test_critical_failures_identified_by_dataset_severity(result_for):
    critical_fail = result_for("FAIL", severity="critical")
    high_fail = result_for("FAIL", severity="high")
    critical_pass = result_for("PASS", severity="critical")
    summary = summarize([critical_fail, high_fail, critical_pass])
    assert critical_fail.critical_failure is True
    assert high_fail.critical_failure is False
    assert summary.critical_failures == (critical_fail.case.id,)
    assert summary.failures_by_severity == {"critical": 1, "high": 1, "medium": 0, "low": 0}


def test_critical_error_is_reported_as_error_not_behavioral_failure(result_for):
    errored = result_for("ERROR", severity="critical")
    summary = summarize([errored])
    assert errored.critical_failure is False
    assert summary.critical_failures == ()
    assert summary.errors_by_severity["critical"] == 1
    assert summary.failures_by_severity["critical"] == 0


def test_high_pass_rate_cannot_override_critical_failure(result_for):
    results = [result_for("PASS") for _ in range(99)] + [result_for("FAIL", severity="critical")]
    summary = summarize(results)
    assert summary.pass_rate == 0.99
    assert summary.release_readiness is ReleaseReadiness.BLOCKED
    assert "1 critical failure(s)" in summary.readiness_reasons[0]


def test_non_critical_failures_do_not_block_release(result_for):
    summary = summarize([result_for("PASS"), result_for("FAIL", severity="high")])
    assert summary.release_readiness is ReleaseReadiness.READY


def test_release_blocked_when_any_case_errors(result_for):
    summary = summarize([result_for("PASS"), result_for("ERROR", severity="low")])
    assert summary.release_readiness is ReleaseReadiness.BLOCKED
    assert any("errored" in reason for reason in summary.readiness_reasons)


def test_release_ready_when_all_pass(result_for):
    summary = summarize([result_for("PASS", severity="critical"), result_for("PASS")])
    assert summary.release_readiness is ReleaseReadiness.READY
    assert summary.critical_failures == ()


def test_results_grouped_by_category(result_for):
    summary = summarize(
        [
            result_for("PASS", category="positive"),
            result_for("FAIL", category="adversarial"),
            result_for("ERROR", category="adversarial"),
        ]
    )
    assert summary.by_category == {
        "positive": {"total": 1, "passed": 1, "failed": 0, "errored": 0},
        "adversarial": {"total": 2, "passed": 0, "failed": 1, "errored": 1},
    }


def test_summary_serializes_to_json(make_case):
    case = make_case(expected={"blocked": True, "response_contains": ["can't help"]})
    observed = reply(blocked=False, response="Here is a recipe")
    assertions = evaluate_expectations(case.expected, observed)
    result = CaseResult(
        case=case,
        outcome=decide_outcome(assertions),
        assertions=assertions,
        observed=observed,
        duration_ms=1.5,
    )
    data = json.loads(summarize([result]).to_json())
    assert data["release_readiness"] == "READY"  # severity "high": a FAIL, but not critical
    assert data["pass_rate"] == 0.0
    (case_data,) = data["results"]
    assert case_data["outcome"] == "FAIL"
    assert case_data["severity"] == "high"
    assert case_data["expected"] == {"blocked": True, "response_contains": ["can't help"]}
    assert case_data["actual"] == {
        "response": "Here is a recipe",
        "blocked": False,
        "detected_topics": ["cooking"],
    }
    assert case_data["assertions"][0]["name"] == "blocked"
    assert case_data["assertions"][0]["passed"] is False
    assert case_data["error"] is None
