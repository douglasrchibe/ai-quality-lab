"""Tests for baseline approval: explicit, immutable, audited, and guarded against regressions."""

import json

import pytest

from ai_quality_lab.observability.baselines import (
    BaselineError,
    approve_baseline,
    load_baseline,
    load_baselines,
    read_manifest,
)
from ai_quality_lab.observability.records import metric
from ai_quality_lab.observability.regression import DEFAULT_RULES, load_rules

RULES = load_rules(DEFAULT_RULES)


def approve(directory, record, **kwargs):
    return approve_baseline(directory, record, approved_by="tester", reason="test",
                            rules=RULES, now="2026-11-01T12:00:00Z", **kwargs)


def audit_lines(directory):
    return [json.loads(line) for line in
            (directory / "audit.jsonl").read_text(encoding="utf-8").splitlines()]


def test_explicit_approval_writes_record_manifest_and_audit(tmp_path, make_record):
    entry = approve(tmp_path, make_record(run_id="r1"))
    assert entry["previous_run_id"] is None and entry["flags"] == []
    assert (tmp_path / "custom_engine" / "r1.json").exists()
    assert read_manifest(tmp_path)["current"]["custom_engine"]["run_id"] == "r1"
    assert load_baseline(tmp_path, "custom_engine")["run_id"] == "r1"
    assert audit_lines(tmp_path)[0]["approved_by"] == "tester"


def test_replacing_a_baseline_preserves_the_previous_one(tmp_path, make_record):
    approve(tmp_path, make_record(run_id="r1"))
    entry = approve(tmp_path, make_record(run_id="r2", recorded_at="2026-11-02T10:00:00Z"))
    assert entry["previous_run_id"] == "r1"
    assert (tmp_path / "custom_engine" / "r1.json").exists()
    assert load_baseline(tmp_path, "custom_engine")["run_id"] == "r2"
    assert [a["run_id"] for a in audit_lines(tmp_path)] == ["r1", "r2"]


@pytest.mark.parametrize(
    "overrides, message",
    [
        ({"status": "BLOCKED"}, "only a PASS run"),
        ({"parameters": {"simulated_failure": "guardrail_fail_open"}}, "seeded-defect"),
        ({"metrics": {"unexpected_skips": metric(1, numerator=1, denominator=9)}},
         "unexpected skipped"),
        ({"run_id": "../escape"}, "file name"),
    ],
)
def test_unacceptable_runs_are_rejected(tmp_path, make_record, overrides, message):
    with pytest.raises(BaselineError, match=message):
        approve(tmp_path, make_record(**overrides))
    assert not (tmp_path / "manifest.json").exists()


def test_approval_requires_a_reason_and_approver(tmp_path, make_record):
    with pytest.raises(BaselineError, match="reason"):
        approve_baseline(tmp_path, make_record(), approved_by=" ", reason="", rules=RULES)


def test_same_run_cannot_be_approved_twice(tmp_path, make_record):
    approve(tmp_path, make_record(run_id="r1"))
    with pytest.raises(BaselineError, match="already the approved"):
        approve(tmp_path, make_record(run_id="r1"))


def test_incompatible_run_needs_explicit_identity_change(tmp_path, make_record):
    approve(tmp_path, make_record(run_id="r1"))
    changed = make_record(run_id="r2", identity={"dataset_fingerprint": "d2"})
    with pytest.raises(BaselineError, match="not comparable.*--accept-identity-change"):
        approve(tmp_path, changed)
    entry = approve(tmp_path, changed, accept_identity_change=True)
    assert entry["flags"] == ["identity_change"]
    assert audit_lines(tmp_path)[-1]["flags"] == ["identity_change"]


def test_regressing_run_cannot_silently_become_the_baseline(tmp_path, make_record):
    approve(tmp_path, make_record(run_id="r1"))
    worse = make_record(run_id="r2", metrics={"pass_rate": metric(0.75, numerator=3,
                                                                  denominator=4),
                                              "critical_failures": metric(0, numerator=0,
                                                                          denominator=2)})
    with pytest.raises(BaselineError, match="regresses.*--accept-regression"):
        approve(tmp_path, worse)
    assert load_baseline(tmp_path, "custom_engine")["run_id"] == "r1"
    entry = approve(tmp_path, worse, accept_regression=True)
    assert entry["flags"] == ["regression_accepted"]


def test_existing_baseline_file_is_never_overwritten(tmp_path, make_record):
    (tmp_path / "custom_engine").mkdir()
    (tmp_path / "custom_engine" / "r1.json").write_text("{}", encoding="utf-8")
    with pytest.raises(BaselineError, match="never overwritten"):
        approve(tmp_path, make_record(run_id="r1"))


def test_edited_baseline_is_detected(tmp_path, make_record):
    approve(tmp_path, make_record(run_id="r1"))
    path = tmp_path / "custom_engine" / "r1.json"
    data = json.loads(path.read_text(encoding="utf-8"))
    data["metrics"]["pass_rate"]["value"] = 0.5
    data["metrics"]["pass_rate"]["numerator"] = 2
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(BaselineError, match="does not match the hash"):
        load_baseline(tmp_path, "custom_engine")


def test_baseline_with_crlf_line_endings_still_verifies(tmp_path, make_record):
    approve(tmp_path, make_record(run_id="r1"))
    path = tmp_path / "custom_engine" / "r1.json"
    path.write_bytes(path.read_bytes().replace(b"\n", b"\r\n"))  # a Windows checkout
    assert load_baseline(tmp_path, "custom_engine")["run_id"] == "r1"


@pytest.mark.parametrize("problem", ["missing manifest", "corrupt manifest", "missing file",
                                     "no entry"])
def test_unusable_baselines_are_errors(tmp_path, make_record, problem):
    approve(tmp_path, make_record(run_id="r1"))
    manifest = tmp_path / "manifest.json"
    if problem == "missing manifest":
        manifest.unlink()
    elif problem == "corrupt manifest":
        manifest.write_text("{oops", encoding="utf-8")
    elif problem == "missing file":
        (tmp_path / "custom_engine" / "r1.json").unlink()
    with pytest.raises(BaselineError):
        load_baseline(tmp_path, "promptfoo" if problem == "no entry" else "custom_engine")
    loaded = load_baselines(tmp_path, ["promptfoo" if problem == "no entry" else "custom_engine"])
    assert all(isinstance(v, BaselineError) for v in loaded.values())


def test_corrupt_current_baseline_blocks_a_new_approval(tmp_path, make_record):
    approve(tmp_path, make_record(run_id="r1"))
    (tmp_path / "custom_engine" / "r1.json").write_text("{}", encoding="utf-8")
    with pytest.raises(BaselineError):
        approve(tmp_path, make_record(run_id="r2"))


# --- Phase 8.1 remediation ----------------------------------------------------------------------

from ai_quality_lab.observability.baselines import audit_problems, quality_blockers  # noqa: E402


def inconsistent(make_record, run_id="bad"):
    """Status says PASS, but a critical case failed (what a buggy or edited report produces)."""
    cases = [{"id": "C-0", "outcome": "FAIL", "severity": "critical"},
             {"id": "C-1", "outcome": "PASS", "severity": "critical"},
             {"id": "C-2", "outcome": "PASS", "severity": "high"},
             {"id": "C-3", "outcome": "PASS", "severity": "medium"}]
    return make_record(run_id=run_id, status="PASS", cases=cases, metrics={
        "case_count": metric(4), "pass_rate": metric(0.75, numerator=3, denominator=4),
        "critical_failures": metric(1, numerator=1, denominator=2),
        "error_count": metric(0, numerator=0, denominator=4)})


def test_inconsistent_record_is_rejected_as_a_first_baseline(tmp_path, make_record):
    with pytest.raises(BaselineError, match="whatever its status says: 1 critical failure"):
        approve(tmp_path, inconsistent(make_record))
    assert not (tmp_path / "manifest.json").exists()


def test_inconsistent_record_is_rejected_against_an_existing_baseline(tmp_path, make_record):
    approve(tmp_path, make_record(run_id="r1"))
    with pytest.raises(BaselineError, match="whatever its status says"):
        approve(tmp_path, inconsistent(make_record, "r2"), accept_regression=True)
    assert load_baseline(tmp_path, "custom_engine")["run_id"] == "r1"


@pytest.mark.parametrize(
    "overrides, reason",
    [
        ({"cases": [{"id": "C-0", "outcome": "ERROR", "severity": "low"}]}, "errored case"),
        ({"evaluation_type": "rag_mock",
          "metrics": {"case_count": metric(1), "doc_recall": metric(0.5, denominator=19),
                      "doc_hit_rate": metric(0.95, denominator=19)}},
         "retrieval gate failed: document recall"),
        ({"evaluation_type": "pytest", "cases": [],
          "metrics": {"tests_failed": metric(1, numerator=1, denominator=10)}}, "tests failed"),
    ],
)
def test_quality_blockers_ignore_the_claimed_status(make_record, overrides, reason):
    assert any(reason in r for r in quality_blockers(make_record(**overrides)))


def test_clean_record_has_no_quality_blockers(make_record):
    assert quality_blockers(make_record()) == []


@pytest.mark.parametrize("source, problem", [
    ({"commit": None}, "unknown commit"),
    ({"uncommitted_changes": True}, "uncommitted changes on top of abc1234"),
])
def test_unreproducible_runs_need_an_explicit_override(tmp_path, make_record, source, problem):
    record = make_record(run_id="r1")
    record["source"].update(source)
    with pytest.raises(BaselineError, match=f"{problem}.*--allow-uncommitted"):
        approve(tmp_path, record)
    entry = approve(tmp_path, record, allow_uncommitted=True)
    assert entry["flags"] == ["uncommitted_source"]
    assert audit_lines(tmp_path)[-1]["flags"] == ["uncommitted_source"]


def test_identity_change_shows_old_and_new_identity_and_metric_differences(tmp_path,
                                                                            make_record):
    approve(tmp_path, make_record(run_id="r1"))
    changed = make_record(run_id="r2", identity={"dataset_fingerprint": "d2"},
                          metrics={"pass_rate": metric(1.0, numerator=5, denominator=5),
                                   "critical_failures": metric(0, numerator=0, denominator=3)})
    with pytest.raises(BaselineError) as info:
        approve(tmp_path, changed)
    message = str(info.value)
    assert "dataset_fingerprint: 'd1' -> 'd2'" in message
    assert "critical_failures: 0 -> 0" not in message  # unchanged values are not listed
    assert "--accept-identity-change" in message
    entry = approve(tmp_path, changed, accept_identity_change=True)
    assert entry["identity_change"]["identity"] == {"dataset_fingerprint": ["d1", "d2"]}
    assert audit_lines(tmp_path)[-1]["identity_change"]["identity"]["dataset_fingerprint"] == [
        "d1", "d2"]


def test_audit_log_must_cover_every_manifest_entry(tmp_path, make_record):
    approve(tmp_path, make_record(run_id="r1"))
    approve(tmp_path, make_record(evaluation_type="promptfoo", run_id="p1"))
    assert audit_problems(tmp_path) == []
    lines = (tmp_path / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    (tmp_path / "audit.jsonl").write_text(lines[0] + "\n", encoding="utf-8")  # drop one
    assert audit_problems(tmp_path) == ["promptfoo: no approval in the audit log"]


def test_committed_baselines_match_their_audit_log():
    from pathlib import Path

    assert audit_problems(Path(__file__).resolve().parents[1] / "reports" / "baselines") == []
