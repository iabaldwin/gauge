"""Bounded benchmark orchestration. All comparison work stays in the separate backend."""

from __future__ import annotations

import asyncio
import json
import os
import time
from pathlib import Path

import httpx

from gauge import captures
from gauge.evaluation.scoring import aggregate, expected_for, grade
from gauge.openai import AsyncOpenAI
from gauge.validation.backend import BackendClient, source_options
from gauge.validation.checks import evaluate
from gauge.validation.dispatch import Dispatcher
from gauge.validation.drain import drain, preflight
from gauge.validation.prefixes import verify_prefixes
from gauge.validation.records import read_suite
from gauge.validation.report import render

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
    if manifest.get("dispatch_mode") != "quality-suite":
        raise ValueError("Run requires a quality-suite manifest; prepare a new suite")
    if any(s.protocol not in {"chat", "responses"} for s in scenarios):
        raise ValueError("Quality cases require self-contained chat or Responses input")
    if not 1 <= manifest["source_calls"] <= 1000:
        raise ValueError("Source call budget must be 1–1000")
    options = source_options()
    ledger: list[dict] = []
    source_rows: list[dict] = []
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
        "candidate_max_output_tokens": 2048,
        "source_max_output_tokens": 1024,
        "checks": {},
    }
    run_id = None
    with BackendClient(connection) as backend:
        try:
            state = backend.get("/stats")
            judges = state.get("judging", {})
            references = state["reference_judging"]
            if (
                state.get("active_run_id")
                or state["active"]
                or state["queued"]
                or judges.get("active")
                or judges.get("queued")
                or references["pending"]
            ):
                raise ValueError("Backend must be idle before validation")
            if not judges.get("enabled") or judges.get("calls_started", 0):
                raise ValueError("Quality validation needs a fresh enabled judge budget")
            reference_budget = (
                sum(len(s.turns) for s in scenarios if s.task in {"coqa", "quac"}) * 4
            )
            if reference_budget and (
                not references["enabled"]
                or references["budget"] - references["calls_finished"] < reference_budget
            ):
                raise ValueError(
                    "Insufficient enabled JEV reference budget; restart the idle backend"
                )
            result = backend.post(
                "/v1/runs/start",
                {
                    "name": (
                        f"Quality suite · {manifest['profile']} · {manifest['source_calls']} calls"
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
                GAUGE_ENABLED="1",
                GAUGE_RUN_ID=run_id,
            )
            semaphore = asyncio.Semaphore(concurrency)
            async with AsyncOpenAI(
                **options,
                max_retries=0,
                timeout=30,
                http_client=httpx.AsyncClient(proxy=os.environ.get("HTTPS_PROXY"), trust_env=False),
            ) as client:
                dispatcher = Dispatcher(
                    client, model, report["source_max_output_tokens"], ledger, source_rows
                )

                async def episode(scenario, start=0):
                    async with semaphore:
                        for index in range(start, len(scenario.turns)):
                            await dispatcher.turn(scenario, index)
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

                # Check exactly one turn before spending the rest of the fixture budget.
                if not await dispatcher.turn(scenarios[0], 0):
                    raise RuntimeError(
                        "First source call failed; remaining budget was not dispatched"
                    )
                if not captures.flush(timeout=15) or captures.exported != 1:
                    raise RuntimeError("First capture was not exported")
                await preflight(backend, run_id)
                await asyncio.gather(episode(scenarios[0], 1), *(episode(s) for s in scenarios[1:]))
            drained = captures.flush(timeout=60)
            state, records = await drain(backend, run_id, wait_seconds)
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
                                expected_for(scenario, truth["turn"]),
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
            prefix_checks = verify_prefixes(records, ledger, scenarios)
            report["prefix_checks"] = prefix_checks
            checks["request_prefixes_correct"] = prefix_checks["passed"]
            eligible = [
                j
                for r in records
                for j in r["comparisons"]
                if j["status"] == "completed" and j.get("finish_reason") == "stop"
            ]
            checks["all_eligible_pairs_judged"] = bool(eligible) and all(
                j.get("judgment", {}).get("status") in {"completed", "uncertain"} for j in eligible
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
                backend_counts=state,
                capture_metrics={
                    "exported": captures.exported,
                    "export_dropped": captures.export_dropped,
                },
                wall_seconds=round(time.monotonic() - started, 2),
            )
            from gauge.validation.agreement import evaluate as evaluate_agreement

            report["agreement"] = await evaluate_agreement(
                backend, report, judge=True, wait_seconds=wait_seconds
            )
            checks["reference_judgments_complete"] = not any(
                report["agreement"]["statuses"].get(status, 0)
                for status in ("unjudged", "pending", "error", "skipped")
            )
            checks["native_checkers_succeeded"] = not any(
                output.get("score", {}).get("reason", "").startswith("checker_error:")
                for conversation in report["agreement"]["conversations"]
                for turn in conversation["turns"]
                for output in turn["outputs"].values()
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
