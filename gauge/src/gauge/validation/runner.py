"""Bounded benchmark orchestration. All comparison work stays in the separate backend."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx

from gauge import captures
from gauge.openai import AsyncOpenAI
from gauge.validation.backend import BackendClient, source_options
from gauge.validation.checks import evaluate
from gauge.validation.drain import drain, preflight
from gauge.validation.prefixes import recorded_prefix, verify_prefixes
from gauge.validation.protocols import exchange, outcome
from gauge.validation.records import read_suite
from gauge.validation.report import render
from gauge.validation.scoring import aggregate, grade

DEFAULT_MODELS = [
    "accounts/fireworks/models/qwen3p8-max",
    "accounts/fireworks/models/gpt-oss-120b",
    "accounts/fireworks/models/deepseek-v4p1-flash",
]


async def run(
    manifest_path: Path,
    output: Path,
    connection: Path,
    model: str,
    concurrency: int = 4,
    wait_seconds: int = 1200,
) -> dict:
    if not 1 <= concurrency <= 20 or not 1 <= wait_seconds <= 3600:
        raise ValueError("Concurrency 1–20 and wait 1–3600 seconds required")
    manifest, scenarios = read_suite(manifest_path)
    fixed = manifest.get("dispatch_mode") == "fixed-prefix"
    if fixed and any(s.protocol not in {"chat", "responses"} for s in scenarios):
        raise ValueError("Fixed-prefix cases require self-contained chat or Responses input")
    if not 1 <= manifest["source_calls"] <= 1000:
        raise ValueError("Source call budget must be 1–1000")
    options = source_options()
    ledger, source_rows = [], []
    started = time.monotonic()
    report = {
        "schema_version": 1,
        "manifest_sha256": manifest["sha256"],
        "profile": manifest["profile"],
        "dispatch_mode": manifest.get("dispatch_mode", "protocols"),
        "source_model": model,
        "source_calls": manifest["source_calls"],
        "sources": manifest["sources"],
        "candidate_models": DEFAULT_MODELS,
        "candidate_max_output_tokens": 2048 if fixed else 1024,
        "source_max_output_tokens": 1024 if fixed else 256,
        "checks": {},
    }
    run_id = None
    with BackendClient(connection) as backend:
        try:
            state = backend.get("/stats?summary=1")
            judges = state.get("judging", {})
            if (
                state.get("active_run_id")
                or state["active"]
                or state["queued"]
                or judges.get("active")
                or judges.get("queued")
            ):
                raise ValueError("Backend must be idle before validation")
            if fixed and (not judges.get("enabled") or judges.get("calls_started", 0)):
                raise ValueError("Fixed-prefix validation needs a fresh enabled judge budget")
            result = backend.post(
                "/v1/runs/start",
                {
                    "name": (
                        f"{'Fixed prefixes' if fixed else 'Public data'} · "
                        f"{manifest['profile']} · {manifest['source_calls']} calls"
                    ),
                    "models": DEFAULT_MODELS,
                    "capacity": 3000,
                    "max_calls": manifest["source_calls"] * len(DEFAULT_MODELS),
                    "max_output_tokens": report["candidate_max_output_tokens"],
                },
            )
            run_id = result["run"]["id"]
            report.update(run_id=run_id, dashboard=backend.url + "/dashboard?run=" + run_id)
            print(json.dumps({"run_id": run_id, "dashboard": report["dashboard"]}), flush=True)
            os.environ.update(
                GAUGE_ENDPOINT=backend.url + "/v1/captures",
                GAUGE_API_KEY=backend.http.headers["Authorization"][7:],
                GAUGE_ENABLED="1",
            )
            semaphore = asyncio.Semaphore(concurrency)
            async with AsyncOpenAI(
                **options,
                max_retries=0,
                timeout=30,
                http_client=httpx.AsyncClient(proxy=os.environ.get("HTTPS_PROXY"), trust_env=False),
            ) as client:

                async def episode(scenario):
                    async with semaphore:
                        history, previous, conversation = [], None, None
                        if scenario.protocol == "conversation":
                            try:
                                conversation = (await client.conversations.create()).id
                            except Exception as exc:
                                report.setdefault("control_errors", []).append(
                                    {
                                        "scenario_id": scenario.id,
                                        "error": type(exc).__name__,
                                        "http_status": getattr(exc, "status_code", None),
                                    }
                                )
                                return
                        for i, turn in enumerate(scenario.turns):
                            if fixed:
                                history = recorded_prefix(scenario, i)[:-1]
                            before = time.monotonic()
                            entry = {
                                "scenario_id": scenario.id,
                                "dataset": scenario.dataset,
                                "task": scenario.task,
                                "protocol": scenario.protocol,
                                "turn": i,
                                "recorded_reference": turn.get("assistant") if fixed else None,
                                "parent_response_id": previous
                                if scenario.protocol == "chain"
                                else None,
                            }
                            try:
                                response = await exchange(
                                    client,
                                    scenario,
                                    i,
                                    history,
                                    previous,
                                    conversation,
                                    model,
                                    report["source_max_output_tokens"],
                                )
                                raw, text, complete = outcome(response)
                                entry.update(
                                    response_id=response.id,
                                    request_id=response._request_id,
                                    complete=complete,
                                )
                                source_rows.append(
                                    {
                                        **entry,
                                        "target": model,
                                        "score": grade(
                                            scenario.task, scenario.expected, text, complete
                                        ),
                                    }
                                )
                                if not fixed:
                                    history += [
                                        {"role": "user", "content": turn["user"]},
                                        {"role": "assistant", "content": text or ""},
                                    ]
                                    previous = response.id
                            except Exception as exc:
                                entry.update(
                                    response_id=None,
                                    complete=False,
                                    error=type(exc).__name__,
                                    http_status=getattr(exc, "status_code", None),
                                )
                                source_rows.append(
                                    {
                                        **entry,
                                        "target": model,
                                        "score": grade(
                                            scenario.task, scenario.expected, None, False
                                        ),
                                    }
                                )
                                ledger.append(entry)
                                if fixed:
                                    continue  # Independent cases retain recorded context.
                                break  # Never pretend a failed stateful turn has usable context.
                            entry["latency_ms"] = round((time.monotonic() - before) * 1000, 2)
                            ledger.append(entry)
                            if len(ledger) % 25 == 0:
                                print(
                                    json.dumps(
                                        {
                                            "source_progress": len(ledger),
                                            "budget": manifest["source_calls"],
                                        }
                                    ),
                                    flush=True,
                                )

                # One independent request checks source access within the fixture budget.
                await episode(scenarios[0])
                if not ledger or not ledger[-1].get("complete"):
                    raise RuntimeError(
                        "First source call failed; remaining budget was not dispatched"
                    )
                if fixed:
                    if not captures.flush(timeout=15):
                        raise RuntimeError("First capture was not exported")
                    await preflight(backend)
                await asyncio.gather(*(episode(s) for s in scenarios[1:]))
            drained = captures.flush(timeout=60)
            deadline, next_progress = time.monotonic() + wait_seconds, 0.0
            while not fixed:
                state = backend.get("/stats?summary=1")
                if not state["active"] and not state["queued"]:
                    break
                if time.monotonic() > deadline:
                    raise TimeoutError("Comparison drain timed out")
                if time.monotonic() > next_progress:
                    print(
                        json.dumps(
                            {k: state[k] for k in ("completed", "failed", "queued", "active")}
                        ),
                        flush=True,
                    )
                    next_progress = time.monotonic() + 15
                await asyncio.sleep(0.5)
            if fixed:
                state, records = await drain(backend, run_id, wait_seconds)
            else:
                records = backend.records(run_id)
            events = [r["capture"] for r in records]
            correlation = evaluate(events, ledger)
            by_response = {r["response_id"]: r for r in ledger if r.get("response_id")}
            scenarios_by_id = {s.id: s for s in scenarios}
            scores = list(source_rows)
            for record in records:
                truth = by_response.get((record["capture"].get("response") or {}).get("id"))
                if not truth:
                    continue
                scenario = scenarios_by_id[truth["scenario_id"]]
                for job in record["comparisons"]:
                    scores.append(
                        {
                            "scenario_id": scenario.id,
                            "dataset": scenario.dataset,
                            "target": job["candidate"],
                            "turn": truth["turn"],
                            "score": grade(
                                scenario.task,
                                scenario.expected,
                                job.get("candidate_output"),
                                job["status"] == "completed" and job.get("finish_reason") == "stop",
                            ),
                        }
                    )
            checks = {
                "source_budget_completed": len(ledger) == manifest["source_calls"]
                and all(r.get("complete") for r in ledger),
                "all_captures_delivered": drained and len(events) == len(ledger),
                "correlation_correct": correlation["passed"],
                "candidate_joins_complete": all(len(r["comparisons"]) == 3 for r in records),
                "no_comparison_errors": state["failed"] == 0,
                "supported_contexts_replayed": all(
                    all(j["status"] == "skipped" for j in r["comparisons"])
                    if r["capture"]["request"].get("conversation")
                    else all(j["status"] == "completed" for j in r["comparisons"])
                    for r in records
                ),
                "no_queue_drops": state["queue_dropped"] == 0,
                "backend_drained": not state["queued"] and not state["active"],
            }
            if fixed:
                prefix_checks = verify_prefixes(records, ledger, scenarios)
                report["prefix_checks"] = prefix_checks
                checks["recorded_prefixes_correct"] = prefix_checks["passed"]
                eligible = [
                    j
                    for r in records
                    for j in r["comparisons"]
                    if j["status"] == "completed" and j.get("finish_reason") == "stop"
                ]
                checks["all_eligible_pairs_judged"] = bool(eligible) and all(
                    j.get("judgment", {}).get("status") in {"completed", "uncertain"}
                    for j in eligible
                )
                checks["judge_drained"] = (
                    not state["judging"]["active"] and not state["judging"]["queued"]
                )
                report["judgment_counts"] = {
                    "eligible": len(eligible),
                    **{
                        status: sum(j.get("judgment", {}).get("status") == status for j in eligible)
                        for status in ("completed", "uncertain", "error", "skipped")
                    },
                }
            report.update(
                checks=checks,
                records=records,
                ledger=ledger,
                scores=scores,
                quality=aggregate(scores),
                correlation=correlation,
                backend_counts={k: v for k, v in state.items() if k != "results"},
                capture_metrics={
                    "exported": captures.exported,
                    "export_dropped": captures.export_dropped,
                },
                wall_seconds=round(time.monotonic() - started, 2),
            )
        except Exception as exc:
            report["test_error"] = type(exc).__name__ + ": " + str(exc)[:300]
            report.setdefault("ledger", ledger)
            raise
        finally:
            captures.flush(timeout=10)
            if run_id:
                validation = {
                    k: report.get(k)
                    for k in (
                        "profile",
                        "dispatch_mode",
                        "manifest_sha256",
                        "quality",
                        "backend_counts",
                        "prefix_checks",
                        "judgment_counts",
                    )
                }
                validation["correlation"] = {
                    k: v for k, v in report.get("correlation", {}).items() if k != "graph"
                }
                try:
                    backend.post(
                        "/v1/runs/end",
                        {
                            "run_id": run_id,
                            "report": {
                                "source_model": model,
                                "checks": report["checks"],
                                "test_error": report.get("test_error"),
                                "limits": {
                                    "openai_calls": manifest["source_calls"],
                                    "fireworks_calls": manifest["source_calls"] * 3,
                                },
                                "during_saturation": {"calls": len(ledger)},
                                "validation": validation,
                            },
                        },
                    )
                except Exception as exc:
                    report["backend_save_error"] = type(exc).__name__
                    report["checks"]["saved_to_backend"] = False
            output.parent.mkdir(parents=True, exist_ok=True)
            await asyncio.to_thread(output.write_text, json.dumps(report, indent=2) + "\n")
            captures.close()
    if "quality" in report:
        await asyncio.to_thread(output.with_suffix(".html").write_text, render(report))
    return report
