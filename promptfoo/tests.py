"""Promptfoo test generator: builds Promptfoo tests from the shared dataset.

Both evaluation engines read ``evals/guardrail_cases.json``; this file only converts it.
"""

from pathlib import Path

from ai_quality_lab.promptfoo_adapter import build_promptfoo_tests

DATASET = Path(__file__).resolve().parents[1] / "evals" / "guardrail_cases.json"


def generate_tests(config=None):
    """Return Promptfoo test dicts. Optional config key ``dataset`` overrides the dataset path."""
    dataset = (config or {}).get("dataset") or DATASET
    return build_promptfoo_tests(dataset)
