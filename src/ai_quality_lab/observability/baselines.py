"""Explicitly approved baselines: immutable files, a manifest, and an append-only audit log.

Layout (committed to Git; ``reports/baselines/`` is the one tracked folder under ``reports/``)::

    reports/baselines/manifest.json         current baseline per evaluation type (+ record hash)
    reports/baselines/audit.jsonl           one line per approval, never rewritten
    reports/baselines/<type>/<run_id>.json  the approved record; never overwritten

Rules:
* Only ``approve_baseline`` changes a baseline; CI never calls it.
* A run is rejected unless its own status is PASS, it has no seeded failure, and (pytest) no
  unexpected skips.
* Replacing a baseline with a non-comparable run (dataset, corpus, or model change) needs
  ``accept_identity_change``; with a run that regresses against the current baseline needs
  ``accept_regression``. Both are written to the audit log with the reason, so a regression cannot
  disappear silently by becoming the new baseline. Earlier baselines stay on disk.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from ai_quality_lab.observability.records import (
    EvaluationType,
    blocking_reasons,
    canonical_json,
    utc_now,
    validate_record,
)
from ai_quality_lab.observability.regression import Rule, compare_records

DEFAULT_BASELINES = Path("reports") / "baselines"
MANIFEST = "manifest.json"
AUDIT = "audit.jsonl"
SAFE_NAME = re.compile(r"^[A-Za-z0-9._-]+$")


class BaselineError(ValueError):
    """A baseline is missing, corrupt, or an approval was rejected."""


def record_hash(record: Mapping[str, Any]) -> str:
    """Platform-independent hash (canonical JSON), so CRLF checkouts verify on Windows too."""
    return hashlib.sha256(canonical_json(record).encode("utf-8")).hexdigest()


def read_manifest(directory: str | Path) -> dict[str, Any]:
    path = Path(directory) / MANIFEST
    try:
        manifest = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise BaselineError(f"baseline manifest {path} does not exist") from None
    except (OSError, ValueError) as exc:
        raise BaselineError(f"baseline manifest {path} is unreadable: {exc}") from None
    if (not isinstance(manifest, dict) or manifest.get("schema_version") != 1
            or not isinstance(manifest.get("current"), dict)):
        raise BaselineError(f"baseline manifest {path} is not a version-1 manifest")
    return manifest


def load_baseline(directory: str | Path, evaluation_type: str) -> dict[str, Any]:
    """The current approved baseline for a type, verified against the manifest's hash."""
    directory = Path(directory)
    entry = read_manifest(directory)["current"].get(evaluation_type)
    if not isinstance(entry, dict):
        raise BaselineError(f"no approved baseline for {evaluation_type}")
    path = directory / str(entry.get("file", ""))
    try:
        record = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BaselineError(f"baseline file {path} is missing or unreadable: {exc}") from None
    errors = validate_record(record)
    if errors:
        raise BaselineError(f"baseline {path} is invalid: " + "; ".join(errors))
    if record_hash(record) != entry.get("sha256"):
        raise BaselineError(f"baseline {path} does not match the hash in the manifest "
                            "(edited or corrupted)")
    if record["evaluation_type"] != evaluation_type or record["run_id"] != entry.get("run_id"):
        raise BaselineError(f"baseline {path} does not match its manifest entry")
    return record


def load_baselines(directory: str | Path, types: list[str]) -> dict[str, Any]:
    """type -> record, or type -> BaselineError (so the caller reports ERROR, never PASS)."""
    result: dict[str, Any] = {}
    for etype in types:
        try:
            result[etype] = load_baseline(directory, etype)
        except BaselineError as exc:
            result[etype] = exc
    return result


def quality_blockers(record: Mapping[str, Any]) -> list[str]:
    """Why a record's *content* makes it unfit as a baseline, independent of its ``status``.

    Baseline safety must not rely only on the record builder (D-8.1): the release policy is
    recomputed here from the metrics and per-case outcomes.
    """
    from ai_quality_lab.rag.evaluate import RETRIEVAL_GATES

    etype, metrics = record["evaluation_type"], record["metrics"]
    reasons: list[str] = []
    if etype in ("custom_engine", "promptfoo") or etype.startswith("rag_"):
        if not record["cases"] and not metrics.get("case_count", {}).get("value"):
            reasons.append("no cases were executed")
    if etype in ("custom_engine", "promptfoo"):
        reasons += blocking_reasons(metrics)
    elif etype.startswith("rag_"):
        reasons += blocking_reasons(metrics, RETRIEVAL_GATES)
    elif etype == "pytest":
        for name in ("tests_failed", "unexpected_skips"):
            if metrics.get(name, {}).get("value"):
                reasons.append(f"{metrics[name]['value']} {name.replace('_', ' ')}")
        if "executed_tests" in metrics and not metrics["executed_tests"]["value"]:
            reasons.append("no tests were executed")
    errored = [c["id"] for c in record["cases"] if c["outcome"] == "ERROR"]
    critical = [c["id"] for c in record["cases"]
                if c["severity"] == "critical" and c["outcome"] != "PASS"]
    if errored:
        reasons.append(f"errored case(s) {errored}")
    if critical:
        reasons.append(f"critical case(s) not passing {critical}")
    return list(dict.fromkeys(reasons))


def _identity_change(previous: Mapping[str, Any], record: Mapping[str, Any]) -> dict[str, Any]:
    """Old and new identity fields and shared metric values, shown before an identity change."""
    keys = sorted(set(previous["identity"]) | set(record["identity"]))
    return {
        "identity": {k: [previous["identity"].get(k), record["identity"].get(k)] for k in keys
                     if previous["identity"].get(k) != record["identity"].get(k)},
        "metrics": {k: [previous["metrics"][k]["value"], record["metrics"][k]["value"]]
                    for k in sorted(set(previous["metrics"]) & set(record["metrics"]))
                    if previous["metrics"][k]["value"] != record["metrics"][k]["value"]},
    }


def describe_identity_change(change: Mapping[str, Any]) -> str:
    identity = "; ".join(f"{k}: {v[0]!r} -> {v[1]!r}" for k, v in change["identity"].items())
    metrics = "; ".join(f"{k}: {v[0]} -> {v[1]}" for k, v in change["metrics"].items())
    return f"identity [{identity}]; metric differences [{metrics or 'none'}]"


def audit_problems(directory: str | Path) -> list[str]:
    """Every manifest entry must match an audit-log approval (same run, file, and hash), and the
    latest approval logged for each type must be the manifest's current baseline."""
    directory = Path(directory)
    try:
        manifest = read_manifest(directory)["current"]
        lines = (directory / AUDIT).read_text(encoding="utf-8").splitlines()
        audit = [json.loads(line) for line in lines if line.strip()]
    except (OSError, ValueError, BaselineError) as exc:
        return [f"cannot read manifest or audit log: {exc}"]
    problems = []
    for etype, entry in sorted(manifest.items()):
        approvals = [a for a in audit if isinstance(a, dict) and a.get("evaluation_type") == etype]
        if not approvals:
            problems.append(f"{etype}: no approval in the audit log")
            continue
        last = approvals[-1]
        if (last.get("run_id"), last.get("file"), last.get("sha256")) != (
                entry.get("run_id"), entry.get("file"), entry.get("sha256")):
            problems.append(f"{etype}: manifest entry {entry.get('run_id')} does not match the "
                            f"latest audit-log approval {last.get('run_id')}")
    return problems


def approve_baseline(directory: str | Path, record: Mapping[str, Any], *, approved_by: str,
                     reason: str, rules: Mapping[str, Rule], accept_identity_change: bool = False,
                     accept_regression: bool = False, allow_uncommitted: bool = False,
                     now: str | None = None) -> dict[str, Any]:
    """Approve ``record`` as the new baseline for its type. Returns the audit entry."""
    directory = Path(directory)
    errors = validate_record(record)
    if errors:
        raise BaselineError("record is invalid: " + "; ".join(errors))
    if not approved_by.strip() or not reason.strip():
        raise BaselineError("approval needs --approved-by and a --reason")
    etype, run_id = record["evaluation_type"], record["run_id"]
    if not SAFE_NAME.match(run_id) or etype not in {t.value for t in EvaluationType}:
        raise BaselineError(f"run_id {run_id!r} cannot be used as a file name")
    if record["status"] != "PASS":
        raise BaselineError(f"only a PASS run can be a baseline; {run_id} is {record['status']}")
    seeded = record["parameters"].get("simulated_failure")
    if seeded not in (None, "none"):
        raise BaselineError(f"{run_id} ran with simulated failure {seeded!r}; a seeded-defect "
                            "run can never be a baseline")
    unexpected = record["metrics"].get("unexpected_skips", {}).get("value", 0)
    if unexpected:
        raise BaselineError(f"{run_id} has {unexpected} unexpected skipped test(s)")
    blockers = quality_blockers(record)
    if blockers:
        raise BaselineError(f"{run_id} cannot be a baseline whatever its status says: "
                            + "; ".join(blockers))
    flags: list[str] = []
    source = record["source"]
    if source.get("commit") is None or source.get("uncommitted_changes") is True:
        problem = ("unknown commit" if source.get("commit") is None
                   else f"uncommitted changes on top of {source.get('commit')}")
        if not allow_uncommitted:
            raise BaselineError(f"{run_id} was recorded with {problem}: it cannot be reproduced "
                                "from Git. Record a run on a committed, clean state, or use "
                                "--allow-uncommitted deliberately (it is written to the audit "
                                "log)")
        flags.append("uncommitted_source")

    manifest_path = directory / MANIFEST
    manifest = read_manifest(directory) if manifest_path.exists() else \
        {"schema_version": 1, "current": {}}
    previous = None
    identity_change = None
    if etype in manifest["current"]:
        previous = load_baseline(directory, etype)  # a corrupt current baseline stops approval
        if previous["run_id"] == run_id:
            raise BaselineError(f"{run_id} is already the approved {etype} baseline")
        comparison = compare_records(previous, record, rules)
        if not comparison.comparable:
            identity_change = _identity_change(previous, record)
            if not identity_change["identity"]:  # not comparable for another reason
                raise BaselineError("comparison with the current baseline failed: " + " | ".join(
                    f.reason for f in comparison.findings if f.action == "error"))
            if not accept_identity_change:
                raise BaselineError("run is not comparable with the current baseline, so no "
                                    "regression can be computed: "
                                    + describe_identity_change(identity_change)
                                    + ". Review these differences, then use "
                                    "--accept-identity-change to approve deliberately")
            flags.append("identity_change")
        elif comparison.status == "ERROR":
            raise BaselineError("comparison with the current baseline failed: " + " | ".join(
                f.reason for f in comparison.findings if f.action == "error"))
        if any(f.action == "block" for f in comparison.findings):
            if not accept_regression:
                raise BaselineError("run regresses against the current baseline: " + " | ".join(
                    f.reason for f in comparison.findings if f.action == "block")
                    + " (use --accept-regression to approve deliberately)")
            flags.append("regression_accepted")

    relative = f"{etype}/{run_id}.json"
    target = directory / relative
    if target.exists():
        raise BaselineError(f"{target} already exists; baseline files are never overwritten")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(record, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
                      encoding="utf-8", newline="\n")
    entry = {"action": "approve", "evaluation_type": etype, "run_id": run_id, "file": relative,
             "sha256": record_hash(record), "commit": record["source"].get("commit"),
             "previous_run_id": previous["run_id"] if previous else None,
             "approved_at": now or utc_now(), "approved_by": approved_by.strip(),
             "reason": reason.strip(), "flags": flags, "identity_change": identity_change}
    manifest["current"][etype] = {k: entry[k] for k in ("run_id", "file", "sha256", "commit",
                                                        "approved_at", "approved_by", "reason")}
    temporary = manifest_path.with_suffix(".tmp")
    temporary.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8",
                         newline="\n")
    os.replace(temporary, manifest_path)
    with (directory / AUDIT).open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(canonical_json(entry) + "\n")
    return entry
