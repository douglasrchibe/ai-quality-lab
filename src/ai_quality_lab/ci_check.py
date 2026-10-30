"""Run one CI quality check and record a machine-readable status.

    python -m ai_quality_lab.ci_check --name custom-engine --codes project --expect pass \\
        --out ci-artifacts/custom-engine -- python -m ai_quality_lab.runner

The command's own exit code is mapped to an outcome using the tool's documented convention:

==========  =========  ===========  ===================================
``--codes`` PASS       BLOCKED      ERROR
==========  =========  ===========  ===================================
project     0          1            anything else (2 = setup error)
pytest      0          1 (failures) anything else (usage, no tests...)
promptfoo   0          100          anything else
pip         0          non-zero     command could not run
==========  =========  ===========  ===================================

``--expect`` states the outcome that counts as success for this check: ``pass`` for normal
checks, ``blocked`` for deliberate failure demonstrations (which must keep blocking). This wrapper
exits 0 when the expectation is met, 1 when the check produced a quality outcome other than
expected, and 2 when the check could not run (ERROR). A status JSON and a full log are always
written, so artifacts exist even when the check fails.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
import time
from collections.abc import Sequence
from pathlib import Path

CODE_MAPS = {
    "project": {0: "PASS", 1: "BLOCKED"},
    "pytest": {0: "PASS", 1: "BLOCKED"},
    "promptfoo": {0: "PASS", 100: "BLOCKED"},
}
OUTCOMES = ("PASS", "BLOCKED", "ERROR")


def classify(exit_code: int | None, codes: str) -> str:
    """Map an exit code to PASS / BLOCKED / ERROR. ``None`` means the command could not start."""
    if exit_code is None:
        return "ERROR"
    if codes == "pip":
        return "PASS" if exit_code == 0 else "BLOCKED"
    return CODE_MAPS[codes].get(exit_code, "ERROR")


def wrapper_exit_code(outcome: str, expected: str) -> int:
    if outcome == expected:
        return 0
    return 2 if outcome == "ERROR" else 1


def run_check(name: str, command: Sequence[str], codes: str, expect: str, out_dir: Path,
              stdout_file: Path | None = None) -> dict:
    out_dir.mkdir(parents=True, exist_ok=True)
    log_path = out_dir / f"{name}.log"
    start = time.perf_counter()
    executable = shutil.which(command[0]) or command[0]  # resolves npm.cmd etc. on Windows
    try:
        completed = subprocess.run([executable, *command[1:]], capture_output=True, text=True,
                                   encoding="utf-8", errors="replace")
        exit_code, stdout, stderr = completed.returncode, completed.stdout, completed.stderr
    except OSError as exc:
        exit_code, stdout, stderr = None, "", f"Could not start command: {exc}\n"
    duration = round(time.perf_counter() - start, 2)
    outcome = classify(exit_code, codes)
    expected = expect.upper()
    log_path.write_text(
        f"$ {' '.join(command)}\n--- stdout ---\n{stdout}\n--- stderr ---\n{stderr}",
        encoding="utf-8",
    )
    if stdout_file is not None:
        stdout_file.parent.mkdir(parents=True, exist_ok=True)
        stdout_file.write_text(stdout, encoding="utf-8")
    status = {
        "name": name,
        "command": list(command),
        "codes": codes,
        "exit_code": exit_code,
        "outcome": outcome,
        "expected": expected,
        "expectation_met": outcome == expected,
        "duration_s": duration,
        "log": log_path.name,
    }
    (out_dir / f"{name}.status.json").write_text(json.dumps(status, indent=2), encoding="utf-8")
    sys.stdout.write(stdout)
    sys.stderr.write(stderr)
    verdict = "OK" if status["expectation_met"] else "UNEXPECTED"
    print(f"[ci_check] {name}: exit={exit_code} outcome={outcome} expected={expected} -> {verdict}",
          flush=True)
    return status


def main(argv: Sequence[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if "--" not in argv:
        print("usage: ... -- <command> [args...]", file=sys.stderr)
        return 2
    split = argv.index("--")
    parser = argparse.ArgumentParser(prog="python -m ai_quality_lab.ci_check")
    parser.add_argument("--name", required=True)
    parser.add_argument("--codes", required=True, choices=[*CODE_MAPS, "pip"])
    parser.add_argument("--expect", default="pass", choices=["pass", "blocked"])
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--stdout-file", type=Path, help="also save the command's stdout here")
    args = parser.parse_args(argv[:split])
    command = argv[split + 1:]
    if not command:
        print("No command given after --", file=sys.stderr)
        return 2
    status = run_check(args.name, command, args.codes, args.expect, args.out, args.stdout_file)
    return wrapper_exit_code(status["outcome"], status["expected"])


if __name__ == "__main__":
    raise SystemExit(main())
