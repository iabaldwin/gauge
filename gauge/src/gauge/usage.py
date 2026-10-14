"""Normalize token counters without treating hidden/formatting tokens as answers."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

TOKEN_METRICS = (
    "input_tokens",
    "total_output_tokens",
    "reasoning_tokens",
    "final_answer_tokens",
    "non_reasoning_output_tokens",
    "unattributed_output_tokens",
)


@dataclass(frozen=True)
class TokenUsage:
    """Nullable counts; final answer means the answer channel, including partial text."""

    input_tokens: int | None = None
    total_output_tokens: int | None = None
    reasoning_tokens: int | None = None
    final_answer_tokens: int | None = None
    non_reasoning_output_tokens: int | None = None
    unattributed_output_tokens: int | None = None
    sources: dict[str, str] = field(default_factory=dict)
    issues: tuple[str, ...] = ()


def normalize_usage(
    usage: Mapping[str, Any],
    *,
    text_only: bool = False,
    answer_text: str | None = None,
) -> TokenUsage:
    """Read native Responses/Chat counters. Missing is unknown, not zero.

    `text_only` asserts that this call produces only a final-answer text channel
    alongside reasoning. Do not enable for tool calls, audio, or mixed content.
    No tokenizer or character-count estimates are made.
    """
    sources: dict[str, str] = {}
    issues: list[str] = []

    def counter(metric: str, *paths: str) -> int | None:
        found: list[tuple[str, int]] = []
        for path in paths:
            value: Any = usage
            for part in path.split("."):
                value = value.get(part) if isinstance(value, Mapping) else None
            if value is None:
                continue
            if type(value) is not int or value < 0:
                issues.append(f"Invalid counter: {path}")
                continue
            found.append((path, value))
        if not found:
            return None
        if len({value for _, value in found}) != 1:
            issues.append(f"Conflicting counters: {metric}")
            return None
        sources[metric] = ", ".join(path for path, _ in found)
        return found[0][1]

    inputs = counter("input_tokens", "input_tokens", "prompt_tokens")
    total = counter("total_output_tokens", "output_tokens", "completion_tokens")
    reasoning = counter(
        "reasoning_tokens",
        "output_tokens_details.reasoning_tokens",
        "completion_tokens_details.reasoning_tokens",
    )
    answer = None
    if text_only:
        answer = counter(
            "final_answer_tokens",
            "output_tokens_details.text_tokens",
            "completion_tokens_details.text_tokens",
        )
        # A known empty answer channel has zero visible answer tokens, even when
        # reasoning exhausts the budget. None means the channel is not known.
        if answer is None and answer_text == "" and not issues:
            answer = 0
            sources["final_answer_tokens"] = "empty_answer_channel"

    non_reasoning, unattributed = None, None
    if total is not None and reasoning is not None:
        if reasoning <= total:
            non_reasoning = total - reasoning
            sources["non_reasoning_output_tokens"] = "total_output_tokens - reasoning_tokens"
        else:
            issues.append("reasoning_tokens exceeds total_output_tokens")
    if total is not None and answer is not None and answer > total:
        issues.append("final_answer_tokens exceeds total_output_tokens")
    if non_reasoning is not None and answer is not None:
        if answer <= non_reasoning:
            unattributed = non_reasoning - answer
            sources["unattributed_output_tokens"] = (
                "total_output_tokens - reasoning_tokens - final_answer_tokens"
            )
        else:
            issues.append("reasoning_tokens + final_answer_tokens exceeds total_output_tokens")

    return TokenUsage(
        input_tokens=inputs,
        total_output_tokens=total,
        reasoning_tokens=reasoning,
        final_answer_tokens=answer,
        non_reasoning_output_tokens=non_reasoning,
        unattributed_output_tokens=unattributed,
        sources=sources,
        issues=tuple(issues),
    )
