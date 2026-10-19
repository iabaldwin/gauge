"""Versioned capture envelope shared by SDK export and backend ingestion."""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any

SCHEMA_VERSION = "1"
MAX_BODY_BYTES = 65536
MAX_ENVELOPE_BYTES = 160000
MAX_DEPTH = 32
MAX_NODES = 8192


def decode_object(raw: bytes, *, limit: int = MAX_BODY_BYTES) -> dict[str, Any]:
    """Parse only bounded JSON, then bound the shape consumed by backend adapters."""
    if type(raw) is not bytes or len(raw) > limit:
        raise ValueError("Capture body exceeds byte limit")
    try:
        value = json.loads(raw)
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ValueError("Invalid capture JSON") from exc
    if type(value) is not dict:
        raise ValueError("Capture body must be an object")
    todo = [(value, 0)]
    nodes = 0
    while todo:
        item, depth = todo.pop()
        nodes += 1
        if nodes > MAX_NODES or depth > MAX_DEPTH:
            raise ValueError("Capture body exceeds structural limits")
        if type(item) is dict:
            todo.extend((child, depth + 1) for child in item.values())
        elif type(item) is list:
            todo.extend((child, depth + 1) for child in item)
        elif type(item) is float:
            import math

            if not math.isfinite(item):
                raise ValueError("Non-finite JSON number")
    return value


def replay_reason(operation: str, request: dict[str, Any], response: dict[str, Any]) -> str | None:
    """Conservative eligibility for text-only independent comparisons, not a grade."""
    if not isinstance(request.get("model"), str) or not request["model"]:
        return "missing_model"
    if any(request.get(key) for key in ("previous_response_id", "conversation", "tools", "prompt")):
        return "state_or_tools"
    if any(
        request.get(key)
        for key in ("audio", "modalities", "functions", "function_call", "web_search_options")
    ):
        return "unsupported_input"
    if request.get("stream") or request.get("background"):
        return "stream_or_background"
    messages = request.get("input") if operation == "responses.create" else request.get("messages")
    if not isinstance(messages, str):
        if not isinstance(messages, list) or not messages:
            return "unsupported_input"
        for message in messages:
            if not isinstance(message, dict) or message.get("role") not in {
                "user",
                "assistant",
                "system",
                "developer",
            }:
                return "unsupported_input"
            if message.get("tool_calls") or message.get("function_call"):
                return "state_or_tools"
            content = message.get("content")
            if isinstance(content, str):
                continue
            if not isinstance(content, list) or not content:
                return "unsupported_input"
            if any(
                not isinstance(part, dict)
                or part.get("type")
                not in {
                    "text",
                    "input_text",
                    "output_text",
                }
                or not isinstance(part.get("text"), str)
                for part in content
            ):
                return "unsupported_input"
    if operation == "responses.create":
        output = response.get("output")
        if response.get("status") != "completed" or not isinstance(output, list) or not output:
            return "incomplete_output"
        text_found = False
        for item in output:
            if not isinstance(item, dict):
                return "unsupported_output"
            if item.get("type") == "reasoning":
                continue
            if item.get("type") != "message" or not isinstance(item.get("content"), list):
                return "unsupported_output"
            for part in item["content"]:
                if not isinstance(part, dict) or part.get("type") != "output_text":
                    return "unsupported_output"
                if not isinstance(part.get("text"), str):
                    return "unsupported_output"
                text_found = True
        return None if text_found else "incomplete_output"
    if operation == "chat.completions.create":
        choices = response.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            return "unsupported_output"
        choice = choices[0]
        message = choice.get("message")
        if choice.get("finish_reason") != "stop" or not isinstance(message, dict):
            return "incomplete_output"
        if message.get("tool_calls") or message.get("function_call") or message.get("refusal"):
            return "unsupported_output"
        return None if isinstance(message.get("content"), str) else "incomplete_output"
    return "unsupported_operation"


@dataclass(frozen=True)
class CaptureEvent:
    """Immutable bytes only; never retains a native result or application model."""

    id: str
    operation: str
    request_json: bytes | None
    response_json: bytes | None
    status: str
    latency_ms: float
    created_at: float
    error: str | None = None
    omission_reason: str | None = None
    provider_request_id: str | None = None
    endpoint_host: str | None = None
    attempts: int = 0
    run_id: str | None = None
    process_id: str | None = None

    @property
    def size_bytes(self) -> int:
        # Reserve metadata/object overhead in addition to the retained wire bodies.
        return len(self.request_json or b"") + len(self.response_json or b"") + 2048

    @property
    def request(self) -> dict[str, Any]:
        if self.request_json is None:
            return {"capture_omitted": self.omission_reason or "unavailable"}
        return decode_object(self.request_json)

    @property
    def response(self) -> dict[str, Any] | None:
        return decode_object(self.response_json) if self.response_json is not None else None

    def to_dict(self) -> dict[str, Any]:
        request, response = self.request, self.response
        reason = self.omission_reason
        if reason is None:
            reason = (
                replay_reason(self.operation, request, response)
                if self.status == "completed" and response is not None
                else "no_completed_response"
            )
        return {
            "run_id": self.run_id,
            "process_id": self.process_id,
            "schema_version": SCHEMA_VERSION,
            "call_id": self.id,
            "operation": self.operation,
            "created_at": self.created_at,
            "latency_ms": self.latency_ms,
            "status": self.status,
            "request": request,
            "response": response,
            "error": self.error,
            "provider_request_id": self.provider_request_id,
            "endpoint_host": self.endpoint_host,
            "attempts": self.attempts,
            "replay_eligible": reason is None,
            "ineligible_reason": reason,
            "reference_source": "original_response",
        }
