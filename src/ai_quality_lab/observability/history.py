"""Append-only JSONL history of evaluation records, read strictly.

One record per line, written with ``\\n`` line endings on every platform. Reading never skips a
bad line: a malformed, invalid, or duplicate record makes the whole history unusable
(``HistoryError``), so a trend can never be computed silently from partial data.
"""

from __future__ import annotations

import json
from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from ai_quality_lab.observability.records import canonical_json, validate_record

DEFAULT_HISTORY = Path("reports") / "history" / "quality_history.jsonl"


class HistoryError(ValueError):
    """The history file is missing, unreadable, or contains invalid records."""

    def __init__(self, problems: Sequence[str]):
        self.problems = list(problems)
        super().__init__("; ".join(self.problems))


def read_history(path: str | Path) -> list[dict[str, Any]]:
    """All records in file order. Raises ``HistoryError`` listing every problem found."""
    path = Path(path)
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        raise HistoryError([f"history file {path} does not exist"]) from None
    except (OSError, UnicodeDecodeError) as exc:
        raise HistoryError([f"history file {path} is unreadable: {exc}"]) from None
    records: list[dict[str, Any]] = []
    problems: list[str] = []
    seen: dict[str, int] = {}
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except ValueError as exc:
            problems.append(f"line {number}: malformed JSON ({exc.msg})")
            continue
        errors = validate_record(record)
        if errors:
            problems.append(f"line {number}: " + "; ".join(errors))
            continue
        run_id = record["run_id"]
        if run_id in seen:
            problems.append(f"line {number}: duplicate run_id {run_id!r} (first on line "
                            f"{seen[run_id]})")
            continue
        seen[run_id] = number
        records.append(record)
    if problems:
        raise HistoryError(problems)
    return records


def append_records(path: str | Path, records: Iterable[dict[str, Any]]) -> None:
    """Validate, then append. Refuses to extend a history that is already corrupt."""
    path = Path(path)
    existing = read_history(path) if path.exists() else []
    known = {r["run_id"] for r in existing}
    new = list(records)
    problems = []
    for record in new:
        errors = validate_record(record)
        if errors:
            problems.append(f"record {record.get('run_id') if isinstance(record, dict) else '?'}"
                            f": " + "; ".join(errors))
        elif record["run_id"] in known:
            problems.append(f"duplicate run_id {record['run_id']!r}")
        else:
            known.add(record["run_id"])
    if problems:
        raise HistoryError(problems)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        for record in new:
            handle.write(canonical_json(record) + "\n")
