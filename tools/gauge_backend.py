"""Manage the persistent loopback Gauge backend independently of test runners."""

from __future__ import annotations

import argparse
import fcntl
import json
import os
import secrets
import signal
import sqlite3
import subprocess
import sys
import time
import uuid
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parents[1]
STATE_DIR = ROOT / ".gauge"
STATE = STATE_DIR / "backend.json"


def settings() -> dict:
    return json.loads(STATE.read_text()) if STATE.exists() else {}


def health(config: dict) -> dict | None:
    if not config:
        return None
    try:
        with httpx.Client(trust_env=False, timeout=1) as client:
            response = client.get(
                config["url"] + "/stats",
                headers={"Authorization": "Bearer " + config["token"]},
            )
            response.raise_for_status()
            return response.json()
    except (httpx.HTTPError, ValueError, KeyError):
        return None


def import_report(report_path: Path, destination: Path) -> None:
    from gauge.backend.store import DashboardStore
    from gauge.ingestion import CaptureInbox

    report = json.loads(report_path.read_text())
    source = Path(report["backend_database"]).resolve()
    if source == destination.resolve():
        raise ValueError("Import source must differ from the persistent database")
    CaptureInbox(str(destination)).close()
    store = DashboardStore(str(destination))
    run_id = "run_" + uuid.uuid5(uuid.NAMESPACE_URL, str(report_path.resolve())).hex
    with sqlite3.connect(source.as_uri() + "?mode=ro", uri=True) as old:
        captures = old.execute("SELECT * FROM gauge_captures").fetchall()
        jobs = old.execute("SELECT call_id, payload FROM gauge_comparisons").fetchall()
    with sqlite3.connect(destination) as db:
        for row in captures:
            prior = db.execute(
                "SELECT digest FROM gauge_captures WHERE call_id = ?", (row[0],)
            ).fetchone()
            if prior and prior[0] != row[1]:
                raise ValueError("Capture conflict while importing saved run")
            db.execute("INSERT OR IGNORE INTO gauge_captures VALUES (?, ?, ?, ?)", row)
            db.execute(
                "INSERT OR IGNORE INTO gauge_run_captures VALUES (?, ?)",
                (row[0], run_id),
            )
        db.executemany(
            "INSERT OR IGNORE INTO gauge_comparisons VALUES (?, ?, ?)",
            [
                (call_id, json.loads(payload)["candidate"], payload)
                for call_id, payload in jobs
            ],
        )
        run = {
            "id": run_id,
            "name": f"Saturation · {report['limits']['openai_calls']} OpenAI calls",
            "created_at": min(
                (json.loads(r[3])["created_at"] for r in captures), default=time.time()
            ),
            "status": "completed"
            if report.get("checks") and all(report["checks"].values())
            else "failed",
            "summary": store.report_summary(report),
        }
        db.execute(
            "INSERT OR REPLACE INTO gauge_runs VALUES (?, ?)", (run_id, json.dumps(run))
        )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["start", "status", "stop", "open", "judge"])
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--workers", type=int, default=1)
    parser.add_argument("--import-report", type=Path)
    parser.add_argument("--run-id")
    parser.add_argument("--call-id")
    parser.add_argument(
        "--limit", type=int, default=30, help="Judge at most 1–100 saved pairs"
    )
    args = parser.parse_args()
    if not 1 <= args.workers <= 16:
        parser.error("Comparison workers must be 1–16")
    STATE_DIR.mkdir(mode=0o700, exist_ok=True)
    with (STATE_DIR / "manager.lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        config = settings()
        current = health(config)
        if args.command == "start" and not current:
            if not os.environ.get("FIREWORKS_API_KEY"):
                parser.error("FIREWORKS_API_KEY must be set in the backend environment")
            db = ROOT / "out/gauge-backend.sqlite"
            db.parent.mkdir(exist_ok=True)
            if args.import_report:
                import_report(args.import_report, db)
            token = (
                config.get("token")
                or os.environ.get("GAUGE_API_KEY")
                or secrets.token_urlsafe(32)
            )
            with (STATE_DIR / "backend.log").open("ab") as log:
                process = subprocess.Popen(
                    [
                        sys.executable,
                        "-m",
                        "gauge.backend",
                        "--port",
                        str(args.port),
                        "--db",
                        str(db),
                        "--capacity",
                        "4",
                        "--max-calls",
                        "8",
                        "--workers",
                        str(args.workers),
                    ],
                    cwd=ROOT,
                    env={**os.environ, "GAUGE_API_KEY": token},
                    stdin=subprocess.DEVNULL,
                    stdout=log,
                    stderr=log,
                    start_new_session=True,
                )
            config = {
                "pid": process.pid,
                "url": f"http://127.0.0.1:{args.port}",
                "db": str(db),
                "token": token,
            }
            fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as file:
                json.dump(config, file)
            for _ in range(50):
                current = health(config)
                if current:
                    break
                if process.poll() is not None:
                    break
                time.sleep(0.1)
            if not current:
                raise SystemExit("Backend failed to start. See .gauge/backend.log")
        elif args.command == "start" and args.import_report:
            parser.error(
                "Stop the backend before importing a historical report, "
                "then start it with --import-report"
            )
        if args.command == "stop" and current:
            if current.get("pid") != config["pid"]:
                raise SystemExit(
                    "Backend process identity changed; refusing to stop an unrelated process"
                )
            os.kill(config["pid"], signal.SIGTERM)
            print("Backend stopped; saved runs remain in " + config["db"])
            return
        if args.command == "judge":
            if not current:
                raise SystemExit("Start the backend first")
            if not 1 <= args.limit <= 100:
                parser.error("Judge limit must be 1–100")
            with httpx.Client(trust_env=False, timeout=5) as client:
                response = client.post(
                    config["url"] + "/v1/judgments",
                    headers={"Authorization": "Bearer " + config["token"]},
                    json={
                        "run_id": args.run_id,
                        "call_id": args.call_id,
                        "limit": args.limit,
                    },
                )
                if not response.is_success:
                    raise SystemExit("Judge scheduling failed: " + response.text)
                print(json.dumps(response.json()))
            return
        if args.command == "open":
            if not current:
                raise SystemExit(
                    "Start the backend first: gauge/.venv/bin/python tools/gauge_backend.py start"
                )
            with httpx.Client(trust_env=False, timeout=3) as client:
                response = client.post(
                    config["url"] + "/dashboard/ticket",
                    headers={"Authorization": "Bearer " + config["token"]},
                )
                response.raise_for_status()
                # One-use, 30-minute link; never expose the API key itself.
                print(config["url"] + response.json()["path"])
            return
        print(
            json.dumps(
                {
                    "running": bool(current),
                    "dashboard": config.get("url", "") + "/dashboard",
                    "database": config.get("db"),
                    "pid": current.get("pid") if current else None,
                }
            )
        )


if __name__ == "__main__":
    main()
