"""Previewed, transactional cleanup of saved runs. No provider calls or SDK imports."""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import sqlite3
import time
from pathlib import Path


class MaintenanceConflict(ValueError):
    pass


class RunMaintenance:
    def __init__(self, path: str):
        self.path = path

    @staticmethod
    def _plan(db: sqlite3.Connection, run_ids: list[str] | None) -> dict:
        runs = db.execute("SELECT id, payload FROM gauge_runs ORDER BY id").fetchall()
        selected = [row for row in runs if run_ids is None or row[0] in run_ids]
        if run_ids is not None and set(run_ids) != {r[0] for r in selected}:
            raise ValueError("One or more selected runs no longer exist")
        if any(json.loads(row[1]).get("status") == "running" for row in selected):
            raise MaintenanceConflict("An active run cannot be cleared")
        ids = [r[0] for r in selected]
        marks = ",".join("?" for _ in ids) or "NULL"
        captures = db.execute(
            "SELECT c.call_id, c.payload FROM gauge_captures c "
            "JOIN gauge_run_captures r ON c.call_id = r.call_id "
            f"WHERE r.run_id IN ({marks}) ORDER BY c.call_id",
            ids,
        ).fetchall()
        comparisons = db.execute(
            "SELECT j.call_id, j.candidate, j.payload FROM gauge_comparisons j "
            "JOIN gauge_run_captures r ON j.call_id = r.call_id "
            f"WHERE r.run_id IN ({marks}) ORDER BY j.call_id, j.candidate",
            ids,
        ).fetchall()
        # Keep surviving traces usable when their provider-held context points into another run.
        dependencies = db.execute(
            "SELECT count(*) FROM gauge_captures child "
            "WHERE child.call_id NOT IN (SELECT call_id FROM gauge_run_captures "
            f"WHERE run_id IN ({marks})) AND "
            "json_extract(child.payload, '$.request.previous_response_id') IN "
            "(SELECT json_extract(parent.payload, '$.response.id') FROM gauge_captures parent "
            "JOIN gauge_run_captures r ON parent.call_id = r.call_id "
            f"WHERE r.run_id IN ({marks}))",
            ids + ids,
        ).fetchone()[0]
        if dependencies:
            raise MaintenanceConflict("Other saved captures depend on this run's context")
        agreement = db.execute(
            f"SELECT payload FROM gauge_agreement WHERE run_id IN ({marks}) "
            "ORDER BY run_id, conversation, turn",
            ids,
        ).fetchall()
        digest = hashlib.sha256(
            json.dumps([selected, captures, comparisons, agreement]).encode()
        ).hexdigest()
        return {
            "run_ids": ids,
            "runs": [{"id": i, "name": json.loads(p).get("name", i)} for i, p in selected],
            "captures": len(captures),
            "comparisons": len(comparisons),
            "judgments": sum(bool(json.loads(p).get("judgment")) for _, _, p in comparisons),
            "fingerprint": digest,
        }

    def preview(self, run_ids: list[str] | None) -> dict:
        with sqlite3.connect(self.path, timeout=2) as db:
            db.execute("BEGIN")
            return self._plan(db, run_ids)

    def clear(self, run_ids: list[str], fingerprint: str) -> dict:
        # Backup precedes the write transaction. Any intervening change invalidates the preview.
        directory = Path(self.path).resolve().parent / "backups"
        directory.mkdir(mode=0o700, exist_ok=True)
        backup = directory / f"gauge-before-clear-{time.time_ns()}-{secrets.token_hex(3)}.sqlite"
        fd = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        os.close(fd)
        with sqlite3.connect(self.path, timeout=2) as source, sqlite3.connect(backup) as target:
            source.backup(target)
        with sqlite3.connect(self.path, timeout=2) as db:
            db.execute("BEGIN IMMEDIATE")
            plan = self._plan(db, run_ids)
            if plan["fingerprint"] != fingerprint:
                raise MaintenanceConflict("Results changed since preview; preview again")
            marks = ",".join("?" for _ in run_ids) or "NULL"
            for table in ("gauge_comparisons", "gauge_captures"):
                db.execute(
                    f"DELETE FROM {table} WHERE call_id IN "
                    f"(SELECT call_id FROM gauge_run_captures WHERE run_id IN ({marks}))",
                    run_ids,
                )
            db.execute(f"DELETE FROM gauge_agreement WHERE run_id IN ({marks})", run_ids)
            db.execute(f"DELETE FROM gauge_run_captures WHERE run_id IN ({marks})", run_ids)
            db.execute(f"DELETE FROM gauge_runs WHERE id IN ({marks})", run_ids)
        return {**plan, "backup": str(backup), "cleared": True}
