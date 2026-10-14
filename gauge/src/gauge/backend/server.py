"""Loopback-only live Gauge backend for bounded integration and load tests.

Run with FIREWORKS_API_KEY and GAUGE_API_KEY set. Every accepted comparison uses
real Fireworks inference. This is a local test service, not a production server.
"""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import queue
import secrets
import threading
import time
from http.cookies import SimpleCookie
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
        self, db: str, model: str, capacity: int, max_calls: int, workers: int = 1
    ) -> None:
        self.db, self.model, self.max_calls = db, model, max_calls
        self.models = [model]
        self.output_tokens = 256
        self.workers = workers
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
            "results": [],
        }
        CaptureInbox(db).close()
        self.dashboard = DashboardStore(db)
        self.judging = JudgeQueue(self.dashboard)
        self.active_run: dict[str, Any] | None = None
        for index in range(workers):
            threading.Thread(
                target=self.consume, daemon=True, name=f"live-comparisons-{index}"
            ).start()

    def accept(self, raw: bytes, idempotency_key: str | None) -> None:
        payload = json.loads(raw)
        if payload.get("call_id") != idempotency_key:
            raise ValueError("Idempotency key mismatch")
        inbox = CaptureInbox(self.db)
        try:
            call_id, inserted = inbox.accept(raw)
            event = inbox.get(call_id)
        finally:
            inbox.close()
        with self.lock:
            if not inserted:
                self.stats["duplicates"] += 1
                return
            self.stats["received"] += 1
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
        with self.lock:
            return {
                **self.stats,
                "results": list(self.stats["results"]),
                "queued": self.jobs.qsize(),
                "paused": not self.gate.is_set(),
                "active_run_id": self.active_run["id"] if self.active_run else None,
                "pid": os.getpid(),
                "workers": self.workers,
                "models": list(self.models),
                "judging": self.judging.snapshot(),
            }

    def start_run(self, config: dict[str, Any]) -> dict[str, Any]:
        capacity, budget = config["capacity"], config["max_calls"]
        models = config.get("models", [config.get("model")])
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
                "model": models[0],
                "models": models,
                "workers": self.workers,
                "max_output_tokens": output_tokens,
            }
            self.dashboard.save_run(run)
            self.active_run = run
            with self.jobs.mutex:
                self.jobs.maxsize = capacity
            self.models, self.model, self.max_calls = list(models), models[0], budget
            self.output_tokens = output_tokens
            for key in self.stats:
                self.stats[key] = [] if key == "results" else 0
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
            self.stats["results"].clear()
            return result

    def consume(self) -> None:
        with OpenAI(
            api_key=os.environ["FIREWORKS_API_KEY"],
            base_url="https://api.fireworks.ai/inference/v1",
            max_retries=0,
            timeout=30,
            # OpenAI 3.29 accepts HTTPX and HTTPX2, but its annotations list HTTPX2 only.
            http_client=cast(
                Any, httpx.Client(proxy=os.environ.get("HTTPS_PROXY"), trust_env=False)
            ),
        ) as client:
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
                        if self.stats["calls_started"] >= self.max_calls:
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
                try:
                    self.dashboard.put(result)
                except Exception:
                    # A reporting failure must not kill the comparison worker.
                    with self.lock:
                        self.stats["dashboard_write_errors"] = (
                            self.stats.get("dashboard_write_errors", 0) + 1
                        )
                with self.lock:
                    self.stats["active"] -= 1
                    self.stats["completed" if result["status"] == "completed" else "failed"] += 1
                    self.stats["results"].append(result)
                try:
                    self.judging.submit(event, result, work["messages"])
                except Exception:
                    # Judge scheduling/storage is optional and must not kill comparison workers.
                    pass
                self.jobs.task_done()


def serve(port: int, db: str, model: str, capacity: int, max_calls: int, workers: int = 1) -> None:
    token = os.environ["GAUGE_API_KEY"]
    if not token or not os.environ.get("FIREWORKS_API_KEY"):
        raise ValueError("GAUGE_API_KEY and FIREWORKS_API_KEY must be configured")
    backend = Backend(db, model, capacity, max_calls, workers)
    session = hmac.new(token.encode(), b"gauge-dashboard-read-session", hashlib.sha256).hexdigest()
    tickets: dict[str, float] = {}
    ticket_lock = threading.Lock()

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

        def authorized(self, read_only: bool = False) -> bool:
            if read_only:
                cookie = SimpleCookie()
                try:
                    cookie.load(self.headers.get("Cookie", ""))
                    value = cookie.get("gauge_dashboard")
                    if value and hmac.compare_digest(value.value, session):
                        return True
                except (ValueError, TypeError):
                    pass
            if not hmac.compare_digest(self.headers.get("Authorization", ""), f"Bearer {token}"):
                self.reply(401, {"error": "unauthorized"})
                return False
            return True

        def do_GET(self) -> None:
            parsed = urlsplit(self.path)
            path, query = parsed.path, parse_qs(parsed.query)
            if path == "/dashboard/session":
                with ticket_lock:
                    expires = tickets.pop(query.get("ticket", [""])[0], 0)
                if expires < time.time():
                    self.send_response(303)
                    self.send_header("Location", "/dashboard?session=expired")
                    self.send_header("Cache-Control", "no-store")
                    self.send_header("Referrer-Policy", "no-referrer")
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                self.send_response(303)
                self.send_header("Location", "/dashboard")
                self.send_header(
                    "Set-Cookie",
                    f"gauge_dashboard={session}; HttpOnly; SameSite=Strict; Path=/; Max-Age=604800",
                )
                self.send_header("Cache-Control", "no-store")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
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
            if not self.authorized(read_only=path in {"/v1/dashboard", "/v1/runs", "/v1/traces"}):
                return
            if path == "/stats":
                stats = backend.snapshot()
                if query.get("summary") == ["1"]:
                    stats.pop("results", None)
                self.reply(200, stats)
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
                    stats.pop("results", None)
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
            if not self.authorized():
                return
            if self.path == "/dashboard/ticket":
                ticket = secrets.token_urlsafe(32)
                with ticket_lock:
                    for old, expiry in list(tickets.items()):
                        if expiry < time.time():
                            tickets.pop(old)
                    if len(tickets) >= 20:
                        tickets.pop(next(iter(tickets)))
                    tickets[ticket] = time.time() + 1800
                self.reply(200, {"path": "/dashboard/session?ticket=" + ticket, "expires_in": 1800})
                return
            self.connection.settimeout(3)
            try:
                length = int(self.headers.get("Content-Length", "0"))
                if self.headers.get("Transfer-Encoding") or not 0 < length <= MAX_ENVELOPE_BYTES:
                    self.reply(413, {"error": "invalid body length"})
                    return
                raw = self.rfile.read(length)
                if len(raw) != length:
                    raise ValueError("Incomplete body")
                if self.path == "/v1/runs/start":
                    self.reply(201, {"run": backend.start_run(json.loads(raw))})
                elif self.path == "/v1/runs/end":
                    backend.end_run(json.loads(raw))
                    self.reply(200, {"saved": True})
                elif self.path in {"/v1/runs/clear/preview", "/v1/runs/clear"}:
                    self.reply(
                        200,
                        backend.maintain_runs(json.loads(raw), clear=self.path == "/v1/runs/clear"),
                    )
                elif self.path == "/control":
                    paused = json.loads(raw)["paused"]
                    if type(paused) is not bool:
                        raise ValueError("paused must be boolean")
                    backend.gate.clear() if paused else backend.gate.set()
                    self.reply(200, backend.snapshot())
                elif self.path == "/v1/captures":
                    backend.accept(raw, self.headers.get("Idempotency-Key"))
                    self.reply(202, {"accepted": True})
                elif self.path == "/v1/judgments":
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

    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
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
    parser.add_argument("--model", default="accounts/fireworks/models/gpt-oss-120b")
    parser.add_argument("--capacity", type=int, default=1)
    parser.add_argument("--max-calls", type=int, default=8)
    parser.add_argument("--workers", type=int, default=1)
    args = parser.parse_args()
    if not 1 <= args.capacity <= 3000 or not 1 <= args.max_calls <= 3000:
        parser.error("Local test limits: capacity and max-calls 1–3000")
    if not 1 <= args.workers <= 16:
        parser.error("Comparison workers must be 1–16")
    Path(args.db).parent.mkdir(parents=True, exist_ok=True)
    serve(args.port, args.db, args.model, args.capacity, args.max_calls, args.workers)


if __name__ == "__main__":
    main()
