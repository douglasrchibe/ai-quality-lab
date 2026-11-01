"""Tests for the append-only JSONL history: strict reading, duplicates, corruption."""

import json

import pytest

from ai_quality_lab.observability.history import HistoryError, append_records, read_history


def test_append_and_read_round_trip_with_lf_line_endings(tmp_path, make_record):
    path = tmp_path / "nested" / "history.jsonl"
    append_records(path, [make_record(run_id="a"), make_record(run_id="b")])
    append_records(path, [make_record(run_id="c")])
    assert [r["run_id"] for r in read_history(path)] == ["a", "b", "c"]
    assert b"\r\n" not in path.read_bytes()


def test_blank_lines_are_ignored(tmp_path, make_record):
    path = tmp_path / "h.jsonl"
    path.write_text("\n" + json.dumps(make_record()) + "\n\n", encoding="utf-8")
    assert len(read_history(path)) == 1


def test_missing_history_is_an_error(tmp_path):
    with pytest.raises(HistoryError, match="does not exist"):
        read_history(tmp_path / "none.jsonl")


def test_malformed_and_invalid_lines_are_reported_with_line_numbers(tmp_path, make_record):
    path = tmp_path / "h.jsonl"
    bad = make_record(run_id="bad")
    del bad["status"]
    path.write_text("\n".join([json.dumps(make_record(run_id="ok")), "{not json",
                               json.dumps(bad)]) + "\n", encoding="utf-8")
    with pytest.raises(HistoryError) as info:
        read_history(path)
    problems = info.value.problems
    assert problems[0].startswith("line 2: malformed JSON")
    assert problems[1].startswith("line 3:") and "status" in problems[1]


def test_duplicate_run_ids_in_file_are_rejected(tmp_path, make_record):
    path = tmp_path / "h.jsonl"
    line = json.dumps(make_record(run_id="same"))
    path.write_text(f"{line}\n{line}\n", encoding="utf-8")
    with pytest.raises(HistoryError, match="duplicate run_id 'same' \\(first on line 1\\)"):
        read_history(path)


def test_append_rejects_duplicates_and_invalid_records(tmp_path, make_record):
    path = tmp_path / "h.jsonl"
    append_records(path, [make_record(run_id="a")])
    with pytest.raises(HistoryError, match="duplicate"):
        append_records(path, [make_record(run_id="a")])
    with pytest.raises(HistoryError, match="duplicate"):
        append_records(path, [make_record(run_id="b"), make_record(run_id="b")])
    invalid = make_record(run_id="c", status="MAYBE")
    with pytest.raises(HistoryError, match="status"):
        append_records(path, [invalid])
    assert [r["run_id"] for r in read_history(path)] == ["a"]  # nothing partial was written


def test_append_refuses_to_extend_a_corrupt_history(tmp_path, make_record):
    path = tmp_path / "h.jsonl"
    path.write_text("{corrupt\n", encoding="utf-8")
    with pytest.raises(HistoryError, match="malformed"):
        append_records(path, [make_record()])
    assert path.read_text(encoding="utf-8") == "{corrupt\n"


def test_incompatible_record_version_is_rejected(tmp_path, make_record):
    path = tmp_path / "h.jsonl"
    path.write_text(json.dumps({**make_record(), "schema_version": 99}) + "\n", encoding="utf-8")
    with pytest.raises(HistoryError, match="unsupported schema_version 99"):
        read_history(path)
