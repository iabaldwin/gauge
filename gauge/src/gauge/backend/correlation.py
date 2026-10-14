"""Evidence-based grouping of captured exchanges. No fixture labels or provider calls."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from typing import Any


def _key(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()[:24]


def conversation_id(event: dict) -> str | None:
    value = event["request"].get("conversation") or (event.get("response") or {}).get(
        "conversation"
    )
    return value.get("id") if isinstance(value, dict) else value if isinstance(value, str) else None


def workload_id(event: dict) -> str:
    request = event["request"]
    instructions = request.get("instructions")
    if instructions is None:
        instructions = "\n".join(
            m.get("content", "")
            for m in request.get("messages", [])
            if m.get("role") in {"system", "developer"} and isinstance(m.get("content"), str)
        )
    contract = {
        "instructions": instructions or "",
        "tools": request.get("tools", []),
        "format": request.get("response_format") or request.get("text", {}).get("format"),
    }
    return "workload_" + _key(contract)


def correlate(events: list[dict]) -> dict[str, dict]:
    """Explicit response links establish parents; shared conversation IDs establish membership.

    Full message history alone establishes neither identity nor a unique parent.
    Duplicate response IDs, missing parents and cycles remain unresolved.
    """
    responses: dict[tuple, list[str]] = defaultdict(list)
    by_id = {e["call_id"]: e for e in events}
    if len(by_id) != len(events):
        raise ValueError("Duplicate capture IDs")
    for e in events:
        response_id = (e.get("response") or {}).get("id")
        if response_id:
            responses[e.get("endpoint_host"), response_id].append(e["call_id"])
    result = {}
    for e in events:
        parent_id = e["request"].get("previous_response_id")
        matches = responses.get((e.get("endpoint_host"), parent_id), []) if parent_id else []
        conv = conversation_id(e)
        history = e["request"].get("messages", e["request"].get("input", []))
        has_history = isinstance(history, list) and any(
            m.get("role") == "assistant" for m in history if isinstance(m, dict)
        )
        evidence = (
            "previous_response_id"
            if len(matches) == 1
            else "unresolved_parent"
            if parent_id
            else "conversation_id"
            if conv
            else "history_without_id"
            if has_history
            else "independent"
        )
        result[e["call_id"]] = {
            "call_id": e["call_id"],
            "parent_call_id": matches[0] if len(matches) == 1 else None,
            "parent_response_id": parent_id,
            "conversation_id": conv,
            "evidence": evidence,
            "workload_id": workload_id(e),
            "trace_id": None,
        }
    invalid = set()
    for call_id in result:
        node, visited = call_id, set()
        while result[node]["parent_call_id"] and node not in visited:
            visited.add(node)
            node = result[node]["parent_call_id"]
        if node in visited:
            invalid.update(visited)
    for call_id in invalid:
        result[call_id].update(parent_call_id=None, evidence="invalid_parent_cycle")
    for call_id, row in result.items():
        identity: tuple[Any, ...]
        node = call_id
        while result[node]["parent_call_id"]:
            node = result[node]["parent_call_id"]
        root = result[node]
        if row["conversation_id"]:
            identity = (by_id[call_id].get("endpoint_host"), "conversation", row["conversation_id"])
        elif (
            root["parent_response_id"]
            and root["parent_call_id"] is None
            and root["evidence"] != "invalid_parent_cycle"
        ):
            identity = (by_id[call_id].get("endpoint_host"), "external", root["parent_response_id"])
        else:
            identity = ("call", node)
        row["trace_id"] = "trace_" + _key(identity)
    return result
