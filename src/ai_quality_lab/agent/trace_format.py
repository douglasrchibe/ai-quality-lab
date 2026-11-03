"""The serialized agent-trace format, versions 1–3, and one upgrade path to the current version.

* v1 (Milestone 11.1): no ``trace_version`` key.
* v2: adds ``run_id``, ``scenario_id`` and ``run_id_source`` (run and steps), plus per-step
  ``retry`` and ``execution``.
* v3: removes the test-only policy switch from the runtime. The run's ``policy_enforcement`` and
  each step's ``unauthorized_execution`` are gone: whether an action was authorized is decided by
  the independent evaluator from evidence, never from a runtime flag.

``upgrade_trace`` reads any of the three and returns the v3 shape. Fields a version can't provide
are set to ``None`` and listed in ``missing_evidence``, so a consumer can refuse to pass a case on
evidence that was never recorded. Values of removed v1/v2 fields are kept under ``legacy``. Pure
data in, pure data out.
"""

from __future__ import annotations

import copy
from collections.abc import Mapping
from typing import Any

TRACE_VERSION = 3
SUPPORTED_VERSIONS = (1, 2, 3)
V2_RUN_FIELDS = ("run_id", "scenario_id", "run_id_source")
V2_STEP_FIELDS = ("run_id", "scenario_id", "retry", "execution")
REMOVED_IN_V3 = {"run": ("policy_enforcement",), "step": ("unauthorized_execution",)}


class TraceFormatError(ValueError):
    """A trace that isn't any supported version."""


def trace_version(data: Mapping[str, Any]) -> int:
    """The version of a serialized trace; v1 traces carry no version key."""
    if not isinstance(data, Mapping) or not isinstance(data.get("steps"), list):
        raise TraceFormatError("not an agent trace: expected an object with a 'steps' list")
    version = data.get("trace_version", 1)
    if version not in SUPPORTED_VERSIONS:
        raise TraceFormatError(f"unsupported trace_version {version!r}; "
                               f"supported: {list(SUPPORTED_VERSIONS)}")
    return version


def upgrade_trace(data: Mapping[str, Any]) -> dict[str, Any]:
    """Return a v3-shaped copy of a v1, v2 or v3 trace (the input is not modified)."""
    version = trace_version(data)
    trace = copy.deepcopy(dict(data))
    missing: list[str] = []
    legacy: dict[str, Any] = {}

    if version < 2:
        for name in V2_RUN_FIELDS:
            trace[name] = None
            missing.append(name)
        for step in trace["steps"]:
            for name in V2_STEP_FIELDS:
                step[name] = None
        missing.extend(f"steps[].{name}" for name in V2_STEP_FIELDS)
    if version < 3:
        for name in REMOVED_IN_V3["run"]:
            if name in trace:
                legacy[name] = trace.pop(name)
        flagged = [s["index"] for s in trace["steps"] if s.pop("unauthorized_execution", False)]
        legacy["unauthorized_execution_steps"] = flagged

    trace["trace_version"] = TRACE_VERSION
    trace["source_trace_version"] = version
    trace["missing_evidence"] = missing
    trace["legacy"] = legacy
    return trace
