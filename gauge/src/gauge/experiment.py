"""Bounded concurrent dispatch with independent reference and candidate requests."""

from __future__ import annotations

import asyncio
import copy
import math
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from typing import TypedDict
from uuid import uuid4

from gauge.capture import suppress_capture
from gauge.grading import ExactMatch
from gauge.models import Case, Comparison, Grade, Grader, Outcome, Report, Target


class _GradeArguments(TypedDict):
    case: Case
    instructions: str
    reference: Outcome
    candidate: Outcome


class Experiment:
    """Backend API; application users only wrap their inference client."""

    def __init__(
        self,
        *,
        instructions: str,
        reference: Target,
        candidates: Sequence[Target],
        max_concurrency: int = 8,
        max_cases: int = 1000,
        max_calls: int = 10000,
        timeout: float = 60,
        grader: Grader | None = None,
    ) -> None:
        targets = [reference, *candidates]
        names = [target.name for target in targets]
        if not candidates or any(not name for name in names) or len(set(names)) != len(names):
            raise ValueError("Provide candidates and unique, nonempty target names.")
        if any(type(limit) is not int for limit in (max_concurrency, max_cases, max_calls)):
            raise ValueError("Concurrency, case, and call limits must be integers.")
        if max_concurrency < 2 or max_cases < 1 or max_calls < 1:
            raise ValueError("Concurrency must be >= 2; case and call limits must be positive.")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be finite and positive.")
        self.instructions = instructions
        self.reference = reference
        self.targets = targets
        self.max_concurrency = max_concurrency
        self.max_cases = max_cases
        self.max_calls = max_calls
        self.timeout = timeout
        self.grader = grader

    async def run(self, cases: Sequence[Case]) -> Report:
        """Validate before requests, then dispatch the same inputs to every target."""
        if not cases or len(cases) > self.max_cases:
            raise ValueError("Dataset must be nonempty and within max_cases.")
        ids = [case.id for case in cases]
        if any(not item for item in ids) or len(set(ids)) != len(ids):
            raise ValueError("Case IDs must be unique and nonempty.")
        if any(not isinstance(case.input, str) for case in cases):
            raise ValueError("This version supports text case inputs only.")
        if len(cases) * len(self.targets) > self.max_calls:
            raise ValueError("Experiment exceeds max_calls; nothing was dispatched.")
        cases = copy.deepcopy(list(cases))
        target_descriptions = {
            target.name: copy.deepcopy(target.describe()) for target in self.targets
        }
        jobs = iter((case, target) for case in cases for target in self.targets)
        outcomes: dict[tuple[str, str], Outcome] = {}

        async def worker() -> None:
            for case, target in jobs:
                started = time.perf_counter()
                output, error, error_status_code = None, None, None
                try:
                    with suppress_capture():
                        async with asyncio.timeout(self.timeout):
                            output = await target.invoke(
                                copy.deepcopy(case), instructions=self.instructions
                            )
                except Exception as exc:
                    # Do not serialize provider exception bodies, which can contain secrets.
                    error = type(exc).__name__
                    status_code = getattr(exc, "status_code", None)
                    if type(status_code) is int:
                        error_status_code = status_code
                outcomes[case.id, target.name] = Outcome(
                    case.id,
                    target.name,
                    (time.perf_counter() - started) * 1000,
                    output,
                    error,
                    error_status_code,
                )

        async with asyncio.TaskGroup() as group:
            for _ in range(min(self.max_concurrency, len(cases) * len(self.targets))):
                group.create_task(worker())

        comparisons: dict[tuple[str, str], Comparison] = {}
        pairs = iter((case, target) for case in cases for target in self.targets[1:])

        async def grade_worker() -> None:
            for case, target in pairs:
                args: _GradeArguments = dict(
                    case=copy.deepcopy(case),
                    instructions=self.instructions,
                    reference=copy.deepcopy(outcomes[case.id, self.reference.name]),
                    candidate=copy.deepcopy(outcomes[case.id, target.name]),
                )
                agreement = await ExactMatch().grade(**args)
                assessment = None
                if self.grader is not None:
                    try:
                        with suppress_capture():
                            async with asyncio.timeout(self.timeout):
                                assessment = await self.grader.grade(**args)
                    except Exception as exc:
                        assessment = Grade(
                            type(self.grader).__name__,
                            "unknown",
                            "assessment",
                            None,
                            status="error",
                            rationale=type(exc).__name__,
                        )
                comparisons[case.id, target.name] = Comparison(
                    case.id,
                    target.name,
                    agreement,
                    assessment,
                )

        async with asyncio.TaskGroup() as group:
            for _ in range(min(self.max_concurrency, len(cases) * (len(self.targets) - 1))):
                group.create_task(grade_worker())

        return Report(
            id=f"run_{uuid4().hex}",
            created_at=datetime.now(UTC).isoformat(),
            instructions=self.instructions,
            reference=self.reference.name,
            targets=target_descriptions,
            cases=list(cases),
            outcomes=[outcomes[case.id, target.name] for case in cases for target in self.targets],
            comparisons=[
                comparisons[case.id, target.name] for case in cases for target in self.targets[1:]
            ],
        )
