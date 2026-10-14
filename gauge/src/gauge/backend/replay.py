"""Conservative text-only replay preparation, entirely outside the serving process."""

from __future__ import annotations

import json
from collections.abc import Callable

from gauge.wire import MAX_BODY_BYTES, replay_reason


class ReplayUnavailable(ValueError):
    pass


def response_text(response: dict | None) -> str | None:
    if not response:
        return None
    if "choices" in response:
        return response["choices"][0]["message"].get("content")
    parts = [
        p["text"]
        for m in response.get("output", [])
        if m.get("type") == "message"
        for p in m.get("content", [])
        if p.get("type") == "output_text"
    ]
    return "\n".join(parts) if parts else None


def _messages(value) -> list[dict]:
    if isinstance(value, str):
        return [{"role": "user", "content": value}]
    result = []
    for message in value:
        content = message["content"]
        if isinstance(content, list):
            content = "\n".join(p["text"] for p in content)
        result.append({"role": message["role"], "content": content})
    return result


def materialize(event: dict, lookup: Callable[[str, str | None], dict | None]) -> list[dict]:
    """Expand only captured, text-only Response chains. Never fetch provider state.

    A conversation ID doesn't prove the whole prior history was observed, so
    provider-held Conversations stay ineligible. Missing parents fail closed.
    """
    if event.get("status") != "completed" or not event.get("response"):
        raise ReplayUnavailable("no_completed_response")
    if event.get("ineligible_reason") not in {None, "state_or_tools"}:
        raise ReplayUnavailable(event["ineligible_reason"])
    chain, seen, current = [], set(), event
    for _ in range(64):
        if current.get("status") != "completed" or not current.get("response"):
            raise ReplayUnavailable("parent_output_unavailable")
        request = current["request"]
        allowed = (
            {"model", "messages", "max_tokens", "max_completion_tokens"}
            if current["operation"] == "chat.completions.create"
            else {
                "model",
                "input",
                "instructions",
                "max_output_tokens",
                "store",
                "previous_response_id",
            }
        )
        if set(request) - allowed:
            raise ReplayUnavailable("unsupported_replay_parameter_or_conversation")
        plain = {k: v for k, v in request.items() if k != "previous_response_id"}
        reason = replay_reason(current["operation"], plain, current["response"])
        if reason:
            raise ReplayUnavailable(reason)
        if current["call_id"] in seen:
            raise ReplayUnavailable("parent_cycle")
        seen.add(current["call_id"])
        chain.append(current)
        previous = request.get("previous_response_id")
        if not previous:
            break
        parent = lookup(previous, event.get("endpoint_host"))
        if parent is None:
            raise ReplayUnavailable("parent_context_unavailable")
        current = parent
    else:
        raise ReplayUnavailable("context_chain_limit")
    messages = []
    instructions = event["request"].get("instructions")
    if instructions:
        messages.append({"role": "system", "content": instructions})
    for item in reversed(chain):
        request = item["request"]
        messages += _messages(request.get("messages", request.get("input")))
        if item is not event:
            text = response_text(item["response"])
            if text is None:
                raise ReplayUnavailable("parent_output_unavailable")
            messages.append({"role": "assistant", "content": text})
    if len(json.dumps(messages).encode()) > MAX_BODY_BYTES:
        raise ReplayUnavailable("materialized_context_limit")
    return messages
