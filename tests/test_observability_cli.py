"""End-to-end tests of the observability CLI, the Markdown reports, and the committed baselines."""

import json
from pathlib import Path

import pytest

from ai_quality_lab.dataset import load_dataset
from ai_quality_lab.mock_agent import SimulatedFailure
from ai_quality_lab.observability.baselines import load_baseline, read_manifest
from ai_quality_lab.observability.cli import main
from ai_quality_lab.observability.history import read_history
from ai_quality_lab.reporting import fmt_metric, render_run_comparison
from ai_quality_lab.runner import mock_chatbot_factory, run_evaluation_suite

ROOT = Path(__file__).resolve().parents[1]
GUARDRAIL = ROOT / "evals" / "guardrail_cases.json"
RULES = ROOT / "ci" / "regression_rules.json"
DEFECTS = ROOT / "ci" / "known_defects.json"
CI_TYPES = ["custom_engine", "promptfoo", "rag_retrieval", "rag_mock", "pytest"]


def engine_report(tmp_path, failure=SimulatedFailure.NONE):
    summary = run_evaluation_suite(load_dataset(GUARDRAIL), mock_chatbot_factory(failure))
    path = tmp_path / f"engine-{failure.value}.json"
    path.write_text(json.dumps({**summary.to_dict(), "simulated_failure": failure.value}),
                    encoding="utf-8")
    return path


def record(tmp_path, history, report, run_id, kind="custom_engine", *extra):
    return main(["record", "--kind", kind, "--report", str(report), "--history", str(history),
                 "--dataset", str(GUARDRAIL), "--run-id", run_id, "--rules", str(RULES), *extra])


@pytest.fixture
def approved(tmp_path):
    """A history with one healthy custom-engine run, approved as the baseline."""
    history, baselines = tmp_path / "base.jsonl", tmp_path / "baselines"
    # An explicit commit: the record must not depend on whether this checkout is clean.
    assert record(tmp_path, history, engine_report(tmp_path), "base", "custom_engine",
                  "--commit", "abc1234") == 0
    assert main(["baseline", "approve", "--history", str(history), "--baselines", str(baselines),
                 "--run-id", "base", "--approved-by", "tester", "--reason", "test",
                 "--rules", str(RULES)]) == 0
    return baselines


def compare(history, baselines, tmp_path, types="custom_engine"):
    out = tmp_path / "regression.json"
    code = main(["compare", "--history", str(history), "--baselines", str(baselines), "--types",
                 types, "--rules", str(RULES), "--output-json", str(out),
                 "--output-md", str(tmp_path / "regression.md")])
    return code, (json.loads(out.read_text(encoding="utf-8")) if out.exists() else None)


def test_healthy_run_passes_against_its_baseline(tmp_path, approved):
    history = tmp_path / "run.jsonl"
    record(tmp_path, history, engine_report(tmp_path), "run-1")
    code, data = compare(history, approved, tmp_path)
    assert (code, data["status"], data["release_readiness"]) == (0, "PASS", "READY")


def test_seeded_critical_regression_blocks(tmp_path, approved):
    history = tmp_path / "demo.jsonl"
    record(tmp_path, history, engine_report(tmp_path, SimulatedFailure.GUARDRAIL_FAIL_OPEN), "fo")
    code, data = compare(history, approved, tmp_path)
    assert (code, data["status"], data["errored"]) == (1, "BLOCKED", 0)
    findings = data["comparisons"][0]["findings"]
    assert any(f["metric"] == "critical_failures" and f["current"] == 9 for f in findings)
    assert any("new critical failure" in f["reason"] for f in findings)
    assert "guardrail_fail_open" in (tmp_path / "regression.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("problem", ["missing baseline dir", "corrupt history", "missing history",
                                     "missing record type", "corrupt rules"])
def test_unreliable_comparisons_are_errors_never_pass(tmp_path, approved, problem):
    history = tmp_path / "run.jsonl"
    record(tmp_path, history, engine_report(tmp_path), "run-1")
    baselines, types, rules = approved, "custom_engine", RULES
    if problem == "missing baseline dir":
        baselines = tmp_path / "nowhere"
    elif problem == "corrupt history":
        with history.open("a", encoding="utf-8") as handle:
            handle.write("{truncated\n")
    elif problem == "missing history":
        history = tmp_path / "none.jsonl"
    elif problem == "missing record type":
        types = "custom_engine,promptfoo"
    elif problem == "corrupt rules":
        rules = tmp_path / "rules.json"
        rules.write_text("{", encoding="utf-8")
    code = main(["compare", "--history", str(history), "--baselines", str(baselines),
                 "--types", types, "--rules", str(rules)])
    assert code == 2


def test_unknown_type_and_bad_record_inputs_are_errors(tmp_path, approved):
    assert main(["compare", "--history", str(tmp_path / "base.jsonl"), "--types", "nope",
                 "--baselines", str(approved)]) == 2
    bad = tmp_path / "bad.json"
    bad.write_text("{", encoding="utf-8")
    assert record(tmp_path, tmp_path / "h.jsonl", bad, "x") == 2
    assert not (tmp_path / "h.jsonl").exists()


def test_record_can_label_an_older_report_with_its_own_commit(tmp_path):
    history = tmp_path / "h.jsonl"
    assert record(tmp_path, history, engine_report(tmp_path), "old", "custom_engine",
                  "--commit", "unknown", "--branch", "phase-6") == 0
    source = read_history(history)[0]["source"]
    assert (source["commit"], source["branch"]) == (None, "phase-6")


def test_baseline_approval_rejections_exit_with_error(tmp_path, approved):
    history = tmp_path / "demo.jsonl"
    record(tmp_path, history, engine_report(tmp_path, SimulatedFailure.GUARDRAIL_FAIL_OPEN), "fo")
    args = ["baseline", "approve", "--baselines", str(approved), "--approved-by", "t",
            "--reason", "r", "--rules", str(RULES), "--history", str(history)]
    assert main([*args, "--run-id", "fo"]) == 2  # seeded failure, BLOCKED
    assert main([*args, "--run-id", "not-there"]) == 2
    assert load_baseline(approved, "custom_engine")["run_id"] == "base"


def test_trend_and_report(tmp_path, approved, capsys):
    history = tmp_path / "base.jsonl"
    record(tmp_path, history, engine_report(tmp_path), "run-2")
    assert main(["trend", "--history", str(history), "--type", "custom_engine",
                 "--rules", str(RULES)]) == 0
    assert "2 comparable run(s)" in capsys.readouterr().out
    out = tmp_path / "report.md"
    assert main(["report", "--history", str(history), "--baselines", str(approved), "--output",
                 str(out), "--rules", str(RULES), "--known-defects", str(DEFECTS)]) == 0
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# AI Quality Observability Report: ✅ PASS")
    for section in ("## Latest results", "## Baseline comparison", "## Trends",
                    "## Known unresolved defects", "## Data limitations"):
        assert section in text
    for defect in ("D-6.1", "D-6.2", "D-6.3", "D-6.4"):
        assert defect in text
    assert "no record of this evaluation type" in text  # D-6.1's rag_live metric: not invented


def test_report_with_nothing_compared_is_not_a_pass(tmp_path, capsys):
    history = tmp_path / "h.jsonl"
    record(tmp_path, history, engine_report(tmp_path), "only")
    out = tmp_path / "report.md"
    main(["report", "--history", str(history), "--baselines", str(tmp_path / "none"),
          "--output", str(out), "--rules", str(RULES), "--known-defects", str(DEFECTS)])
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# AI Quality Observability Report: ⚠️ ERROR")
    assert "Not compared (no usable baseline): custom_engine" in text


def test_metric_formatting_shows_denominators_and_advisory_labels():
    from ai_quality_lab.observability.records import MetricKind, metric

    assert fmt_metric(metric(0.3158, numerator=6, denominator=19)) == "0.3158 (6/19)"
    assert fmt_metric(metric(0.9, denominator=19)) == "0.9 (n=19)"
    assert fmt_metric(metric(0.4, MetricKind.ADVISORY, denominator=4)).endswith("*advisory*")
    assert fmt_metric(None, "retrieval-only run") == "not measured (retrieval-only run)"


def test_run_report_lists_problems():
    text = render_run_comparison({"status": "ERROR", "problems": ["pytest: no record"],
                                  "comparisons": []})
    assert "ERROR" in text and "pytest: no record" in text


# --- The committed baselines are usable ---------------------------------------------------------


def test_committed_baselines_cover_every_ci_type_and_verify():
    baselines = ROOT / "reports" / "baselines"
    assert set(CI_TYPES) <= set(read_manifest(baselines)["current"])
    for etype in CI_TYPES:
        baseline = load_baseline(baselines, etype)  # hash, schema, and manifest checks
        assert baseline["status"] == "PASS"
        assert baseline["parameters"].get("simulated_failure", "none") == "none"
    audit = (baselines / "audit.jsonl").read_text(encoding="utf-8").splitlines()
    assert len(audit) >= len(CI_TYPES)


# --- Phase 8.1 remediation ----------------------------------------------------------------------


def test_multi_run_local_history_compare_by_run_id_and_latest(tmp_path, approved, capsys):
    history = tmp_path / "local.jsonl"
    record(tmp_path, history, engine_report(tmp_path), "healthy")
    record(tmp_path, history, engine_report(tmp_path, SimulatedFailure.GUARDRAIL_FAIL_OPEN),
           "seeded")
    base = ["compare", "--history", str(history), "--baselines", str(approved), "--types",
            "custom_engine", "--rules", str(RULES)]
    assert main(base) == 2  # two runs and no selection: ambiguous, never a guess
    assert main([*base, "--run-id", "healthy"]) == 0
    assert main([*base, "--run-id", "seeded"]) == 1
    assert main([*base, "--run-id", "missing-run"]) == 2
    assert main([*base, "--run-id", "healthy,seeded"]) == 2
    # (A same-second tie under --latest is an ERROR; tested deterministically in
    # test_observability_regression.py, since wall-clock timing here would make this flaky.)
    from ai_quality_lab.observability.history import append_records
    from ai_quality_lab.observability.records import build_record

    timed = tmp_path / "timed.jsonl"
    for run_id, failure, at in (("older", SimulatedFailure.NONE, "2026-11-01T10:00:00Z"),
                                ("newer", SimulatedFailure.GUARDRAIL_FAIL_OPEN,
                                 "2026-11-01T11:00:00Z")):
        append_records(timed, [build_record("custom_engine", engine_report(tmp_path, failure),
                                            dataset=GUARDRAIL, run_id=run_id, recorded_at=at,
                                            source={"commit": "abc1234"})])
    capsys.readouterr()
    latest = ["compare", "--history", str(timed), "--baselines", str(approved), "--types",
              "custom_engine", "--rules", str(RULES), "--latest"]
    assert main(latest) == 1  # the newer run is the seeded regression
    assert "custom_engine: newer (latest of 2 custom_engine run(s)" in capsys.readouterr().out


def test_duplicate_report_is_recorded_with_a_warning(tmp_path, capsys):
    history, report = tmp_path / "h.jsonl", engine_report(tmp_path)
    assert record(tmp_path, history, report, "first") == 0
    assert record(tmp_path, history, report, "second") == 0
    assert "identical custom_engine report is already recorded as ['first']" in \
        capsys.readouterr().err
    assert len(read_history(history)) == 2


def test_bom_report_records_through_the_cli(tmp_path):
    report = engine_report(tmp_path)
    bom = tmp_path / "bom.json"
    bom.write_bytes(b"\xef\xbb\xbf" + report.read_bytes())
    assert record(tmp_path, tmp_path / "h.jsonl", bom, "bom") == 0


def test_baseline_verify(tmp_path, approved):
    assert main(["baseline", "verify", "--baselines", str(approved)]) == 0
    assert main(["baseline", "verify", "--baselines", str(ROOT / "reports" / "baselines")]) == 0
    (approved / "audit.jsonl").write_text("", encoding="utf-8")
    assert main(["baseline", "verify", "--baselines", str(approved)]) == 2


def test_cli_approval_rejects_an_uncommitted_record_unless_overridden(tmp_path):
    history, baselines = tmp_path / "h.jsonl", tmp_path / "b"
    record(tmp_path, history, engine_report(tmp_path), "r", "custom_engine", "--commit",
           "unknown")
    args = ["baseline", "approve", "--history", str(history), "--baselines", str(baselines),
            "--run-id", "r", "--approved-by", "t", "--reason", "test", "--rules", str(RULES)]
    assert main(args) == 2
    assert main([*args, "--allow-uncommitted"]) == 0
