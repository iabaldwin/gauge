"""Internal Gauge service. No Gauge authentication; project IDs segment results.

Provider credentials are needed only for comparisons/judging. Local capture and
inspection work without them. Inference runs in this service, never in the SDK.
"""

from __future__ import annotations

import argparse
import json
import os
import queue
import secrets
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlsplit

import httpx
from openai import OpenAI

from gauge.assets import asset
from gauge.backend.judge import RUBRIC
from gauge.backend.judging import JudgeQueue
from gauge.backend.maintenance import MaintenanceConflict, RunMaintenance
from gauge.backend.replay import ReplayUnavailable, materialize, response_text
from gauge.backend.store import DashboardStore
from gauge.ingestion import CaptureConflict, CaptureInbox
from gauge.wire import MAX_ENVELOPE_BYTES

ASSETS = {
    "/": (asset("index.html"), "text/html; charset=utf-8"),
    "/dashboard": (asset("index.html"), "text/html; charset=utf-8"),
    "/dashboard/": (asset("index.html"), "text/html; charset=utf-8"),
    "/dashboard/app.js": (asset("app.js"), "text/javascript; charset=utf-8"),
    "/dashboard/gauge-adapter.js": (
        asset("gauge-adapter.js"),
        "text/javascript; charset=utf-8",
    ),
    "/dashboard/dashboard.css": (asset("dashboard.css"), "text/css"),
    "/dashboard/validation-summary.js": (
        asset("validation-summary.js"),
        "text/javascript; charset=utf-8",
    ),
    "/pixel-lab/pixel-lab.js": (asset("pixel-lab.js"), "text/javascript; charset=utf-8"),
    "/pixel-lab/pixel-lab.css": (asset("pixel-lab.css"), "text/css"),
}


class Backend:
    def __init__(
        self,
        db: str,
        model: str | None,
        capacity: int,
        max_calls: int,
        workers: int = 1,
        *,
        project_id: str | None = None,
    ) -> None:
        self.db, self.max_calls = db, max_calls
        from gauge.config import comparison_models

        self.models = [model] if model else list(comparison_models(os.environ))
        self.output_tokens = 256
        self.workers = workers
        self.project_id = project_id or os.environ.get("GAUGE_PROJECT_ID")
        self.comparisons_enabled = bool(os.environ.get("FIREWORKS_API_KEY"))
        self.jobs: queue.Queue[dict[str, Any]] = queue.Queue(capacity)
        self.gate = threading.Event()
        self.gate.set()
        self.lock = threading.Lock()
        self.stats: dict[str, Any] = {
            "received": 0,
            "comparison_jobs": 0,
            "duplicates": 0,
            "queue_dropped": 0,
            "ineligible": 0,
            "calls_started": 0,
            "active": 0,
            "completed": 0,
            "failed": 0,
        }
        CaptureInbox(db).close()
        self.dashboard = DashboardStore(db)
        self.judging = JudgeQueue(self.dashboard)
        self.active_run: dict[str, Any] | None = None
        policy_path = Path(db).parent / "settings.json"
        if policy_path.exists():
            self.policy = json.loads(policy_path.read_text())
            self.models = self.policy["models"]
            self.max_calls = self.policy["budget"]
            self.output_tokens = self.policy["max_output_tokens"]
        threading.Thread(target=self.watch, daemon=True, name="gauge-dispatch").start()
        for index in range(workers if self.comparisons_enabled else 0):
            threading.Thread(
                target=self.consume, daemon=True, name=f"live-comparisons-{index}"
            ).start()

    def watch(self) -> None:
        while True:
            try:
                policy = getattr(self, "policy", {})
                if policy.get("automatic") and self.comparisons_enabled:
                    self.compare({"limit": 1000})
            except Exception:
                with self.lock:
                    self.stats["dispatch_errors"] = self.stats.get("dispatch_errors", 0) + 1
            time.sleep(1)

    def configure(self, config: dict) -> dict:
        from gauge.backend.policy import configure

        return configure(self, config)

    def compare(self, config: dict) -> dict:
        from gauge.backend.policy import dispatch

        return dispatch(self, config)

    def accept(self, raw: bytes, idempotency_key: str | None, *, compare: bool = True) -> None:
        payload = json.loads(raw)
        if self.project_id and payload.get("project_id") != self.project_id:
            raise ValueError("Capture project does not match the selected project")
        if payload.get("call_id") != idempotency_key:
            raise ValueError("Idempotency key mismatch")
        inbox = CaptureInbox(self.db)
        try:
            run = self.active_run
            if run is None and payload.get("run_id") and payload.get("project_id"):
                run = {
                    "id": payload["run_id"],
                    "project_id": payload["project_id"],
                    "name": "Application execution",
                    "status": "captured",
                    "kind": "automatic",
                    "created_at": payload["created_at"],
                }
            call_id, inserted = inbox.accept(raw, run=run)
            event = inbox.get(call_id)
        finally:
            inbox.close()
        with self.lock:
            if not inserted:
                self.stats["duplicates"] += 1
                return
            self.stats["received"] += 1
            if not self.comparisons_enabled or not compare:
                return
            if self.active_run:
                self.dashboard.attach(call_id, self.active_run["id"])
            messages, reason = None, (event or {}).get("ineligible_reason")
            if event:
                try:
                    messages = materialize(event, self.dashboard.response_capture)
                    reason = None
                except ReplayUnavailable as exc:
                    reason = str(exc)
            eligible = messages is not None
            if not eligible:
                self.stats["ineligible"] += 1
            for model in self.models:
                job = {
                    "id": "comparison_" + secrets.token_hex(16),
                    "attempt_id": "attempt_" + secrets.token_hex(16),
                    "call_id": call_id,
                    "candidate": model,
                    "status": "pending",
                    "max_output_tokens": self.output_tokens,
                    "received_at": time.time(),
                }
                self.stats["comparison_jobs"] += 1
                if not eligible:
                    job.update(status="skipped", reason=reason)
                    job["finished_at"] = time.time()
                    self.dashboard.put(job)
                    continue
                self.dashboard.put(job)
                try:
                    self.jobs.put_nowait({"event": event, "job": job, "messages": messages})
                except queue.Full:
                    self.stats["queue_dropped"] += 1
                    job.update(
                        status="skipped", reason="Comparison queue full", finished_at=time.time()
                    )
                    self.dashboard.put(job)

    def snapshot(self) -> dict[str, Any]:
        counts = self.dashboard.comparison_counts()
        pending_writes = len(self.dashboard.reporting.snapshot())
        with self.lock:
            return {
                **self.stats,
                "received": self.dashboard.page()["total"],
                "comparison_jobs": sum(counts.values()),
                "completed": counts.get("completed", 0),
                "failed": counts.get("error", 0),
                "queued": self.jobs.qsize(),
                "paused": not self.gate.is_set(),
                "active_run_id": self.active_run["id"] if self.active_run else None,
                "pid": os.getpid(),
                "workers": self.workers,
                "models": list(self.models),
                "project_id": self.project_id,
                "comparisons_enabled": self.comparisons_enabled,
                "pending_result_writes": pending_writes,
                "capture_diagnostics": [
                    json.loads(p.read_text())
                    for p in (Path(self.db).parent / "diagnostics").glob("*.json")
                ],
                "configuration_note": (
                    f"{pending_writes} finished result(s) awaiting storage. "
                    "Outputs are available in memory; backend workers are retrying writes."
                )
                if pending_writes
                else None
                if self.comparisons_enabled
                else (
                    "Original captures saved. Set FIREWORKS_API_KEY "
                    "in the backend environment to compare."
                ),
                "judging": self.judging.snapshot(),
            }

    def start_run(self, config: dict[str, Any]) -> dict[str, Any]:
        capacity, budget = config["capacity"], config["max_calls"]
        models = config["models"]
        output_tokens = config.get("max_output_tokens", 256)
        if (
            type(capacity) is not int
            or not 1 <= capacity <= 3000
            or type(budget) is not int
            or not 1 <= budget <= 3000
            or not isinstance(models, list)
            or not 1 <= len(models) <= 3
            or any(not isinstance(m, str) or not 1 <= len(m) <= 256 for m in models)
            or len(set(models)) != len(models)
            or type(output_tokens) is not int
            or not 1 <= output_tokens <= 2048
        ):
            raise ValueError("Invalid run configuration")
        with self.lock:
            if self.active_run or self.stats["active"] or self.jobs.unfinished_tasks:
                raise CaptureConflict("Backend already has an active run or comparison")
            run = {
                "id": "run_" + secrets.token_hex(12),
                "name": str(config.get("name", "Saturation test"))[:100],
                "created_at": time.time(),
                "status": "running",
                "models": models,
                "workers": self.workers,
                "max_output_tokens": output_tokens,
            }
            self.dashboard.save_run(run)
            self.active_run = run
            with self.jobs.mutex:
                self.jobs.maxsize = capacity
            self.models, self.max_calls = list(models), budget
            self.output_tokens = output_tokens
            for key in self.stats:
                self.stats[key] = 0
            self.gate.set()
            return run

    def end_run(self, payload: dict[str, Any]) -> None:
        with self.lock:
            if not self.active_run or payload["run_id"] != self.active_run["id"]:
                raise CaptureConflict("Run is not active")
            report = payload["report"]
            if not isinstance(report, dict):
                raise ValueError("Invalid report")
            run = dict(self.active_run)
            run.update(
                status="completed"
                if report.get("checks")
                and all(report["checks"].values())
                and not report.get("test_error")
                else "failed",
                finished_at=time.time(),
                summary=DashboardStore.report_summary(report),
            )
            self.dashboard.save_run(run)
            self.active_run = None
            self.gate.set()

    def maintain_runs(self, config: dict[str, Any], *, clear: bool) -> dict:
        ids = config.get("run_ids")
        if ids is not None and (
            not isinstance(ids, list)
            or not ids
            or any(not isinstance(i, str) or not 1 <= len(i) <= 256 for i in ids)
            or len(set(ids)) != len(ids)
        ):
            raise ValueError("Invalid run selection")
        if ids is None and config.get("all") is not True:
            raise ValueError("Select run_ids or explicitly all runs")
        with self.lock, self.judging.lock:
            if clear and (self.active_run or self.jobs.unfinished_tasks or self.judging.inflight):
                raise MaintenanceConflict("Backend must be idle before clearing saved results")
            maintenance = RunMaintenance(self.db)
            if not clear:
                return maintenance.preview(ids)
            fingerprint = config.get("fingerprint")
            if not isinstance(fingerprint, str) or len(fingerprint) != 64:
                raise ValueError("A current cleanup preview is required")
            if ids is None:
                raise ValueError("Clear requires the exact run_ids returned by preview")
            result = maintenance.clear(ids, fingerprint)
            return result

    def consume(self) -> None:
        client = None
        while True:
            work = self.jobs.get()
            event, job = work["event"], work["job"]
            with self.lock:
                self.stats["active"] += 1
            self.gate.wait()  # Local fault injection; never blocks HTTP ingestion.
            started = time.perf_counter()
            result: dict[str, Any] = dict(job)
            try:
                with self.lock:
                    if (
                        not job.get("configuration_id")
                        and self.stats["calls_started"] >= self.max_calls
                    ):
                        raise ValueError("Live comparison call budget exhausted")
                    self.stats["calls_started"] += 1
                reference = response_text(event["response"])
                result.update(status="running", started_at=time.time())
                self.dashboard.put(result)
                parameters: dict[str, Any] = {
                    "model": job["candidate"],
                    "messages": work["messages"],
                    "max_tokens": job["max_output_tokens"],
                }
                if "gpt-oss" in job["candidate"]:
                    parameters["reasoning_effort"] = "low"
                client = client or OpenAI(
                    api_key=os.environ["FIREWORKS_API_KEY"],
                    base_url="https://api.fireworks.ai/inference/v1",
                    max_retries=0,
                    timeout=30,
                    http_client=cast(
                        Any, httpx.Client(proxy=os.environ.get("HTTPS_PROXY"), trust_env=False)
                    ),
                )
                output = client.chat.completions.create(**parameters)
                candidate = output.choices[0].message.content
                result.update(
                    input=work["messages"][-1]["content"],
                    reference=reference,
                    candidate_output=candidate,
                    finish_reason=output.choices[0].finish_reason,
                    agreement=(reference == candidate)
                    if output.choices[0].finish_reason == "stop"
                    else None,
                    usage=output.usage.model_dump() if output.usage else None,
                    status="completed",
                )
            except Exception as exc:
                result.update(
                    status="error",
                    error=type(exc).__name__,
                    http_status=getattr(exc, "status_code", None),
                )
            result["latency_ms"] = round((time.perf_counter() - started) * 1000, 2)
            result["finished_at"] = time.time()
            self.dashboard.finish_comparison(result)
            with self.lock:
                self.stats["active"] -= 1
                self.stats["completed" if result["status"] == "completed" else "failed"] += 1
            try:
                self.judging.submit(event, result, work["messages"])
            except Exception:
                # Judge scheduling/storage is optional and must not kill comparison workers.
                pass
            self.jobs.task_done()


def serve(
    port: int,
    db: str,
    model: str | None,
    capacity: int,
    max_calls: int,
    workers: int = 1,
    *,
    projects_dir: str | None = None,
    host: str = "127.0.0.1",
) -> None:
    from gauge.backend.projects import ProjectRegistry

    default_backend = Backend(db, model, capacity, max_calls, workers)
    registry = ProjectRegistry(
        Path(projects_dir) if projects_dir else None,
        default_backend,
        lambda path, project: Backend(
            path, model, capacity, max_calls, workers, project_id=project
        ),
    )

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args: Any) -> None:
            pass

        def reply(self, status: int, body: dict[str, Any]) -> None:
            raw = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            try:
                self.wfile.write(raw)
            except (BrokenPipeError, ConnectionResetError):
                pass

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            if path in ASSETS:
                file, mime = ASSETS[path]
                try:
                    raw = file.read_bytes()
                except OSError:
                    self.reply(
                        404, {"error": "Dashboard assets missing; run from the full checkout"}
                    )
                    return
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(raw)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
                    "connect-src 'self'; img-src 'self' data:; frame-ancestors 'none'; "
                    "base-uri 'none'; form-action 'self'",
                )
                self.end_headers()
                try:
                    self.wfile.write(raw)
                except (BrokenPipeError, ConnectionResetError):
                    pass
                return
            if path == "/v1/projects":
                self.reply(200, {"projects": registry.list()})
                return
            try:
                backend = registry.select(query.get("project_id", [None])[0])
            except ValueError as exc:
                self.reply(400, {"error": str(exc)})
                return
            if path == "/stats":
                self.reply(200, backend.snapshot())
            elif path == "/v1/runs":
                self.reply(200, {"runs": backend.dashboard.runs()})
            elif path == "/v1/traces":
                self.reply(
                    200,
                    {
                        "schema_version": 1,
                        "calls": backend.dashboard.trace_graph(query.get("run_id", [None])[0]),
                    },
                )
            elif path == "/v1/dashboard":
                try:
                    stats = backend.snapshot()
                    self.reply(
                        200,
                        {
                            "schema_version": 2,
                            **backend.dashboard.page(
                                query.get("run_id", [None])[0],
                                int(query["before"][0]) if "before" in query else None,
                            ),
                            "stats": stats,
                            "limit": 100,
                        },
                    )
                except Exception:
                    self.reply(503, {"error": "Dashboard temporarily unavailable"})
            else:
                self.reply(404, {})

        def do_POST(self) -> None:
            self.connection.settimeout(3)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.headers.get("Transfer-Encoding") or not 0 < length <= MAX_ENVELOPE_BYTES:
                    self.reply(413, {"error": "invalid body length"})
                    return
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("Incomplete body")
                parsed = urlsplit(self.path)
                path = parsed.path
                config = json.loads(raw)
                if not isinstance(config, dict):
                    raise ValueError("JSON object required")
                if path == "/v1/projects":
                    self.reply(201, {"project": registry.create(config)})
                    return
                project_id = parse_qs(parsed.query).get("project_id", [config.get("project_id")])[0]
                backend = registry.select(project_id)
                if path == "/v1/config":
                    self.reply(200, backend.configure(json.loads(raw)))
                elif path == "/v1/comparisons":
                    self.reply(202, backend.compare(json.loads(raw)))
                elif path == "/v1/runs/start":
                    self.reply(201, {"run": backend.start_run(json.loads(raw))})
                elif path == "/v1/runs/end":
                    backend.end_run(json.loads(raw))
                    self.reply(200, {"saved": True})
                elif path in {"/v1/runs/clear/preview", "/v1/runs/clear"}:
                    self.reply(
                        200,
                        backend.maintain_runs(json.loads(raw), clear=path == "/v1/runs/clear"),
                    )
                elif path == "/control":
                    paused = json.loads(raw)["paused"]
                    if type(paused) is not bool:
                        raise ValueError("paused must be boolean")
                    backend.gate.clear() if paused else backend.gate.set()
                    self.reply(200, backend.snapshot())
                elif path == "/v1/runs/import":
                    if (
                        config.get("project_id") != backend.project_id
                        or not isinstance(config.get("id"), str)
                        or not isinstance(config.get("name"), str)
                        or config.get("status") not in {"captured", "completed", "failed"}
                    ):
                        raise ValueError("Invalid imported run")
                    backend.dashboard.save_run(config)
                    self.reply(200, {"saved": True})
                elif path in {"/v1/captures", "/v1/import"}:
                    backend.accept(
                        raw,
                        self.headers.get("Idempotency-Key"),
                        compare=path == "/v1/captures" and backend.active_run is not None,
                    )
                    self.reply(202, {"accepted": True})
                elif path == "/v1/judgments":
                    config = json.loads(raw)
                    limit = config.get("limit", 30)
                    run_id, call_id = config.get("run_id"), config.get("call_id")
                    if (
                        type(limit) is not int
                        or not 1 <= limit <= 100
                        or any(
                            v is not None and (not isinstance(v, str) or len(v) > 256)
                            for v in (run_id, call_id)
                        )
                    ):
                        raise ValueError("Invalid judgment selection")
                    if not backend.judging.enabled:
                        self.reply(409, {"error": "JEV disabled or PORTKEY_API_KEY missing"})
                        return
                    rows = backend.dashboard.judgment_work(
                        run_id=run_id, call_id=call_id, limit=limit, rubric_version=RUBRIC
                    )
                    accepted = sum(backend.judging.submit(event, job) for event, job in rows)
                    self.reply(202, {"accepted": accepted, "judging": backend.judging.snapshot()})
                else:
                    self.reply(404, {})
            except MaintenanceConflict as exc:
                self.reply(409, {"error": str(exc)})
            except CaptureConflict:
                self.reply(409, {"error": "conflicting capture"})
            except (ValueError, KeyError, TypeError, RecursionError):
                self.reply(400, {"error": "invalid capture"})
            except Exception:
                self.reply(503, {"error": "ingestion unavailable"})

    server = ThreadingHTTPServer((host, port), Handler)
    server.daemon_threads = True
    print(json.dumps({"ready": True, "port": server.server_port}), flush=True)
    try:
        server.serve_forever()
    finally:
        server.server_close()


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--db", default="out/gauge-local.sqlite")
    parser.add_argument("--model")
    parser.add_argument("--projects-dir")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--capacity", type=int, default=1)
    parser.add_argument("--max-calls", type=int, default=8)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.capacity <= 3000 or not 1 <= args.max_calls <= 3000:
        parser.error("Local test limits: capacity and max-calls 1–3000")
    if not 1 <= args.workers <= 16:
        parser.error("Comparison workers must be 1–16")
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    serve(
        args.port,
        args.db,
        args.model,
        args.capacity,
        args.max_calls,
        args.workers,
        projects_dir=args.projects_dir,
        host=args.host,
    )


if __name__ == "__main__":
    main()
