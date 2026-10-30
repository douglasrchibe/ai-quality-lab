"""AI Quality Lab: an evaluation framework for a mock guardrailed chatbot."""

from ai_quality_lab.dataset import DatasetValidationError, EvalCase, load_dataset
from ai_quality_lab.evaluator import CaseOutcome, EvaluationSummary, ReleaseReadiness
from ai_quality_lab.guardrails import (
    GuardrailConfig,
    InvalidGuardrailConfigError,
    KeywordTopicDetector,
)
from ai_quality_lab.mock_agent import ChatResponse, MockChatbot, ResponseOutcome, SimulatedFailure

# runner is intentionally not imported here: it is also run as a script
# (python -m ai_quality_lab.runner), and importing it from the package would load it twice.

__version__ = "0.1.0"

__all__ = [
    "CaseOutcome",
    "ChatResponse",
    "DatasetValidationError",
    "EvalCase",
    "EvaluationSummary",
    "GuardrailConfig",
    "InvalidGuardrailConfigError",
    "KeywordTopicDetector",
    "MockChatbot",
    "ReleaseReadiness",
    "ResponseOutcome",
    "SimulatedFailure",
    "load_dataset",
]
