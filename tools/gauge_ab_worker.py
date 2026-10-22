"""Isolated benchmark arm. The native process never imports Gauge.

One JSON command per stdin line, one result per stdout line. Provider calls use
persistent clients and identical options; only the selected client import differs.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time

import httpx

ITEMS = ("Dish soap", "Apples", "Paper towels", "Bananas")
TASK = "Classify the product. Return only one label: grocery, household, or other."


async def measure(client, model, indices, concurrency):
    semaphore = asyncio.Semaphore(concurrency)

    async def call(index):
        async with semaphore:
            started = time.perf_counter()
            row = {"pair": index, "item": ITEMS[index % len(ITEMS)]}
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": TASK},
                        {"role": "user", "content": row["item"]},
                    ],
                    max_completion_tokens=32,
                )
                # Stop at the SDK return boundary, before inspecting/serializing output.
                row["latency_ms"] = (time.perf_counter() - started) * 1000
                row.update(
                    status="completed",
                    answer=response.choices[0].message.content,
                    finish_reason=response.choices[0].finish_reason,
                    response_id=response.id,
                    usage=response.usage.model_dump() if response.usage else None,
                )
            except Exception as exc:  # noqa: BLE001 - preserve partial benchmark evidence
                row.update(
                    latency_ms=(time.perf_counter() - started) * 1000,
                    status="error",
                    error=type(exc).__name__,
                    http_status=getattr(exc, "status_code", None),
                )
            row["latency_ms"] = round(row["latency_ms"], 3)
            return row

    started = time.perf_counter()
    rows = await asyncio.gather(*(call(index) for index in indices))
    return {"samples": rows, "wall_seconds": time.perf_counter() - started}


async def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("arm", choices=("native", "gauge"))
    parser.add_argument("--model", required=True)
    args = parser.parse_args()
    if args.arm == "native":
        from openai import AsyncOpenAI
    else:
        from gauge.openai import AsyncOpenAI

    async with AsyncOpenAI(
        api_key=os.environ["OPENAI_API_KEY"],
        base_url=os.environ.get("OPENAI_BASE_URL") or "https://api.openai.com/v1",
        max_retries=0,
        timeout=30,
        http_client=httpx.AsyncClient(
            proxy=os.environ.get("HTTPS_PROXY"), trust_env=False
        ),
    ) as client:
        print(json.dumps({"ready": True, "arm": args.arm}), flush=True)
        for line in sys.stdin:
            command = json.loads(line)
            if command.get("stop"):
                break
            result = await measure(
                client, args.model, command["indices"], command["concurrency"]
            )
            print(json.dumps(result), flush=True)
    if args.arm == "gauge":
        from gauge import captures

        flushed = captures.flush(timeout=30)
        print(
            json.dumps(
                {
                    "flushed": flushed,
                    "exported": captures.exported,
                    "export_dropped": captures.export_dropped,
                    "queue_dropped": captures.queue_dropped,
                    "failed": captures.failed,
                }
            ),
            flush=True,
        )
        captures.close()
    else:
        print(
            json.dumps(
                {
                    "gauge_imported": any(
                        name == "gauge" or name.startswith("gauge.")
                        for name in sys.modules
                    )
                }
            ),
            flush=True,
        )


if __name__ == "__main__":
    asyncio.run(main())
