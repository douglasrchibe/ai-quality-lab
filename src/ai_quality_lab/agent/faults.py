"""Scripted tool faults for stage 6, so failure handling is tested deterministically.

``TIMEOUT_AFTER_EFFECT`` models the hard case: the action happened, but the caller saw a timeout.
A retry with a new idempotency key then creates a duplicate. The other faults have no effect:
``TIMEOUT_NO_EFFECT`` and ``TOOL_ERROR`` fail before the action, and ``MALFORMED_RESULT`` returns
unusable output instead of a result.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum


class Fault(str, Enum):
    TIMEOUT_NO_EFFECT = "timeout_no_effect"
    TIMEOUT_AFTER_EFFECT = "timeout_after_effect"
    TOOL_ERROR = "tool_error"
    MALFORMED_RESULT = "malformed_result"


@dataclass(frozen=True)
class FaultPlan:
    """``per_call[tool][i]`` is the fault for that tool's i-th execution (``None`` = healthy);
    ``always[tool]`` applies to every execution of that tool."""
    per_call: Mapping[str, Sequence[Fault | None]] = field(default_factory=dict)
    always: Mapping[str, Fault] = field(default_factory=dict)
    error_text: str = "the service returned an error"  # a tool's own message: untrusted

    def fault_for(self, tool: str, call_index: int) -> Fault | None:
        if tool in self.always:
            return self.always[tool]
        script = self.per_call.get(tool, ())
        return script[call_index] if call_index < len(script) else None


NO_FAULTS = FaultPlan()
