"""Agreement today; independently versioned backend grading later."""

from __future__ import annotations

from gauge.models import Case, Grade, Outcome


class ExactMatch:
    """Literal output equality, without stripping or silently normalizing labels."""

    async def grade(
        self, *, case: Case, instructions: str, reference: Outcome, candidate: Outcome
    ) -> Grade:
        if not reference.comparable or not candidate.comparable:
            return Grade(
                "exact_match",
                "1",
                "baseline_agreement",
                None,
                status="not_scored",
                rationale="Both outputs must be complete text responses.",
            )
        assert reference.output is not None and candidate.output is not None
        return Grade(
            "exact_match",
            "1",
            "baseline_agreement",
            float(reference.output.text == candidate.output.text),
            rationale="Literal equality with the reference; not a correctness judgment.",
        )
