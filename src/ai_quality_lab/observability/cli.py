"""Command line: ``python -m ai_quality_lab.observability <command>``.

    record   --kind custom_engine|promptfoo|rag|pytest|ragas --report FILE --history FILE
    compare  --history FILE --types t1,t2 [--run-id ID ... | --latest] [--baselines DIR]
             [--output-json F] [--output-md F]
    trend    --history FILE --type T [--last N]
    report   --history FILE [--baselines DIR] --output FILE
    baseline approve --history FILE --run-id ID --approved-by NAME --reason TEXT
                     [--accept-identity-change] [--accept-regression] [--allow-uncommitted]
    baseline show [--baselines DIR]
    baseline verify [--baselines DIR]   (hashes, schema, manifest vs audit log; exit 0 or 2)

Exit codes: 0 PASS, 1 BLOCKED (a release-blocking regression), 2 ERROR (could not compare
reliably: missing or corrupt history/baseline/rules, invalid records, rejected approval).
"""

from __future__ import annotations

import argparse
import json
import sys
from collections.abc import Sequence
from pathlib import Path

from ai_quality_lab.observability.baselines import (
    DEFAULT_BASELINES,
    BaselineError,
    approve_baseline,
    audit_problems,
    describe_identity_change,
    load_baselines,
    read_manifest,
)
from ai_quality_lab.observability.history import (
    DEFAULT_HISTORY,
    HistoryError,
    append_records,
    read_history,
)
from ai_quality_lab.observability.records import (
    EvaluationType,
    RecordError,
    build_record,
    git_source,
)
from ai_quality_lab.observability.regression import (
    DEFAULT_RULES,
    RulesError,
    compare_records,
    compare_run,
    load_rules,
    select_records,
)
from ai_quality_lab.reporting import TREND_COLUMNS, render_quality_report, render_run_comparison

EXIT = {"PASS": 0, "BLOCKED": 1, "ERROR": 2}
KNOWN_DEFECTS = Path("ci") / "known_defects.json"
TYPES = [t.value for t in EvaluationType]


def _write(path: Path | None, text: str) -> None:
    if path is not None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")


def cmd_record(args: argparse.Namespace) -> int:
    source = git_source()
    # Recording an older report: say which commit produced it instead of stamping HEAD on it.
    for key in ("commit", "branch"):
        value = getattr(args, key)
        if value is not None:
            source[key] = None if value == "unknown" else value
    if args.commit is not None:
        # The report came from that commit, not from this working tree: its state is unknown.
        source["uncommitted_changes"] = None
    record = build_record(args.kind, args.report, dataset=args.dataset,
                          knowledge_base=args.knowledge_base, run_id=args.run_id, note=args.note,
                          source=source)
    if args.history.exists():
        same = [r["run_id"] for r in read_history(args.history)
                if r["evaluation_type"] == record["evaluation_type"]
                and r["source"].get("report_sha256") == record["source"]["report_sha256"]]
        if same:
            # Not refused: a repeated deterministic evaluation can legitimately produce an
            # identical report (e.g. the pytest skip audit has no timings).
            print(f"WARNING: an identical {record['evaluation_type']} report is already recorded "
                  f"as {same}. If this is the same evaluation, it is now counted twice in trends.",
                  file=sys.stderr)
    append_records(args.history, [record])
    print(f"recorded {record['evaluation_type']} run {record['run_id']} status={record['status']} "
          f"-> {args.history}")
    return 0


def cmd_compare(args: argparse.Namespace) -> int:
    types = [t.strip() for t in args.types.split(",") if t.strip()]
    unknown = sorted(set(types) - set(TYPES))
    if unknown:
        raise RecordError(f"unknown evaluation type(s) {unknown}; choose from {TYPES}")
    rules = load_rules(args.rules)
    history = read_history(args.history)
    run_ids = [i.strip() for value in args.run_id for i in value.split(",") if i.strip()]
    selected, problems, notes = select_records(history, types, run_ids, args.latest)
    run = compare_run(selected, load_baselines(args.baselines, types), types, rules)
    run.problems[:0] = problems
    run.selection = notes
    data = run.to_dict()
    text = render_run_comparison(data)
    _write(args.output_json, json.dumps(data, indent=2, ensure_ascii=False) + "\n")
    _write(args.output_md, text)
    print(text)
    return EXIT[data["status"]]


def cmd_trend(args: argparse.Namespace) -> int:
    rules = load_rules(args.rules)
    records = sorted((r for r in read_history(args.history) if r["evaluation_type"] == args.type),
                     key=lambda r: r["recorded_at"])
    if not records:
        print(f"No {args.type} records in {args.history}.")
        return EXIT["ERROR"]
    from ai_quality_lab.reporting import _trend_section

    print("\n".join(_trend_section(args.type, records, rules, args.last)))
    latest = records[-1]
    previous = [r for r in records[:-1] if r["comparability_key"] == latest["comparability_key"]]
    if not previous:
        return EXIT["PASS"]
    return EXIT[compare_records(previous[-1], latest, rules).status]


def cmd_report(args: argparse.Namespace) -> int:
    rules = load_rules(args.rules)
    history = read_history(args.history)
    types = sorted({r["evaluation_type"] for r in history})
    text = render_quality_report(history, load_baselines(args.baselines, types), rules,
                                 args.known_defects, args.last)
    _write(args.output, text)
    print(text)
    return 0  # the report is informational; `compare` is the gate


def cmd_baseline(args: argparse.Namespace) -> int:
    if args.baseline_command == "show":
        manifest = read_manifest(args.baselines)
        for etype, entry in sorted(manifest["current"].items()):
            print(f"{etype}: {entry['run_id']} (commit {str(entry.get('commit'))[:7]}, approved "
                  f"{entry['approved_at']} by {entry['approved_by']}: {entry['reason']})")
        return 0
    if args.baseline_command == "verify":
        manifest = read_manifest(args.baselines)
        problems = audit_problems(args.baselines)
        loaded = load_baselines(args.baselines, sorted(manifest["current"]))
        problems += [f"{t}: {v}" for t, v in loaded.items() if isinstance(v, Exception)]
        for problem in problems:
            print(f"PROBLEM: {problem}")
        print(f"{len(loaded)} baseline(s) checked: {'OK' if not problems else 'ERROR'}")
        return EXIT["ERROR"] if problems else EXIT["PASS"]
    matches = [r for r in read_history(args.history) if r["run_id"] == args.run_id]
    if not matches:
        raise BaselineError(f"run {args.run_id!r} is not in {args.history}")
    entry = approve_baseline(args.baselines, matches[0], approved_by=args.approved_by,
                             reason=args.reason, rules=load_rules(args.rules),
                             accept_identity_change=args.accept_identity_change,
                             accept_regression=args.accept_regression,
                             allow_uncommitted=args.allow_uncommitted)
    print(f"approved {entry['evaluation_type']} baseline {entry['run_id']} "
          f"(previous: {entry['previous_run_id']}; flags: {entry['flags'] or 'none'})")
    if entry["identity_change"]:
        print("identity change accepted: " + describe_identity_change(entry["identity_change"]))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m ai_quality_lab.observability")
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser, history: bool = True) -> None:
        if history:
            p.add_argument("--history", type=Path, default=DEFAULT_HISTORY)
        p.add_argument("--rules", type=Path, default=DEFAULT_RULES)
        p.add_argument("--baselines", type=Path, default=DEFAULT_BASELINES)

    record = sub.add_parser("record", help="append one evaluation report to the history")
    record.add_argument("--kind", required=True,
                        choices=["custom_engine", "promptfoo", "rag", "pytest", "ragas"])
    record.add_argument("--report", type=Path, required=True)
    record.add_argument("--dataset", type=Path)
    record.add_argument("--knowledge-base", type=Path)
    record.add_argument("--run-id")
    record.add_argument("--note")
    record.add_argument("--commit", help="commit that produced the report ('unknown' if not "
                        "known); default: the current checkout")
    record.add_argument("--branch", help="branch that produced the report ('unknown' if not known)")
    common(record)

    compare = sub.add_parser("compare", help="compare a run's records with approved baselines")
    compare.add_argument("--types", required=True, help="comma-separated evaluation types")
    compare.add_argument("--run-id", action="append", default=[],
                         help="compare exactly these runs (repeatable or comma-separated; one "
                         "per type). Default: the history must hold one record per type (CI)")
    compare.add_argument("--latest", action="store_true",
                         help="compare the latest run of each type (by recorded_at); a tie is "
                         "an ERROR. The selected run is printed")
    compare.add_argument("--output-json", type=Path)
    compare.add_argument("--output-md", type=Path)
    common(compare)

    trend = sub.add_parser("trend", help="show comparable runs of one evaluation type")
    trend.add_argument("--type", required=True, choices=sorted(TREND_COLUMNS))
    trend.add_argument("--last", type=int, default=10)
    common(trend)

    report = sub.add_parser("report", help="write the full Markdown quality report")
    report.add_argument("--output", type=Path, default=Path("reports") / "quality" /
                        "quality-report.md")
    report.add_argument("--known-defects", type=Path, default=KNOWN_DEFECTS)
    report.add_argument("--last", type=int, default=10)
    common(report)

    baseline = sub.add_parser("baseline", help="approve or show baselines (never run in CI)")
    bsub = baseline.add_subparsers(dest="baseline_command", required=True)
    approve = bsub.add_parser("approve")
    approve.add_argument("--run-id", required=True)
    approve.add_argument("--approved-by", required=True)
    approve.add_argument("--reason", required=True)
    approve.add_argument("--accept-identity-change", action="store_true")
    approve.add_argument("--accept-regression", action="store_true")
    approve.add_argument("--allow-uncommitted", action="store_true",
                         help="approve a run recorded with uncommitted changes or an unknown "
                         "commit (written to the audit log)")
    common(approve)
    show = bsub.add_parser("show")
    common(show, history=False)
    verify = bsub.add_parser("verify", help="check every baseline's hash, schema, and audit entry")
    common(verify, history=False)
    return parser


COMMANDS = {"record": cmd_record, "compare": cmd_compare, "trend": cmd_trend,
            "report": cmd_report, "baseline": cmd_baseline}


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except (RecordError, HistoryError, BaselineError, RulesError, OSError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return EXIT["ERROR"]


if __name__ == "__main__":
    raise SystemExit(main())
