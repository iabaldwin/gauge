"""Worker-owned local persistence. No provider clients and no caller-thread I/O."""

from __future__ import annotations

import json
import os
import sqlite3
import time
from pathlib import Path

from gauge.backend.store import DashboardStore
from gauge.ingestion import CaptureInbox
from gauge.local.state import bootstrap, installation_id


class LocalExporter:
    def __init__(self) -> None:
        self.project = bootstrap()
        self.installation = installation_id()
        self.path = self.project["database"]
        # Read-model schema setup must not interrupt work in an already-running service.
        DashboardStore(self.path, recover=False)
        self.inbox = CaptureInbox(self.path)
        Path(self.path).chmod(0o600)
        self.remote = None
        remote = Path(self.project["root"]) / ".gauge/remote.json"
        if remote.exists():
            from gauge.export import HTTPExporter

            config = json.loads(remote.read_text())
            self.remote = HTTPExporter(config["url"] + "/v1/captures")

    def send(self, payload: dict) -> bool:
        payload = {
            **payload,
            "project_id": self.project["project_id"],
            "installation_id": self.installation,
        }
        run_id = payload["run_id"]
        # Capture insertion and run attachment are one transaction on this worker's connection.
        run = {
            "id": run_id,
            "project_id": self.project["project_id"],
            "name": os.environ.get("GAUGE_RUN_NAME", "Application execution"),
            "created_at": payload["created_at"],
            "status": "captured",
            "kind": "automatic",
        }
        for attempt in range(3):
            try:
                self.inbox.accept(json.dumps(payload).encode(), run=run)
                if self.remote is not None:
                    # Remote failure never erases the locally persisted capture.
                    self.remote.send(payload)
                return True
            except sqlite3.OperationalError:
                if attempt == 2:
                    return False
                time.sleep(0.05 * (attempt + 1))
        return False

    def close(self) -> None:
        self.inbox.close()
        if self.remote is not None:
            self.remote.close()


def finish_run(root: Path, run_id: str, exit_code: int) -> None:
    path = root / ".gauge/captures.sqlite"
    if not path.exists():
        return
    with sqlite3.connect(path, timeout=2) as db:
        db.execute(
            "UPDATE gauge_runs SET payload=json_set(payload, '$.status', ?, "
            "'$.finished_at', ?, '$.test_exit_code', ?) WHERE id=?",
            ("completed" if exit_code == 0 else "failed", time.time(), exit_code, run_id),
        )
