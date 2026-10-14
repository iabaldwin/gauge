"""Gauge: model-call capture and reference/candidate evaluation primitives."""

from importlib import import_module
from typing import TYPE_CHECKING, Any

from gauge.capture import CaptureBuffer, CaptureEvent, captures, wrap

if TYPE_CHECKING:
    from gauge.experiment import Experiment
    from gauge.grading import ExactMatch
    from gauge.models import Case, Comparison, Grade, Grader, ModelOutput, Outcome, Report, Target
    from gauge.providers import OpenAITarget
    from gauge.synthetic import generate_cases
    from gauge.usage import TokenUsage, normalize_usage

_LAZY = {
    "Experiment": "experiment",
    "ExactMatch": "grading",
    "Case": "models",
    "Comparison": "models",
    "Grade": "models",
    "Grader": "models",
    "ModelOutput": "models",
    "Outcome": "models",
    "Report": "models",
    "Target": "models",
    "OpenAITarget": "providers",
    "generate_cases": "synthetic",
    "TokenUsage": "usage",
    "normalize_usage": "usage",
}


def __getattr__(name: str) -> Any:
    if name not in _LAZY:
        raise AttributeError(name)
    value = getattr(import_module(f"gauge.{_LAZY[name]}"), name)
    globals()[name] = value
    return value


__version__ = "0.1.0a1"
__all__ = [
    "CaptureBuffer",
    "CaptureEvent",
    "Case",
    "Comparison",
    "ExactMatch",
    "Experiment",
    "Grade",
    "Grader",
    "ModelOutput",
    "OpenAITarget",
    "Outcome",
    "Report",
    "Target",
    "TokenUsage",
    "captures",
    "generate_cases",
    "normalize_usage",
    "wrap",
]
