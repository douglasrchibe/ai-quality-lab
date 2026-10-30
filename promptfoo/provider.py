"""Promptfoo Python provider entry point for the mock chatbot.

Kept deliberately thin: the logic lives in ``ai_quality_lab.promptfoo_adapter`` where it is
unit-tested. Promptfoo runs this file with the project's virtual-environment Python (set by
``promptfoo/run.mjs``), where ``ai_quality_lab`` is installed.
"""

from ai_quality_lab.promptfoo_adapter import call_api

__all__ = ["call_api"]
