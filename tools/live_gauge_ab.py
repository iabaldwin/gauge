"""Live, randomized matched-batch A/B: native OpenAI versus Gauge.

Runs each arm in its own persistent process. No extra warmups or provider retries.
All requested calls, including cold connections and failures, remain in the report.
"""

from __future__ import annotations

import argparse
import json
import os
import selectors
import subprocess
import sys
import time
from pathlib import Path

import httpx
from gauge.benchmark.statistics import schedule, summarize
from gauge.config import DEFAULT_FIREWORKS_MODELS
from gauge.local.runtime import ensure_service
from gauge.local.state import write_json


class Arm:
    def __init__(self, name, model, env):
        self.process = subprocess.Popen(
            [
                sys.executable,
                str(Path(__file__).with_name("gauge_ab_worker.py")),
                name,
                "--model",
                model,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            text=True,
            bufsize=1,
            env=env,
        )
        self.selector = selectors.DefaultSelector()
        self.selector.register(self.process.stdout, selectors.EVENT_READ)
        try:
            if not self.read(30).get("ready"):
                raise RuntimeError("Benchmark worker did not start")
        except BaseException:
            self.close()
            raise

    def read(self, timeout):
        if not self.selector.select(timeout):
            raise TimeoutError("Benchmark worker timed out")
        line = self.process.stdout.readline()
        if not line:
            raise RuntimeError("Benchmark worker exited unexpectedly")
        return json.loads(line)

    def send(self, command, timeout):
        self.process.stdin.write(json.dumps(command) + "\n")
        self.process.stdin.flush()
        return self.read(timeout)

    def close(self):
        if self.process.poll() is None:
            self.process.terminate()
            try:
                self.process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()
        self.selector.close()
        self.process.stdin.close()
        self.process.stdout.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--calls-per-arm", type=int, default=1000)
    parser.add_argument("--concurrency", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=50)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--model", default="gpt-4.1-nano")
    parser.add_argument("--output", type=Path, default=Path("out/stress/ab.json"))
    parser.add_argument("--wait-seconds", type=int, default=1800)
    args = parser.parse_args()
    if not 1 <= args.calls_per_arm <= 1000 or not 1 <= args.concurrency <= 50:
        parser.error("Use 1–1000 calls per arm and concurrency 1–50")
    if not args.concurrency <= args.batch_size <= 100:
        parser.error("Batch size must be between concurrency and 100")
    if not 1 <= args.wait_seconds <= 3600:
        parser.error("Backend wait must be 1–3600 seconds")
    if not os.environ.get("OPENAI_API_KEY"):
        parser.error("Set OPENAI_API_KEY before running the benchmark")
    config = ensure_service()
    output = args.output.resolve()
    output.parent.mkdir(parents=True, exist_ok=True)
    report = {
        "mode": "ab",
        "source_model": args.model,
        "candidate_models": list(DEFAULT_FIREWORKS_MODELS),
        "samples": [],
        "batches": [],
        "checks": {},
        "performance": {},
    }
    methodology = {
        "model": args.model,
        "concurrency": args.concurrency,
        "batch_size": args.batch_size,
        "seed": args.seed,
        "calls_per_arm": args.calls_per_arm,
        "max_output_tokens": 32,
        "retries": 0,
        "timeout_seconds": 30,
        "description": "Separate persistent processes; randomized matched-batch order; identical "
        "prompts and settings. Cold connections included; no extra warmups. Latency starts after "
        "the concurrency semaphore and ends when the SDK returns. Throughput is successful calls "
        "per active batch second, excluding time spent on the other arm. Backend comparisons "
        "continue between arms. Observed differences include provider and host noise; they do "
        "not establish zero overhead or causal significance.",
    }
    workers, run_id = {}, None
    with httpx.Client(base_url=config["url"], timeout=15, trust_env=False) as backend:

        def request(method, path, body=None):
            response = backend.request(method, path, json=body)
            response.raise_for_status()
            return response.json()

        def save_progress():
            report["performance"] = {
                **summarize(report["samples"], report["batches"], args.calls_per_arm),
                "methodology": methodology,
            }
            write_json(output, report)
            request(
                "POST",
                "/v1/runs/progress",
                {"run_id": run_id, "report": report_summary()},
            )

        def report_summary():
            return {
                key: value
                for key, value in report.items()
                if key not in {"samples", "batches"}
            }

        try:
            state = request("GET", "/stats")
            if not state["comparisons_enabled"]:
                raise RuntimeError(
                    "Start the backend with FIREWORKS_API_KEY before benchmarking"
                )
            if (
                state["active_run_id"]
                or state["queued"]
                or state["active"]
                or state["judging"]["queued"]
                or state["judging"]["active"]
            ):
                raise RuntimeError(
                    "Wait for the existing backend run and queues to finish"
                )
            run = request(
                "POST",
                "/v1/runs/start",
                {
                    "name": f"A/B · {args.calls_per_arm:,} native + {args.calls_per_arm:,} Gauge",
                    "models": list(DEFAULT_FIREWORKS_MODELS),
                    "capacity": 3000,
                    "max_calls": args.calls_per_arm * len(DEFAULT_FIREWORKS_MODELS),
                    "max_output_tokens": 256,
                },
            )["run"]
            run_id = run["id"]
            report.update(
                run_id=run_id, dashboard=config["url"] + "/dashboard?run=" + run_id
            )
            save_progress()  # Also verifies that this server supports benchmark summaries.
            print(
                json.dumps({"dashboard": report["dashboard"], "report": str(output)}),
                flush=True,
            )
            # Comparison/judge credentials never enter either source process.
            env = {
                k: v
                for k, v in os.environ.items()
                if not k.startswith("GAUGE_")
                and k not in {"FIREWORKS_API_KEY", "PORTKEY_API_KEY"}
            }
            workers["native"] = Arm("native", args.model, env)
            workers["gauge"] = Arm(
                "gauge",
                args.model,
                {
                    **env,
                    "GAUGE_ENABLED": "1",
                    "GAUGE_ENDPOINT": config["url"] + "/v1/captures",
                    "GAUGE_PROJECT_ID": config["project_id"],
                    "GAUGE_RUN_ID": run_id,
                },
            )
            for batch_id, batch in enumerate(
                schedule(args.calls_per_arm, args.batch_size, args.seed)
            ):
                for arm in batch["order"]:
                    result = workers[arm].send(
                        {"indices": batch["indices"], "concurrency": args.concurrency},
                        35
                        * (
                            (len(batch["indices"]) + args.concurrency - 1)
                            // args.concurrency
                        )
                        + 15,
                    )
                    report["samples"].extend(
                        {**row, "arm": arm, "batch": batch_id}
                        for row in result["samples"]
                    )
                    report["batches"].append(
                        {
                            "arm": arm,
                            "batch": batch_id,
                            "order": batch["order"],
                            "wall_seconds": result["wall_seconds"],
                        }
                    )
                    save_progress()
                    print(
                        json.dumps(
                            {
                                "arm": arm,
                                "calls": report["performance"]["arms"][arm]["calls"],
                                "of": args.calls_per_arm,
                            }
                        ),
                        flush=True,
                    )
                    if not any(r["status"] == "completed" for r in result["samples"]):
                        raise RuntimeError(
                            "Entire source batch failed; remaining paid calls cancelled"
                        )
            report["capture"] = workers["gauge"].send({"stop": True}, 40)
            report["native_process"] = workers["native"].send({"stop": True}, 10)
            deadline, next_progress = time.monotonic() + args.wait_seconds, 0
            while True:
                state = request("GET", "/stats")
                judge = state["judging"]
                idle = not (
                    state["active"]
                    or state["queued"]
                    or judge["active"]
                    or judge["queued"]
                )
                if idle or time.monotonic() >= deadline:
                    break
                if time.monotonic() >= next_progress:
                    print(
                        json.dumps(
                            {
                                "backend_queued": state["queued"],
                                "backend_active": state["active"],
                                "judge_queued": judge["queued"],
                                "judge_active": judge["active"],
                            }
                        ),
                        flush=True,
                    )
                    next_progress = time.monotonic() + 15
                time.sleep(0.5)
            report["backend_after"] = state
            records, cursor = [], None
            while True:
                page = request(
                    "GET",
                    f"/v1/dashboard?run_id={run_id}"
                    + (f"&before={cursor}" if cursor is not None else ""),
                )
                records.extend(page["records"])
                cursor = page["next_cursor"]
                if cursor is None:
                    break
            jobs = [job for row in records for job in row["comparisons"]]
            report["comparison_counts"] = {
                status: sum(j["status"] == status for j in jobs)
                for status in ("completed", "error", "skipped", "pending", "running")
            }
            report["checks"] = {
                "both_arms_completed": all(
                    a["calls"] == args.calls_per_arm
                    for a in report["performance"]["arms"].values()
                ),
                "all_source_outputs_complete": all(
                    a["successful"] == args.calls_per_arm
                    for a in report["performance"]["arms"].values()
                ),
                "native_never_imported_gauge": not report["native_process"][
                    "gauge_imported"
                ],
                "gauge_captures_saved": len(records) == args.calls_per_arm
                and report["capture"]["flushed"],
                "backend_drained": idle,
                "all_comparisons_completed": len(jobs) == args.calls_per_arm * 3
                and all(
                    j["status"] == "completed" and j.get("finish_reason") == "stop"
                    for j in jobs
                ),
            }
        except Exception as exc:  # noqa: BLE001 - preserve partial benchmark evidence
            report["test_error"] = type(exc).__name__
            print(
                json.dumps(
                    {
                        "error": type(exc).__name__,
                        "message": str(exc)
                        if isinstance(exc, RuntimeError)
                        else "See saved report",
                    }
                ),
                flush=True,
            )
        finally:
            for worker in workers.values():
                worker.close()
            report["performance"] = {
                **summarize(report["samples"], report["batches"], args.calls_per_arm),
                "methodology": methodology,
            }
            if run_id:
                try:
                    request(
                        "POST",
                        "/v1/runs/end",
                        {"run_id": run_id, "report": report_summary()},
                    )
                except Exception as exc:  # noqa: BLE001 - preserve partial benchmark evidence
                    report["backend_save_error"] = type(exc).__name__
            write_json(output, report)
    print(
        json.dumps(
            {
                "report": str(output),
                "dashboard": report.get("dashboard"),
                "checks": report["checks"],
            }
        ),
        flush=True,
    )
    if (
        report.get("test_error")
        or report.get("backend_save_error")
        or not all(report["checks"].values())
    ):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
