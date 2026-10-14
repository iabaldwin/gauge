"""Test-side drain and bounded re-enqueue for disposable judge queue overflow."""

from __future__ import annotations

import asyncio
import json
import time

from gauge.validation.backend import BackendClient


async def preflight(backend: BackendClient) -> None:
    deadline = time.monotonic() + 90
    while time.monotonic() < deadline:
        state = await asyncio.to_thread(backend.get, "/stats?summary=1")
        if state["failed"]:
            raise RuntimeError("First comparison failed; remaining source budget not dispatched")
        judges = state.get("judging", {})
        if judges.get("errors"):
            raise RuntimeError("First JEV judgment failed; remaining source budget not dispatched")
        if state["completed"] == 3 and judges.get("completed", 0) + judges.get("uncertain", 0) == 3:
            print(
                json.dumps({"preflight": "OpenAI, three Fireworks models, and JEV passed"}),
                flush=True,
            )
            return
        await asyncio.sleep(0.5)
    raise TimeoutError("First comparison/judge preflight timed out")


async def drain(backend: BackendClient, run_id: str, wait_seconds: int) -> tuple[dict, list]:
    deadline, last_progress = time.monotonic() + wait_seconds, 0.0

    async def state() -> dict:
        nonlocal last_progress
        result = await asyncio.to_thread(backend.get, "/stats?summary=1")
        if time.monotonic() > deadline:
            raise TimeoutError("Comparison/judge drain timed out")
        if time.monotonic() - last_progress >= 15:
            print(
                json.dumps(
                    {
                        k: result.get(k)
                        for k in ("completed", "failed", "queued", "active", "judging")
                    }
                ),
                flush=True,
            )
            last_progress = time.monotonic()
        return result

    current = await state()
    while current["active"] or current["queued"]:
        await asyncio.sleep(0.5)
        current = await state()
    records = await asyncio.to_thread(backend.records, run_id)
    if not current.get("judging", {}).get("enabled"):
        raise RuntimeError("JEV must be enabled for full-suite judging")
    # Each capture is re-enqueued at most once. Already-completed or in-flight
    # judgments are deduplicated by the backend. This does not rerun inference.
    for record in records:
        needed = any(
            j["status"] == "completed"
            and j.get("finish_reason") == "stop"
            and j.get("judgment", {}).get("status")
            not in {"completed", "uncertain", "pending", "running"}
            for j in record["comparisons"]
        )
        if not needed:
            continue
        while current["judging"]["queued"] > 192:
            await asyncio.sleep(0.5)
            current = await state()
        await asyncio.to_thread(
            backend.post,
            "/v1/judgments",
            {
                "run_id": run_id,
                "call_id": record["capture"]["call_id"],
                "limit": 3,
            },
        )
        current = await state()
    while current["judging"]["queued"] or current["judging"]["active"]:
        await asyncio.sleep(0.5)
        current = await state()
    return current, await asyncio.to_thread(backend.records, run_id)
