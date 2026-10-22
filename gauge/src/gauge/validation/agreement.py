"""Dataset-side grouping and reference registration; all judging runs in the backend."""

from __future__ import annotations

import asyncio
import time


def cases(report: dict) -> list[dict]:
    if report.get("dispatch_mode") not in {"fixed-prefix", "quality-suite"}:
        raise ValueError("Agreement trees require recorded-prefix evaluation")
    captures = {}
    for record in report["records"]:
        response_id = (record["capture"].get("response") or {}).get("id")
        if not response_id:
            continue
        if response_id in captures:
            raise ValueError("Ambiguous source response ID in saved report")
        captures[response_id] = record["capture"]["call_id"]
    return [
        {
            "run_id": report["run_id"],
            "conversation": entry["scenario_id"],
            "dataset": entry["dataset"],
            "turn": entry["turn"],
            "reference": entry.get("recorded_reference")
            or "Apply the supplied instruction constraints.",
            "assessment": entry.get("assessment"),
            "history_mode": entry.get("history_mode", "recorded"),
            "evidence": entry.get("evidence"),
            "call_id": captures.get(entry.get("response_id")),
        }
        for entry in report["ledger"]
        if entry["task"] in {"dialogue", "coqa", "quac", "instruction"}
    ]


async def evaluate(backend, report: dict, *, judge: bool, wait_seconds: int = 1200) -> dict:
    for case in cases(report):
        await asyncio.to_thread(backend.post, "/v1/agreement/cases", case)
    if judge:
        await asyncio.to_thread(
            backend.post, "/v1/agreement/judge", {"run_id": report["run_id"], "limit": 2000}
        )
    deadline = time.monotonic() + wait_seconds
    while True:
        result = await asyncio.to_thread(backend.get, "/v1/agreement?run_id=" + report["run_id"])
        if not judge or not result["statuses"].get("pending", 0):
            return result
        if time.monotonic() >= deadline:
            raise TimeoutError("Reference judging timed out; saved progress remains on backend")
        await asyncio.sleep(1)
