"""Source dispatch and its private audit ledger; candidates run only in the backend."""

from __future__ import annotations

import time

from gauge.evaluation.scoring import expected_for, grade
from gauge.validation.prefixes import recorded_prefix
from gauge.validation.protocols import exchange, outcome


class Dispatcher:
    def __init__(self, client, model: str, max_tokens: int, ledger: list, scores: list):
        self.client, self.model, self.max_tokens = client, model, max_tokens
        self.ledger, self.scores = ledger, scores
        self.history: dict[str, list] = {}
        self.blocked: set[str] = set()

    async def turn(self, scenario, index: int) -> bool:
        turn = scenario.turns[index]
        entry = {
            "scenario_id": scenario.id,
            "dataset": scenario.dataset,
            "task": scenario.task,
            "protocol": scenario.protocol,
            "turn": index,
            "history_mode": scenario.history_mode,
            "recorded_reference": turn.get("assistant") or None,
            "assessment": {"task": scenario.task, "expected": expected_for(scenario, index)},
            "evidence": turn.get("evidence"),
            "parent_response_id": None,
        }
        history = (
            recorded_prefix(scenario, index)[:-1]
            if scenario.history_mode == "recorded"
            else self.history.get(scenario.id, [])
        )
        entry["request_history"] = list(history)
        text, complete = None, False
        before = time.monotonic()
        if scenario.id in self.blocked:
            entry.update(response_id=None, complete=False, error="source_prefix_unavailable")
        else:
            try:
                response = await exchange(
                    self.client, scenario, index, history, None, None, self.model, self.max_tokens
                )
                _, text, complete = outcome(response)
                entry.update(
                    response_id=response.id,
                    request_id=response._request_id,
                    complete=complete,
                    source_output=text,
                )
            except Exception as exc:
                entry.update(
                    response_id=None,
                    complete=False,
                    error=type(exc).__name__,
                    http_status=getattr(exc, "status_code", None),
                )
        if scenario.history_mode == "source":
            if complete and text:
                self.history[scenario.id] = [
                    *history,
                    {"role": "user", "content": turn["user"]},
                    {"role": "assistant", "content": text},
                ]
            else:
                self.blocked.add(scenario.id)
        entry["latency_ms"] = round((time.monotonic() - before) * 1000, 2)
        self.ledger.append(entry)
        self.scores.append(
            {
                **entry,
                "target": self.model,
                "score": grade(scenario.task, expected_for(scenario, index), text, complete),
            }
        )
        return complete
