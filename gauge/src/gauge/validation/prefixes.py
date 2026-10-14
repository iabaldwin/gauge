"""Compile independent evaluation contexts from recorded conversations on the dispatch side."""

from __future__ import annotations

from gauge.validation.records import Scenario


def recorded_prefix(scenario: Scenario, turn: int) -> list[dict[str, str]]:
    if not 0 <= turn < len(scenario.turns):
        raise ValueError("Turn outside recorded conversation")
    messages = []
    for prior in scenario.turns[:turn]:
        if not prior.get("assistant"):
            raise ValueError("Recorded assistant response missing from prefix")
        messages.extend(
            [
                {"role": "user", "content": prior["user"]},
                {"role": "assistant", "content": prior["assistant"]},
            ]
        )
    messages.append({"role": "user", "content": scenario.turns[turn]["user"]})
    return messages


def verify_prefixes(records: list[dict], ledger: list[dict], scenarios: list[Scenario]) -> dict:
    by_response = {r["response_id"]: r for r in ledger if r.get("response_id")}
    by_scenario = {s.id: s for s in scenarios}
    verified, mismatches = 0, []
    for record in records:
        capture = record["capture"]
        entry = by_response.get((capture.get("response") or {}).get("id"))
        if entry is None:
            mismatches.append(capture["call_id"])
            continue
        scenario = by_scenario[entry["scenario_id"]]
        request = capture["request"]
        if scenario.protocol == "chat":
            expected = [
                {"role": "system", "content": scenario.instructions},
                *recorded_prefix(scenario, entry["turn"]),
            ]
            matches = request.get("messages") == expected
        else:
            matches = (
                request.get("input") == recorded_prefix(scenario, entry["turn"])
                and request.get("instructions") == scenario.instructions
            )
        if matches and not (request.get("previous_response_id") or request.get("conversation")):
            verified += 1
        else:
            mismatches.append(capture["call_id"])
    return {
        "verified": verified,
        "mismatches": mismatches,
        "passed": verified == len(ledger) and not mismatches,
    }
