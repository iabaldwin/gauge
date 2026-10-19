"""Internal project segmentation and durable budgets, without provider inference."""

from __future__ import annotations

import json
import os
import selectors
import subprocess
import sys
from uuid import uuid4

import httpx
from test_dashboard_backend import event

from gauge.backend.server import Backend


def test_comparison_budget_and_originals_survive_restart(tmp_path, monkeypatch):
    monkeypatch.setenv("FIREWORKS_API_KEY", "unused")
    monkeypatch.setattr(Backend, "consume", lambda self: None)
    monkeypatch.setattr(Backend, "watch", lambda self: None)
    project = "project_" + uuid4().hex
    path = str(tmp_path / "captures.sqlite")
    backend = Backend(path, "candidate", 20, 3, project_id=project)
    for n in range(3):
        capture = {**event(n), "project_id": project, "run_id": "run_" + "a" * 32}
        backend.accept(json.dumps(capture).encode(), capture["call_id"], compare=False)
    backend.configure({"models": ["model-a", "model-b"], "budget": 3})
    first = backend.compare({"limit": 100})
    assert first["accepted"] == 3
    assert backend.compare({"limit": 100})["accepted"] == 0
    rows = backend.dashboard.page()["records"]
    assert len(rows) == 3
    assert sum(len(r["comparisons"]) for r in rows) == 3
    assert all(j["configuration_id"] and j["attempt_id"] for r in rows for j in r["comparisons"])
    restarted = Backend(path, "candidate", 20, 3, project_id=project)
    assert restarted.compare({"limit": 100})["accepted"] == 0
    assert all(
        j["status"] == "skipped"
        for r in restarted.dashboard.page()["records"]
        for j in r["comparisons"]
    )


def test_internal_shared_server_separates_projects_without_auth(tmp_path, monkeypatch):
    env = {
        k: v
        for k, v in os.environ.items()
        if not k.startswith("GAUGE_") and k not in {"FIREWORKS_API_KEY", "PORTKEY_API_KEY"}
    }
    env["GAUGE_JUDGE"] = "off"
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gauge.backend",
            "--port",
            "0",
            "--db",
            str(tmp_path / "empty.sqlite"),
            "--projects-dir",
            str(tmp_path / "projects"),
        ],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(10), "Shared service startup timed out"
        ready = json.loads(process.stdout.readline())
        with httpx.Client(base_url=f"http://127.0.0.1:{ready['port']}", trust_env=False) as client:
            projects = ["project_" + uuid4().hex for _ in range(2)]
            for n, project in enumerate(projects):
                registered = client.post("/v1/projects", json={"id": project, "name": f"Team {n}"})
                assert registered.status_code == 201
                capture = {**event(n), "project_id": project, "run_id": "run_" + f"{n:032x}"}
                posted = client.post(
                    "/v1/import",
                    params={"project_id": project},
                    json=capture,
                    headers={"Idempotency-Key": capture["call_id"]},
                )
                assert posted.status_code == 202
            assert len(client.get("/v1/projects").json()["projects"]) == 2
            for n, project in enumerate(projects):
                page = client.get("/v1/dashboard", params={"project_id": project}).json()
                assert page["total"] == 1
                assert page["records"][0]["capture"]["call_id"] == event(n)["call_id"]
                assert page["stats"]["project_id"] == project
                runs = client.get("/v1/runs", params={"project_id": project}).json()["runs"]
                preview = client.post(
                    "/v1/runs/clear/preview",
                    params={"project_id": project},
                    json={"run_ids": [runs[0]["id"]]},
                ).json()
                assert preview["captures"] == 1
            wrong = {**event(5), "project_id": projects[1]}
            assert (
                client.post(
                    "/v1/import",
                    params={"project_id": projects[0]},
                    json=wrong,
                    headers={"Idempotency-Key": wrong["call_id"]},
                ).status_code
                == 400
            )
            target = projects[0]
            runs = client.get("/v1/runs", params={"project_id": target}).json()["runs"]
            preview = client.post(
                "/v1/runs/clear/preview",
                params={"project_id": target},
                json={"run_ids": [runs[0]["id"]]},
            ).json()
            cleared = client.post(
                "/v1/runs/clear",
                params={"project_id": target},
                json={"run_ids": preview["run_ids"], "fingerprint": preview["fingerprint"]},
            )
            assert cleared.status_code == 200
            assert client.get("/v1/dashboard", params={"project_id": target}).json()["total"] == 0
            assert (
                client.get("/v1/dashboard", params={"project_id": projects[1]}).json()["total"] == 1
            )
            # Connect a local project twice; preserve its run IDs and metadata without signup.
            from gauge.backend.store import DashboardStore
            from gauge.ingestion import CaptureInbox
            from gauge.local.remote import connect as connect_project
            from gauge.local.state import bootstrap

            local = tmp_path / "developer-project"
            local.mkdir()
            monkeypatch.setenv("GAUGE_PROJECT_DIR", str(local))
            state = bootstrap(local)
            inbox = CaptureInbox(state["database"])
            DashboardStore(state["database"], recover=False)
            run_id = "run_" + uuid4().hex
            capture = {**event(9), "project_id": state["project_id"], "run_id": run_id}
            run = {
                "id": run_id,
                "project_id": state["project_id"],
                "name": "Local tests",
                "status": "completed",
                "created_at": capture["created_at"],
            }
            inbox.accept(json.dumps(capture).encode(), run=run)
            inbox.close()
            url = f"http://127.0.0.1:{ready['port']}"
            assert connect_project(url)["captures_uploaded"] == 1
            assert connect_project(url)["captures_uploaded"] == 1
            selected = {"project_id": state["project_id"]}
            saved = client.get("/v1/dashboard", params=selected).json()
            assert saved["total"] == 1 and saved["records"][0]["comparisons"] == []
            remote_run = client.get("/v1/runs", params=selected).json()["runs"][0]
            assert remote_run["id"] == run_id and remote_run["name"] == "Local tests"
            assert "token" not in (local / ".gauge/remote.json").read_text()
    finally:
        process.terminate()
        process.wait(timeout=5)


def test_reporting_write_retries_preserve_separately_written_judgment(tmp_path, monkeypatch):
    import sqlite3

    from gauge.backend import store as module

    store = module.DashboardStore(str(tmp_path / "reporting.sqlite"))
    original = {"call_id": "call_one", "candidate": "model", "status": "running"}
    store.put(original)
    store.update_judgment("call_one", "model", {"status": "completed", "verdict": "equivalent"})
    connect, attempts = module.sqlite3.connect, []

    def contended(*args, **kwargs):
        attempts.append(1)
        if len(attempts) < 3:
            raise sqlite3.OperationalError("database is locked")
        return connect(*args, **kwargs)

    monkeypatch.setattr(module.sqlite3, "connect", contended)
    store.put({**original, "status": "completed", "candidate_output": "answer"})
    assert len(attempts) == 3
    with connect(store.path) as db:
        saved = json.loads(db.execute("SELECT payload FROM gauge_comparisons").fetchone()[0])
    assert saved["status"] == "completed"
    assert saved["judgment"]["verdict"] == "equivalent"
