"""Correlation scorecard and adversarial replay built from actual dataset conversations."""

from __future__ import annotations

import hashlib
import json
import random
from collections import defaultdict

from gauge.backend.correlation import correlate
from gauge.validation.records import Scenario
from gauge.wire import CaptureEvent


def evaluate(events: list[dict], ledger: list[dict]) -> dict:
    graph = correlate(events)
    by_response = {(e.get("response") or {}).get("id"): e["call_id"] for e in events}
    expected = {
        by_response[r["response_id"]]: r for r in ledger if r.get("response_id") in by_response
    }
    parent_checks, correct_parents, missing, false_parents = 0, 0, 0, 0
    members = defaultdict(set)
    unresolved_history = 0
    for call_id, truth in expected.items():
        row = graph[call_id]
        members[row["trace_id"]].add(truth["scenario_id"])
        previous = truth.get("parent_response_id")
        if previous:
            parent_checks += 1
            parent = by_response.get(previous)
            if parent and row["parent_call_id"] == parent:
                correct_parents += 1
            else:
                missing += 1
        elif row["parent_call_id"] is not None:
            false_parents += 1
        if truth["protocol"] in {"chat", "responses"} and truth["turn"] > 0:
            unresolved_history += row["evidence"] == "history_without_id"
    conversations = defaultdict(set)
    for call_id, truth in expected.items():
        if truth["protocol"] == "conversation":
            conversations[truth["scenario_id"]].add(graph[call_id]["trace_id"])
    false_merges = sum(len(v) > 1 for v in members.values())
    split_conversations = sum(len(v) != 1 for v in conversations.values())
    return {
        "captured_calls": len(events),
        "mapped_calls": len(expected),
        "expected_calls": len(ledger),
        "explicit_parent_links": parent_checks,
        "correct_parent_links": correct_parents,
        "missing_parent_links": missing,
        "false_parent_links": false_parents,
        "false_trace_merges": false_merges,
        "split_explicit_conversations": split_conversations,
        "history_only_turns_left_unresolved": unresolved_history,
        "passed": len(expected) == len(ledger)
        and not (missing or false_parents or false_merges or split_conversations),
        "graph": graph,
    }


def offline(scenarios: list[Scenario]) -> dict:
    events: list[dict] = []
    ledger: list[dict] = []
    for scenario in scenarios:
        previous = None
        history: list[dict] = []
        for i, turn in enumerate(scenario.turns):
            token = hashlib.sha256(f"{scenario.id}/{i}".encode()).hexdigest()
            rid = "resp_" + token
            request: dict = {"model": "fixture", "instructions": scenario.instructions}
            message = {"role": "user", "content": turn["user"]}
            if scenario.protocol == "chat":
                operation = "chat.completions.create"
                request = {
                    "model": "fixture",
                    "messages": [
                        {"role": "system", "content": scenario.instructions},
                        *history,
                        message,
                    ],
                }
            else:
                operation = "responses.create"
                request["input"] = (
                    [*history, message] if scenario.protocol == "responses" else [message]
                )
                if scenario.protocol == "chain" and previous:
                    request["previous_response_id"] = previous
                if scenario.protocol == "conversation":
                    request["conversation"] = (
                        "conv_" + hashlib.sha256(scenario.id.encode()).hexdigest()
                    )
            text = turn.get("assistant") or "fixture output"
            response = {
                "id": rid,
                "status": "completed",
                "output": [{"type": "message", "content": [{"type": "output_text", "text": text}]}],
            }
            if scenario.protocol == "chat":
                response = {
                    "id": rid,
                    "choices": [
                        {"finish_reason": "stop", "message": {"role": "assistant", "content": text}}
                    ],
                }
            e = CaptureEvent(
                "call_" + token[:32],
                operation,
                json.dumps(request).encode(),
                json.dumps(response).encode(),
                "completed",
                10,
                float(len(events)),
                endpoint_host="fixture.invalid",
            ).to_dict()
            events.append(e)
            ledger.append(
                {
                    "scenario_id": scenario.id,
                    "turn": i,
                    "protocol": scenario.protocol,
                    "response_id": rid,
                    "parent_response_id": previous if scenario.protocol == "chain" else None,
                }
            )
            history += [message, {"role": "assistant", "content": text}]
            previous = rid
    # Repeat byte-identical independent inputs with distinct IDs, then reorder delivery.
    single = next(e for e in events if e["operation"] == "chat.completions.create")
    for i in range(4):
        e = json.loads(json.dumps(single))
        e["call_id"] = "call_" + f"{i + 1:032x}"
        e["response"]["id"] = "duplicate_content_" + str(i)
        events.append(e)
        ledger.append(
            {
                "scenario_id": "independent_duplicate_" + str(i),
                "turn": 0,
                "protocol": "chat",
                "response_id": e["response"]["id"],
            }
        )
    random.Random(19).shuffle(events)
    result = evaluate(events, ledger)
    result["adversarial_cases"] = [
        "out_of_order_delivery",
        "identical_independent_calls",
        "interleaved_protocols",
        "history_without_ids",
    ]
    return result
