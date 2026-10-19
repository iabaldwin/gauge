"""Start the separately owned, credential-free local service from terminal tooling."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from pathlib import Path

import httpx

from gauge.local.state import bootstrap, locked, write_json


def ensure_service(root: Path | None = None) -> dict:
    project = bootstrap(root)
    folder = Path(project["root"]) / ".gauge"
    with locked(folder / "service.lock"):
        state = folder / "service.json"
        if state.exists():
            config = json.loads(state.read_text())
            try:
                with httpx.Client(timeout=1, trust_env=False) as client:
                    health = client.get(config["url"] + "/stats?summary=1").json()
                if (
                    health.get("project_id") == project["project_id"]
                    and config.get("db") == project["database"]
                ):
                    return config
            except (httpx.HTTPError, ValueError, KeyError):
                pass
        settings = folder / "settings.json"
        policy = json.loads(settings.read_text()) if settings.exists() else {}
        log_path = folder / "service.log"
        with log_path.open("wb") as log:
            process = subprocess.Popen(
                [
                    sys.executable,
                    "-m",
                    "gauge.backend",
                    "--port",
                    "0",
                    "--db",
                    project["database"],
                    "--workers",
                    "4",
                    "--capacity",
                    "1000",
                    "--max-calls",
                    str(policy.get("budget", 300)),
                ],
                cwd=project["root"],
                env={**os.environ, "GAUGE_PROJECT_ID": project["project_id"]},
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=log,
                start_new_session=True,
            )
        deadline = time.monotonic() + 15
        while time.monotonic() < deadline:
            for line in log_path.read_text(errors="replace").splitlines():
                try:
                    ready = json.loads(line)
                except ValueError:
                    continue
                if ready.get("ready"):
                    config = {
                        "url": f"http://127.0.0.1:{ready['port']}",
                        "pid": process.pid,
                        "db": project["database"],
                        "project_id": project["project_id"],
                    }
                    write_json(state, config)
                    return config
            if process.poll() is not None:
                break
            time.sleep(0.1)
        if process.poll() is None:
            process.terminate()
        raise RuntimeError("Gauge service failed to start; inspect .gauge/service.log")
