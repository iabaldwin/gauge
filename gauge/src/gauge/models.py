"""Versioned records shared by capture, dispatch, and future backend graders."""

from __future__ import annotations

import json
from collections import Counter
from dataclasses import asdict, dataclass, field
from typing import Any, Literal, Protocol

from gauge.usage import TOKEN_METRICS, TokenUsage, normalize_usage


@dataclass(frozen=True)
class Case:
    """An input, never an implicitly correct reference answer."""

    id: str
    input: str
    provenance: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ModelOutput:
    text: str | None
    status: Literal["completed", "incomplete", "refused", "unsupported"] = "completed"
    request: dict[str, Any] = field(default_factory=dict)
    response: dict[str, Any] = field(default_factory=dict)
    usage: dict[str, Any] = field(default_factory=dict)

    @property
    def token_usage(self) -> TokenUsage:
        """Normalized counters alongside the unchanged provider-native usage."""
        return normalize_usage(
            self.usage,
            text_only=self.status in {"completed", "incomplete"} and self.text is not None,
            answer_text=self.text,
        )


class Target(Protocol):
    """Backend-owned adapter. Implementations must support concurrent calls."""

    name: str

    def describe(self) -> dict[str, Any]: ...

    async def invoke(self, case: Case, *, instructions: str) -> ModelOutput: ...


@dataclass(frozen=True)
class Outcome:
    case_id: str
    target: str
    latency_ms: float
    output: ModelOutput | None = None
    error: str | None = None
    error_status_code: int | None = None

    @property
    def comparable(self) -> bool:
        return (
            self.error is None
            and self.output is not None
            and self.output.status == "completed"
            and self.output.text is not None
        )


@dataclass(frozen=True)
class Grade:
    """A separate evaluator opinion; neither model output is a truth label."""

    evaluator: str
    version: str
    metric: str
    value: float | None
    status: Literal["completed", "not_scored", "error"] = "completed"
    rationale: str | None = None
    metadata: dict[str, Any] = field(default_factory=dict)


class Grader(Protocol):
    async def grade(
        self, *, case: Case, instructions: str, reference: Outcome, candidate: Outcome
    ) -> Grade: ...


@dataclass(frozen=True)
class Comparison:
    case_id: str
    candidate: str
    agreement: Grade
    assessment: Grade | None = None


@dataclass(frozen=True)
class Report:
    id: str
    created_at: str
    instructions: str
    reference: str
    targets: dict[str, dict[str, Any]]
    cases: list[Case]
    outcomes: list[Outcome]
    comparisons: list[Comparison]
    schema_version: str = "0.1"
    reference_is_ground_truth: bool = False
    metadata: dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Report:
        """Load native reports, including older reports without normalized token fields."""
        if data.get("schema_version", "0.1") != "0.1":
            raise ValueError("Unsupported report schema version.")
        outcomes = []
        for item in data["outcomes"]:
            raw = item.get("output")
            output = (
                None
                if raw is None
                else ModelOutput(
                    text=raw.get("text"),
                    status=raw.get("status", "completed"),
                    request=raw.get("request", {}),
                    response=raw.get("response", {}),
                    usage=raw.get("usage", {}),
                )
            )
            outcomes.append(
                Outcome(
                    case_id=item["case_id"],
                    target=item["target"],
                    latency_ms=item["latency_ms"],
                    output=output,
                    error=item.get("error"),
                    error_status_code=item.get("error_status_code"),
                )
            )
        comparisons = [
            Comparison(
                case_id=item["case_id"],
                candidate=item["candidate"],
                agreement=Grade(**item["agreement"]),
                assessment=Grade(**item["assessment"]) if item.get("assessment") else None,
            )
            for item in data["comparisons"]
        ]
        return cls(
            id=data["id"],
            created_at=data["created_at"],
            instructions=data["instructions"],
            reference=data["reference"],
            targets=data["targets"],
            cases=[Case(**item) for item in data["cases"]],
            outcomes=outcomes,
            comparisons=comparisons,
            metadata=data.get("metadata", {}),
            reference_is_ground_truth=data.get("reference_is_ground_truth", False),
        )

    def to_html(self) -> str:
        """Build a standalone local report. Call from a backend/reporting job."""
        from gauge.html import render_report

        return render_report(self)

    def to_dict(self) -> dict[str, Any]:
        result = asdict(self)
        for outcome, serialized in zip(self.outcomes, result["outcomes"], strict=True):
            if outcome.output is not None:
                serialized["output"]["token_usage"] = asdict(outcome.output.token_usage)
        result["token_summary"] = self.token_summary()
        return result

    def to_json(self, *, indent: int | None = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, allow_nan=False)

    def summary(self) -> dict[str, dict[str, int | float | None]]:
        """Agreement denominators exclude failures, which remain explicit counts."""
        result = {}
        for name in self.targets:
            if name == self.reference:
                continue
            pairs = [item for item in self.comparisons if item.candidate == name]
            scores = [item.agreement.value for item in pairs if item.agreement.value is not None]
            result[name] = {
                "total": len(pairs),
                "scored": len(scores),
                "not_scored": len(pairs) - len(scores),
                "baseline_agreement": sum(scores) / len(scores) if scores else None,
                "accuracy": None,
            }
        return result

    def token_summary(self) -> dict[str, dict[str, Any]]:
        """All dispatched calls, including partial/failed calls; expose coverage.

        Totals are null if any call lacks that counter. Known subtotals are
        reported separately so partial coverage cannot masquerade as a total.
        Dataset generation and preflight calls are outside this report.
        """
        result: dict[str, dict[str, Any]] = {}
        for name in self.targets:
            outcomes = [item for item in self.outcomes if item.target == name]
            usages = [item.output.token_usage if item.output else TokenUsage() for item in outcomes]
            totals: dict[str, int | None] = {}
            known_totals: dict[str, int | None] = {}
            coverage: dict[str, int] = {}
            for metric in TOKEN_METRICS:
                values = [getattr(usage, metric) for usage in usages]
                known = [value for value in values if value is not None]
                known_totals[metric] = sum(known) if known else None
                totals[metric] = sum(known) if known and len(known) == len(values) else None
                coverage[metric] = len(known)
            result[name] = {
                **totals,
                "calls": len(outcomes),
                "known_totals": known_totals,
                "known_calls": coverage,
                "calls_with_usage_issues": sum(bool(usage.issues) for usage in usages),
            }
        return result

    def diagnostics(self) -> dict[str, dict[str, Any]]:
        """Explain unscored calls without storing provider error bodies."""
        result = {}
        for name, target in self.targets.items():
            outcomes = [item for item in self.outcomes if item.target == name]
            statuses = Counter(
                item.error or (item.output.status if item.output else "missing_output")
                for item in outcomes
            )
            http_statuses = Counter(
                str(item.error_status_code)
                for item in outcomes
                if item.error_status_code is not None
            )
            result[name] = {
                "model": target.get("model"),
                "statuses": dict(statuses),
                "http_statuses": dict(http_statuses),
            }
            if statuses.get("NotFoundError"):
                result[name]["hint"] = (
                    "Model or endpoint not found, inaccessible, or not deployed. "
                    "A catalog listing does not establish serverless availability."
                )
        return result
