"""Console output for the PASS / BLOCKED / ERROR command-line tools.

On Windows, stdout and stderr default to the legacy code page (cp1252) when they are pipes. Printing
a report status symbol then raises ``UnicodeEncodeError``, and an uncaught exception exits with
code 1, which callers read as BLOCKED. A crash must be ERROR (2), never BLOCKED or PASS.
"""

from __future__ import annotations

import io
import sys
import traceback

EXIT_ERROR = 2


def use_utf8_output() -> None:
    """Write stdout and stderr as UTF-8 so report symbols print on any console or pipe."""
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except (ValueError, io.UnsupportedOperation):
            pass  # a stream that can't change encoding keeps its own; errors still exit 2 below


def unexpected_error(tool: str) -> int:
    """Report an unexpected exception and return ERROR, never the BLOCKED exit code."""
    print(f"ERROR: {tool} failed unexpectedly (this is a tool error, not a quality verdict):",
          file=sys.stderr)
    traceback.print_exc()
    return EXIT_ERROR
