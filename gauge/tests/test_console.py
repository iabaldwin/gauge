import asyncio
import json
import sqlite3
import threading
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from test_dashboard_backend import event

from gauge.backend.maintenance import MaintenanceConflict, RunMaintenance
from gauge.backend.server import Backend
from gauge.backend.store import DashboardStore
from gauge.ingestion import CaptureInbox


def saved(tmp_path):
    path = str(tmp_path / "saved.sqlite")
    inbox = CaptureInbox(path)
    store = DashboardStore(path)
    for n in (1, 2):
        run_id = f"run_{n}"
        store.save_run({"id": run_id, "name": f"Example {n}", "status": "completed"})
        capture = event(n)
        inbox.accept(json.dumps(capture).encode())
        store.attach(capture["call_id"], run_id)
        store.put(
            {
                "call_id": capture["call_id"],
                "candidate": "qwen",
                "status": "completed",
                "finish_reason": "stop",
                "candidate_output": "Household goods",
                "latency_ms": 42,
                "agreement": False,
                "judgment": {"status": "completed", "verdict": "equivalent", "confidence": 0.98},
            }
        )
    inbox.close()
    return store


def test_clear_preview_backup_and_scoped_deletion(tmp_path):
    store = saved(tmp_path)
    maintenance = RunMaintenance(store.path)
    preview = maintenance.preview(["run_1"])
    assert preview["captures"] == preview["comparisons"] == preview["judgments"] == 1
    assert len(store.runs()) == 2  # preview changes nothing
    result = maintenance.clear(preview["run_ids"], preview["fingerprint"])
    assert result["cleared"] is True
    assert [r["id"] for r in store.runs()] == ["run_2"]
    assert store.page()["total"] == 1
    with sqlite3.connect(result["backup"]) as db:
        assert db.execute("select count(*) from gauge_runs").fetchone()[0] == 2
        assert db.execute("select count(*) from gauge_comparisons").fetchone()[0] == 2
    assert Path(result["backup"]).stat().st_mode & 0o777 == 0o600


def test_changed_preview_refuses_deletion(tmp_path):
    store = saved(tmp_path)
    maintenance = RunMaintenance(store.path)
    preview = maintenance.preview(["run_1"])
    job = store.page("run_1")["records"][0]["comparisons"][0]
    store.put({**job, "candidate_output": "Changed since preview"})
    with pytest.raises(MaintenanceConflict, match="changed since preview"):
        maintenance.clear(preview["run_ids"], preview["fingerprint"])
    assert len(store.runs()) == 2


def test_active_runs_and_cross_run_context_cannot_be_cleared(tmp_path):
    store = saved(tmp_path)
    store.save_run({"id": "run_1", "status": "running", "name": "Active"})
    with pytest.raises(MaintenanceConflict, match="active run"):
        RunMaintenance(store.path).preview(["run_1"])
    store.save_run({"id": "run_1", "status": "completed", "name": "Done"})
    with sqlite3.connect(store.path) as db:
        db.execute(
            "update gauge_captures set payload=json_set(payload,'$.response.id','parent') "
            "where call_id=?",
            (event(1)["call_id"],),
        )
        db.execute(
            "update gauge_captures set payload=json_set(payload,"
            "'$.request.previous_response_id','parent') where call_id=?",
            (event(2)["call_id"],),
        )
    with pytest.raises(MaintenanceConflict, match="depend"):
        RunMaintenance(store.path).preview(["run_1"])
    # Clearing both dependent runs is valid; no surviving context is broken.
    assert len(RunMaintenance(store.path).preview(None)["run_ids"]) == 2


@pytest.mark.parametrize("busy", ["run", "comparison", "judge"])
def test_backend_requires_idle_before_clear(tmp_path, busy):
    store = saved(tmp_path)
    backend = Backend.__new__(Backend)
    backend.db, backend.lock = store.path, threading.Lock()
    backend.active_run = {"id": "other"} if busy == "run" else None
    backend.jobs = SimpleNamespace(unfinished_tasks=int(busy == "comparison"))
    backend.judging = SimpleNamespace(
        lock=threading.Lock(), inflight={1} if busy == "judge" else set()
    )
    preview = RunMaintenance(store.path).preview(["run_1"])
    with pytest.raises(MaintenanceConflict, match="idle"):
        backend.maintain_runs(
            {"run_ids": ["run_1"], "fingerprint": preview["fingerprint"]}, clear=True
        )
    assert len(store.runs()) == 2


def test_console_export_pagination_and_project_scope(tmp_path):
    from gauge.cli.client import ConsoleClient, export_run

    connection = tmp_path / "connection.json"
    connection.write_text(
        json.dumps({"url": "http://127.0.0.1:8765", "project_id": "project_test"})
    )
    client = ConsoleClient(connection)
    client.http.close()

    def handler(request):
        assert request.url.params["project_id"] == "project_test"
        assert "authorization" not in request.headers
        if request.url.path == "/v1/runs":
            return httpx.Response(200, json={"runs": [{"id": "run_1"}]})
        older = "before" in request.url.params
        return httpx.Response(
            200,
            json={
                "records": [{"capture": {"call_id": str(older)}}],
                "next_cursor": None if older else 123,
            },
        )

    client.http = httpx.Client(
        base_url=client.url,
        transport=httpx.MockTransport(handler),
    )
    output = export_run(client, "run_1", tmp_path / "export.json")
    assert len(json.loads(output.read_text())["records"]) == 2
    with pytest.raises(FileExistsError):
        export_run(client, "run_1", output)
    client.close()


def test_semantic_counts_and_terminal_controls():
    pytest.importorskip("rich")
    from gauge.cli.views import models, plain

    assert "\x1b" not in plain("name\x1b[31m\x00")
    jobs = [
        {
            "candidate": "qwen",
            "status": "completed",
            "finish_reason": "stop",
            "judgment": {"status": status, "verdict": value},
        }
        for status, value in [
            ("completed", "equivalent"),
            ("uncertain", "uncertain"),
            ("error", None),
        ]
    ]
    row = models([{"comparisons": jobs}])[0]
    assert row["equivalent"] == row["judged"] == row["uncertain"] == 1


def test_terminal_keyboard_inspection_and_clear_cancel(tmp_path):
    pytest.importorskip("textual")
    from textual.widgets import Button, DataTable, Input

    from gauge.cli.dialogs import ClearDialog, InspectDialog
    from gauge.cli.tui import GaugeConsole

    store = saved(tmp_path)

    class FakeClient:
        def snapshot(self):
            return {"runs": store.runs(), "stats": {"comparison_jobs": 2, "completed": 2}}

        def records(self, run_id):
            return store.page(run_id)["records"]

        def preview(self, ids):
            return RunMaintenance(store.path).preview(ids)

        def clear(self, preview):
            return RunMaintenance(store.path).clear(preview["run_ids"], preview["fingerprint"])

    async def exercise():
        app = GaugeConsole(FakeClient())
        async with app.run_test(size=(140, 45)) as pilot:
            for _ in range(30):
                await pilot.pause(0.05)
                if app.records:
                    break
            assert app.query_one("#runs", DataTable).row_count == 2
            assert app.query_one("#pairs", DataTable).row_count == 1
            app.query_one("#pairs", DataTable).focus()
            await pilot.press("enter")
            assert isinstance(app.screen, InspectDialog)
            await pilot.pause(3)  # polling must remain safe while an inspector is open
            assert isinstance(app.screen, InspectDialog)
            await pilot.press("escape")
            await pilot.press("d")
            for _ in range(40):
                await pilot.pause(0.05)
                if isinstance(app.screen, ClearDialog):
                    break
            assert isinstance(app.screen, ClearDialog)
            assert app.screen.query_one("#confirm-clear", Button).disabled
            app.screen.query_one("#clear-confirm", Input).value = "CLEAR"
            await pilot.pause()
            assert not app.screen.query_one("#confirm-clear", Button).disabled
            await pilot.press("escape")
            assert len(store.runs()) == 2
            await pilot.press("d")
            await pilot.pause(0.2)
            app.screen.query_one("#clear-confirm", Input).value = "CLEAR"
            await pilot.pause()
            await pilot.click("#confirm-clear")
            for _ in range(30):
                await pilot.pause(0.05)
                if len(store.runs()) == 1:
                    break
            assert len(store.runs()) == 1
            assert list((tmp_path / "backups").glob("*.sqlite"))
            await pilot.resize_terminal(80, 24)
            await pilot.pause()
            assert app.query_one("#pairs", DataTable).region.width > 0
            assert app.query_one("#pairs", DataTable).region.height >= 4

    asyncio.run(exercise())


def test_explicit_terminal_colors_preserve_launch_environment(monkeypatch):
    pytest.importorskip("textual")
    from gauge.cli.tui import GaugeConsole

    monkeypatch.setenv("NO_COLOR", "1")
    import os

    app = GaugeConsole(None, color=True)
    assert app.no_color is False
    assert os.environ["NO_COLOR"] == "1"
