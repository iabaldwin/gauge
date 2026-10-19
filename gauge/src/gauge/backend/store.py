"""Dashboard read model for the local backend; never used by the caller SDK."""

from __future__ import annotations

import json
import sqlite3
import time
from typing import Any


class DashboardStore:
    def __init__(self, path: str, *, recover: bool = True) -> None:
        self.path = path
        with sqlite3.connect(path, timeout=5) as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "CREATE TABLE IF NOT EXISTS gauge_runs (id TEXT PRIMARY KEY, payload TEXT NOT NULL)"
            )
            db.execute("""CREATE TABLE IF NOT EXISTS gauge_run_captures (
                call_id TEXT PRIMARY KEY, run_id TEXT NOT NULL)""")
            db.execute("""
                CREATE TABLE IF NOT EXISTS gauge_comparisons (
                    call_id TEXT NOT NULL,
                    candidate TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    PRIMARY KEY (call_id, candidate)
                )
            """)
            db.execute(
                "CREATE INDEX IF NOT EXISTS comparison_configuration "
                "ON gauge_comparisons(json_extract(payload, '$.configuration_id'))"
            )
            db.execute("PRAGMA busy_timeout=5000")
            if not recover:
                return
            # This local queue is not durable. Never leave old jobs looking active
            # or automatically spend provider quota by replaying them on restart.
            for call_id, candidate, payload in db.execute(
                "SELECT call_id, candidate, payload FROM gauge_comparisons"
            ).fetchall():
                row = json.loads(payload)
                if row["status"] in {"pending", "running"}:
                    row.update(
                        status="skipped",
                        reason="Backend restarted; comparison interrupted, outcome unavailable",
                        finished_at=time.time(),
                    )
                    db.execute(
                        "UPDATE gauge_comparisons SET payload = ? "
                        "WHERE call_id = ? AND candidate = ?",
                        (json.dumps(row), call_id, candidate),
                    )
                if row.get("judgment", {}).get("status") in {"pending", "running"}:
                    row["judgment"].update(
                        status="skipped",
                        reason="Backend restarted; judgment interrupted",
                        finished_at=time.time(),
                    )
                    db.execute(
                        "UPDATE gauge_comparisons SET payload = ? "
                        "WHERE call_id = ? AND candidate = ?",
                        (json.dumps(row), call_id, candidate),
                    )
            for run_id, payload in db.execute("SELECT id, payload FROM gauge_runs"):
                run = json.loads(payload)
                if run["status"] == "running" and run.get("kind") != "automatic":
                    run.update(status="interrupted", finished_at=time.time())
                    db.execute(
                        "UPDATE gauge_runs SET payload = ? WHERE id = ?", (json.dumps(run), run_id)
                    )

    @staticmethod
    def report_summary(report: dict[str, Any]) -> dict[str, Any]:
        return {
            "baseline": {k: v for k, v in report.get("baseline", {}).items() if k != "results"},
            "during_saturation": {
                k: v for k, v in report.get("during_saturation", {}).items() if k != "results"
            },
            "checks": report.get("checks"),
            "error": report.get("test_error"),
            "source_model": report.get("source_model"),
            "candidate_models": report.get("candidate_models"),
            "mode": report.get("mode"),
            "limits": report.get("limits"),
            "validation": report.get("validation"),
        }

    def put(self, row: dict[str, Any]) -> None:
        for attempt in range(3):
            try:
                with sqlite3.connect(self.path, timeout=2) as db:
                    db.execute("BEGIN IMMEDIATE")
                    previous = db.execute(
                        "SELECT payload FROM gauge_comparisons WHERE call_id=? AND candidate=?",
                        (row["call_id"], row["candidate"]),
                    ).fetchone()
                    saved = json.loads(previous[0]) if previous else {}
                    merged = dict(row)
                    for field in ("judgment", "judgment_history"):
                        if field not in merged and field in saved:
                            merged[field] = saved[field]
                    db.execute(
                        "INSERT OR REPLACE INTO gauge_comparisons VALUES (?, ?, ?)",
                        (row["call_id"], row["candidate"], json.dumps(merged, allow_nan=False)),
                    )
                return
            except sqlite3.OperationalError as exc:
                if attempt == 2 or not any(word in str(exc).lower() for word in ("locked", "busy")):
                    raise
                time.sleep(0.05 * (attempt + 1))

    def save_run(self, run: dict[str, Any]) -> None:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute(
                "INSERT OR REPLACE INTO gauge_runs VALUES (?, ?)", (run["id"], json.dumps(run))
            )

    def update_judgment(
        self, call_id: str, candidate: str, judgment: dict, *, only_unjudged: bool = False
    ) -> bool:
        """Patch only the judgment; never overwrite captured inference or comparison results."""
        with sqlite3.connect(self.path, timeout=5) as db:
            if only_unjudged:
                db.execute("BEGIN IMMEDIATE")
                record = db.execute(
                    "SELECT payload FROM gauge_comparisons WHERE call_id = ? AND candidate = ?",
                    (call_id, candidate),
                ).fetchone()
                if record is None:
                    return False
                saved = json.loads(record[0])
                prior = saved.get("judgment", {})
                if prior.get("status") in {"pending", "running"} or (
                    prior.get("status") in {"completed", "uncertain"}
                    and prior.get("rubric_version") == judgment.get("rubric_version")
                ):
                    return False
                if prior.get("status") in {"completed", "uncertain"}:
                    history = [*saved.get("judgment_history", []), prior][-10:]
                    db.execute(
                        "UPDATE gauge_comparisons SET payload = "
                        "json_set(payload, '$.judgment_history', json(?)) "
                        "WHERE call_id = ? AND candidate = ?",
                        (json.dumps(history, allow_nan=False), call_id, candidate),
                    )
            cursor = db.execute(
                "UPDATE gauge_comparisons SET payload = "
                "json_set(payload, '$.judgment', json(?)) WHERE call_id = ? AND candidate = ?",
                (json.dumps(judgment, allow_nan=False), call_id, candidate),
            )
            return cursor.rowcount == 1

    def judgment_work(
        self, *, run_id: str | None, call_id: str | None, limit: int, rubric_version: str
    ) -> list:
        with sqlite3.connect(self.path, timeout=5) as db:
            rows = db.execute(
                "SELECT c.payload, j.payload FROM gauge_comparisons j "
                "JOIN gauge_captures c ON c.call_id = j.call_id "
                "LEFT JOIN gauge_run_captures r ON r.call_id = j.call_id "
                "WHERE (? IS NULL OR r.run_id = ?) AND (? IS NULL OR j.call_id = ?) "
                "AND json_extract(j.payload, '$.status') = 'completed' "
                "AND json_extract(j.payload, '$.finish_reason') = 'stop' "
                "AND COALESCE(json_extract(j.payload, '$.judgment.status'), '') "
                "NOT IN ('pending', 'running') "
                "AND (COALESCE(json_extract(j.payload, '$.judgment.status'), '') "
                "NOT IN ('completed', 'uncertain') "
                "OR COALESCE(json_extract(j.payload, '$.judgment.rubric_version'), '') != ?) "
                "ORDER BY c.rowid DESC, j.candidate LIMIT ?",
                (run_id, run_id, call_id, call_id, rubric_version, limit),
            ).fetchall()
        return [(json.loads(event), json.loads(job)) for event, job in rows]

    def runs(self) -> list[dict[str, Any]]:
        with sqlite3.connect(self.path, timeout=5) as db:
            return [
                json.loads(row[0])
                for row in db.execute("SELECT payload FROM gauge_runs ORDER BY rowid DESC")
            ]

    def attach(self, call_id: str, run_id: str) -> None:
        with sqlite3.connect(self.path, timeout=5) as db:
            db.execute("INSERT OR IGNORE INTO gauge_run_captures VALUES (?, ?)", (call_id, run_id))

    def response_capture(self, response_id: str, endpoint_host: str | None) -> dict | None:
        with sqlite3.connect(self.path, timeout=5) as db:
            rows = db.execute(
                "SELECT payload FROM gauge_captures "
                "WHERE json_extract(payload, '$.response.id') = ? "
                "AND json_extract(payload, '$.endpoint_host') IS ? LIMIT 2",
                (response_id, endpoint_host),
            ).fetchall()
        return json.loads(rows[0][0]) if len(rows) == 1 else None

    def trace_graph(self, run_id: str | None = None) -> dict:
        from gauge.backend.correlation import correlate

        events: list[dict] = []
        cursor = None
        while True:
            page = self.page(run_id, cursor)
            events.extend(row["capture"] for row in page["records"])
            cursor = page["next_cursor"]
            if cursor is None:
                break
        return correlate(events)

    def page(self, run_id: str | None = None, before: int | None = None) -> dict[str, Any]:
        where: list[str] = []
        values: list[Any] = []
        if run_id:
            where.append("r.run_id = ?")
            values.append(run_id)
        clause = " WHERE " + " AND ".join(where) if where else ""
        join = " FROM gauge_captures c LEFT JOIN gauge_run_captures r ON c.call_id = r.call_id"
        with sqlite3.connect(self.path, timeout=5) as db:
            total = db.execute("SELECT count(*)" + join + clause, values).fetchone()[0]
            if before is not None:
                where.append("c.rowid < ?")
                values.append(before)
            clause = " WHERE " + " AND ".join(where) if where else ""
            rows = db.execute(
                "SELECT c.rowid, c.call_id, c.payload"
                + join
                + clause
                + " ORDER BY c.rowid DESC LIMIT 101",
                values,
            ).fetchall()
            page = rows[:100]
            jobs: dict[str, list[dict[str, Any]]] = {}
            if page:
                placeholders = ",".join("?" for _ in page)
                for call_id, payload in db.execute(
                    "SELECT call_id, payload FROM gauge_comparisons "
                    f"WHERE call_id IN ({placeholders}) ORDER BY candidate",
                    [row[1] for row in page],
                ):
                    jobs.setdefault(call_id, []).append(json.loads(payload))
        return {
            "records": [
                {"capture": json.loads(payload), "comparisons": jobs.get(call_id, [])}
                for _, call_id, payload in page
            ],
            "total": total,
            "next_cursor": page[-1][0] if len(rows) > 100 else None,
        }
