"""Offline SDK microbenchmark; no provider requests. Run using gauge/.venv/bin/python."""

import argparse
import json
import platform
import statistics
import threading
import time
import tracemalloc

import httpx
from openai import OpenAI, __version__

from gauge import CaptureBuffer, wrap


class LocalOnly:
    def send(self, payload):
        return True

    def close(self):
        pass


class BlockedExporter:
    def __init__(self):
        self.release = threading.Event()

    def send(self, payload):
        self.release.wait(60)
        return False

    def close(self):
        pass


def measure(mode, count, output_chars):
    payload = {
        "id": "resp_bench",
        "object": "response",
        "created_at": 1,
        "model": "reference",
        "status": "completed",
        "output": [
            {
                "id": "m",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [
                    {
                        "type": "output_text",
                        "text": "x" * output_chars,
                        "annotations": [],
                    }
                ],
            }
        ],
    }
    raw = json.dumps(payload).encode()
    exporter = BlockedExporter() if mode == "backend_stalled" else None
    buffer = (
        CaptureBuffer(exporter=exporter or LocalOnly()) if mode != "native" else None
    )
    samples = []
    try:
        with OpenAI(
            api_key="offline",
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(
                        200, content=raw, headers={"content-type": "application/json"}
                    ),
                )
            ),
        ) as client:
            if buffer:
                wrap(client, buffer=buffer)
            for _ in range(50):
                client.responses.create(model="reference", input="classify")
            for _ in range(count):
                start = time.perf_counter_ns()
                client.responses.create(model="reference", input="classify")
                samples.append((time.perf_counter_ns() - start) / 1000)
            tracemalloc.start()
            for _ in range(200):
                client.responses.create(model="reference", input="classify")
            current, peak = tracemalloc.get_traced_memory()
            tracemalloc.stop()
        ordered = sorted(samples)
        result = {
            "mode": mode,
            "calls": count,
            "output_chars": output_chars,
            "p50_us": round(statistics.median(ordered), 2),
            "p95_us": round(ordered[int(len(ordered) * 0.95)], 2),
            "p99_us": round(ordered[int(len(ordered) * 0.99)], 2),
            "separate_200_call_tracemalloc_current_bytes": current,
            "separate_200_call_tracemalloc_peak_bytes": peak,
        }
        if buffer:
            result.update(
                pending_accounted_bytes=buffer._pending_bytes,
                history_accounted_bytes=buffer._event_bytes,
                budget_bytes_per_queue=buffer.max_buffer_bytes,
                capture_dropped=buffer.dropped,
            )
        return result
    finally:
        if exporter:
            exporter.release.set()
        if buffer:
            buffer.close()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--calls", type=int, default=1000)
    args = parser.parse_args()
    if args.calls < 100:
        parser.error("At least 100 calls required for percentiles")
    print(
        json.dumps(
            {
                "python": platform.python_version(),
                "openai": __version__,
                "note": "Mock HTTP only; descriptive microbenchmark, not a production latency SLO. "
                "Tracemalloc runs separately and includes native SDK allocations. "
                "Thread scheduling and runtime noise affect percentiles.",
                "results": [
                    measure(mode, args.calls, size)
                    for size in (128, 131072)
                    for mode in ("native", "capture_local", "backend_stalled")
                ],
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
