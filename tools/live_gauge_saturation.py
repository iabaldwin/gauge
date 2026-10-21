"""LIVE OpenAI → local Gauge backend → Fireworks load/saturation exercise.

Configurable bounded OpenAI calls and Fireworks comparisons. No mocks.
The persistent backend runs in a different process. Pausing comparison dispatch deliberately
fills its tiny queue; the application's native inference remains live throughout.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import math
import os
import statistics
import time
from pathlib import Path
from urllib.parse import urlsplit

import httpx
from gauge.local.runtime import ensure_service as settings
from gauge.openai import AsyncOpenAI as GaugeAsyncOpenAI
from openai import AsyncOpenAI

from gauge import captures

ROOT = Path(__file__).resolve().parents[1]
ITEMS = ["Dish soap", "Apples", "Paper towels", "Bananas"]
TASK = "Classify the product. Return only one label: grocery, household, or other."


def source_settings():
    key = os.environ.get("OPENAI_API_KEY") or os.environ.get("PORTKEY_API_KEY")
    if not key:
        raise ValueError("Configure OPENAI_API_KEY or the existing PORTKEY credentials")
    base = os.environ.get("OPENAI_BASE_URL")
    if not os.environ.get("OPENAI_API_KEY"):
        base = os.environ.get("PORTKEY_BASE_URL")
        if not base:
            raise ValueError("PORTKEY_BASE_URL required when using gateway credentials")
    headers = {}
    if not os.environ.get("OPENAI_API_KEY"):
        if urlsplit(base).hostname == "cybertron-service-gateway.service.prod.ddsd":
            base = "https://cybertron-service-gateway.doordash.team/v1"
        headers["x-portkey-api-key"] = key
    return key, base, headers


async def phase(cls, model, count=4, concurrency=4, start_index=0):
    key, base, headers = source_settings()
    semaphore = asyncio.Semaphore(concurrency)
    finished = 0
    async with cls(
        api_key=key,
        default_headers=headers,
        **({"base_url": base} if base else {}),
        max_retries=0,
        timeout=20,
        http_client=httpx.AsyncClient(
            proxy=os.environ.get("HTTPS_PROXY"), trust_env=False
        ),
    ) as client:

        async def dispatch(index):
            nonlocal finished
            async with semaphore:
                result = await call(ITEMS[index % len(ITEMS)])
                result["sequence"] = index
                finished += 1
                if finished % 25 == 0 or finished == count:
                    print(
                        json.dumps({"phase_progress": finished, "of": count}),
                        flush=True,
                    )
                return result

        async def call(item):
            started = time.perf_counter()
            try:
                response = await client.chat.completions.create(
                    model=model,
                    messages=[
                        {"role": "system", "content": TASK},
                        {"role": "user", "content": item},
                    ],
                    max_completion_tokens=32,
                )
                return {
                    "item": item,
                    "status": "completed",
                    "answer": response.choices[0].message.content,
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                    "finish_reason": response.choices[0].finish_reason,
                    "usage": response.usage.model_dump() if response.usage else None,
                }
            except Exception as exc:  # noqa: BLE001 - record every failed live call
                return {
                    "item": item,
                    "status": "error",
                    "error": type(exc).__name__,
                    "http_status": getattr(exc, "status_code", None),
                    "latency_ms": round((time.perf_counter() - started) * 1000, 2),
                }

        return await asyncio.gather(
            *(dispatch(i) for i in range(start_index, start_index + count))
        )


def summarize(results):
    if not results:
        return {"calls": 0, "successes": 0, "results": []}
    timings = sorted(r["latency_ms"] for r in results)
    return {
        "calls": len(results),
        "successes": sum(r["status"] == "completed" for r in results),
        "median_ms": round(statistics.median(timings), 2),
        "p95_ms": timings[math.ceil(len(timings) * 0.95) - 1],
        "p99_ms": timings[math.ceil(len(timings) * 0.99) - 1],
        "max_ms": max(timings),
        "results": results,
    }


def saved_comparisons(control, run_id):
    """Read this run's persisted jobs, including all dashboard pages."""
    comparisons, cursor = [], None
    while True:
        params = {"run_id": run_id}
        if cursor is not None:
            params["before"] = cursor
        response = control.get("/v1/dashboard", params=params)
        response.raise_for_status()
        page = response.json()
        comparisons.extend(job for row in page["records"] for job in row["comparisons"])
        cursor = page["next_cursor"]
        if cursor is None:
            return comparisons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source-model",
        default=(
            "gpt-4.1-nano"
            if os.environ.get("OPENAI_API_KEY")
            else "@openai-foundations/gpt-5.4-mini"
        ),
    )
    parser.add_argument(
        "--candidate-model", action="append", help="Repeat for up to 3 models"
    )
    parser.add_argument("--mode", choices=["saturation", "load"], default="saturation")
    parser.add_argument("--comparison-wait-seconds", type=int, default=1200)
    parser.add_argument("--output", default="out/gauge-live-saturation.json")
    parser.add_argument(
        "--backend-url",
        help="Existing dedicated local backend (defaults to managed backend)",
    )
    parser.add_argument("--calls", type=int, default=8, help="Total OpenAI call budget")
    parser.add_argument("--baseline-calls", type=int, default=4)
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--comparison-capacity", type=int, default=1)
    parser.add_argument("--comparison-max-calls", type=int, default=4)
    args = parser.parse_args()
    if not 2 <= args.calls <= 1000 or not 0 <= args.baseline_calls < args.calls:
        parser.error("Use 2–1000 total calls and fewer baseline calls than total calls")
    if args.mode == "saturation" and args.baseline_calls == 0:
        parser.error("Saturation mode needs at least one native baseline call")
    models = args.candidate_model or ["accounts/fireworks/models/gpt-oss-120b"]
    if not 1 <= len(models) <= 3 or len(set(models)) != len(models):
        parser.error("Use 1–3 unique candidate models")
    if not 1 <= args.concurrency <= 50:
        parser.error("Concurrency must be 1–50")
    if not 1 <= args.comparison_capacity <= 3000:
        parser.error("Comparison capacity must be 1–3000")
    if not 1 <= args.comparison_max_calls <= 3000:
        parser.error("Comparison call budget must be 1–3000")
    if not 1 <= args.comparison_wait_seconds <= 3600:
        parser.error("Comparison wait must be 1–3600 seconds")
    if args.mode == "load" and args.comparison_max_calls < (
        args.calls - args.baseline_calls
    ) * len(models):
        parser.error("Load comparison budget must cover all captured calls × models")
    if (
        args.mode == "saturation"
        and (args.calls - args.baseline_calls) * len(models)
        <= args.comparison_capacity + 1
    ):
        parser.error("Saturation phase must have more calls than queue + active job")
    source_settings()
    config = settings()
    backend_url = (
        args.backend_url or config.get("url") or "http://127.0.0.1:8765"
    ).rstrip("/")
    if urlsplit(backend_url).hostname not in {"127.0.0.1", "localhost"}:
        parser.error("Saturation control is restricted to a dedicated loopback backend")
    output = Path(args.output).resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    run_id = None
    report = {
        "live": True,
        "source_model": args.source_model,
        "candidate_models": models,
        "mode": args.mode,
        "backend_database": config.get("db")
        if config.get("url") == backend_url
        else None,
        "dashboard_url": backend_url + "/dashboard",
        "limits": {
            "openai_calls": args.calls,
            "baseline_calls": args.baseline_calls,
            "saturation_calls": args.calls - args.baseline_calls,
            "openai_concurrency": args.concurrency,
            "openai_max_tokens_per_call": 32,
            "fireworks_calls": args.comparison_max_calls,
            "fireworks_max_tokens_per_call": 256,
            "comparison_queue_capacity": args.comparison_capacity,
            "comparison_workers": None,
        },
        "note": "Live load exercise; repeated classification fixtures, not a quality benchmark.",
    }
    try:
        os.environ.update(
            GAUGE_ENDPOINT=backend_url + "/v1/captures",
            GAUGE_ENABLED="1",
        )
        with httpx.Client(
            base_url=backend_url,
            trust_env=False,
            timeout=3,
        ) as control:
            previous = control.get("/stats").json()
            if previous.get("comparisons_enabled") is False:
                raise RuntimeError(
                    "Backend comparisons are disabled. Set FIREWORKS_API_KEY, "
                    "stop the idle backend with gauge backend stop, then retry."
                )
            totals = {
                key: previous.get(key, 0)
                for key in ("received", "comparison_jobs", "completed", "failed")
            }
            report["backend_before_run"] = totals

            def run_stats():
                snapshot = control.get("/stats").json()
                return {
                    **snapshot,
                    **{
                        key: snapshot.get(key, 0) - value
                        for key, value in totals.items()
                    },
                }

            response = control.post(
                "/v1/runs/start",
                json={
                    "name": f"{args.mode.title()} · {args.calls} OpenAI calls · {len(models)} candidates",
                    "capacity": args.comparison_capacity,
                    "max_calls": args.comparison_max_calls,
                    "models": models,
                },
            )
            response.raise_for_status()
            run = response.json()["run"]
            run_id = run["id"]
            os.environ["GAUGE_RUN_ID"] = run_id
            report["limits"]["comparison_workers"] = run.get("workers", 1)
            report["run_id"] = run_id
            report["dashboard_url"] += "?run=" + run_id
            print(
                json.dumps({"run_id": run_id, "dashboard": report["dashboard_url"]}),
                flush=True,
            )
            started = time.perf_counter()
            baseline = asyncio.run(
                phase(
                    AsyncOpenAI,
                    args.source_model,
                    args.baseline_calls,
                    args.concurrency,
                )
            )
            report["baseline"] = summarize(baseline)
            report["baseline"]["wall_seconds"] = round(time.perf_counter() - started, 3)
            if any(item["status"] == "error" for item in baseline):
                raise RuntimeError(
                    "Live OpenAI baseline failed; no saturation calls dispatched"
                )
            if args.mode == "saturation":
                control.post("/control", json={"paused": True}).raise_for_status()
            started = time.perf_counter()
            # The first captured call is part of the requested budget and checks
            # source access before dispatching the remaining paid calls.
            results = asyncio.run(phase(GaugeAsyncOpenAI, args.source_model, 1, 1))
            if results[0]["status"] != "completed":
                report["during_saturation"] = summarize(results)
                raise RuntimeError(
                    "First OpenAI call failed; remaining calls not dispatched"
                )
            results += asyncio.run(
                phase(
                    GaugeAsyncOpenAI,
                    args.source_model,
                    args.calls - args.baseline_calls - 1,
                    args.concurrency,
                    start_index=1,
                )
            )
            report["during_saturation"] = summarize(results)
            elapsed = time.perf_counter() - started
            report["during_saturation"]["wall_seconds"] = round(elapsed, 3)
            report["during_saturation"]["calls_per_second"] = round(
                len(results) / elapsed, 2
            )
            # Test-controller diagnostics only; adopting application code never flushes.
            report["capture_export_drained"] = captures.flush(timeout=60)
            report["backend_while_paused"] = run_stats()
            report["capture_metrics"] = {
                "exported": captures.exported,
                "failed": captures.failed,
                "dropped": captures.dropped,
                "export_dropped": captures.export_dropped,
                "pending_accounted_bytes": captures._pending_bytes,
                "history_accounted_bytes": captures._event_bytes,
            }
            control.post("/control", json={"paused": False}).raise_for_status()
            deadline = time.monotonic() + args.comparison_wait_seconds
            next_progress = 0.0
            while True:
                stats = run_stats()
                if not stats["active"] and not stats["queued"]:
                    break
                if time.monotonic() >= deadline:
                    break
                if time.monotonic() >= next_progress:
                    print(
                        json.dumps(
                            {
                                "comparison_progress": stats["completed"],
                                "failed": stats["failed"],
                                "queued": stats["queued"],
                                "active": stats["active"],
                            }
                        ),
                        flush=True,
                    )
                    next_progress = time.monotonic() + 15
                time.sleep(0.5)
            report["backend_after_recovery"] = stats
            comparisons = saved_comparisons(control, run_id)
            report["comparisons"] = comparisons
            report["checks"] = {
                "all_openai_calls_completed": all(
                    r["status"] == "completed" for r in baseline + results
                ),
                "native_answers_usable": all(
                    r.get("answer") in {"grocery", "household", "other"}
                    and r.get("finish_reason") == "stop"
                    for r in results
                ),
                "backend_saturated_or_load_mode": args.mode == "load"
                or report["backend_while_paused"]["queue_dropped"] > 0,
                "real_fireworks_comparisons_completed": any(
                    r.get("agreement") is not None for r in comparisons
                ),
                "all_accepted_comparisons_succeeded": (
                    stats["failed"] == 0
                    and stats["completed"] > 0
                    and stats["completed"]
                    == (
                        stats["received"] * len(models)
                        - stats["queue_dropped"]
                        - stats["ineligible"] * len(models)
                    )
                ),
                "backend_recovered": stats["active"] == 0 and stats["queued"] == 0,
            }
            if args.mode == "load":
                report["checks"].update(
                    all_captures_delivered=stats["received"] == len(results),
                    no_comparison_queue_drops=stats["queue_dropped"] == 0,
                    all_model_comparisons_completed=stats["completed"]
                    == len(results) * len(models),
                    every_model_has_scored_outputs=all(
                        any(
                            r.get("candidate") == model
                            and r.get("agreement") is not None
                            for r in comparisons
                        )
                        for model in models
                    ),
                )
    except Exception as exc:  # noqa: BLE001 - always write the diagnostic report
        report["test_error"] = type(exc).__name__ + ": " + str(exc)
    finally:
        captures.close()
        if run_id:
            try:
                summary = {
                    k: report[k]
                    for k in (
                        "baseline",
                        "during_saturation",
                        "checks",
                        "test_error",
                        "source_model",
                        "candidate_models",
                        "mode",
                        "limits",
                    )
                    if k in report
                }
                summary = {
                    k: {a: b for a, b in v.items() if a != "results"}
                    if isinstance(v, dict)
                    else v
                    for k, v in summary.items()
                }
                with httpx.Client(
                    base_url=backend_url,
                    trust_env=False,
                    timeout=5,
                ) as control:
                    control.post("/control", json={"paused": False}).raise_for_status()
                    control.post(
                        "/v1/runs/end", json={"run_id": run_id, "report": summary}
                    ).raise_for_status()
            except Exception as exc:  # noqa: BLE001 - retain results on backend failure
                report["backend_save_error"] = type(exc).__name__
        output.write_text(json.dumps(report, indent=2) + "\n")
    print(
        json.dumps(
            {
                "report": str(output),
                "checks": report.get("checks"),
                "error": report.get("test_error"),
                "backend_save_error": report.get("backend_save_error"),
                "dashboard": report.get("dashboard_url"),
            },
            indent=2,
        ),
        flush=True,
    )
    if (
        report.get("test_error")
        or report.get("backend_save_error")
        or not all(report.get("checks", {}).values())
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
