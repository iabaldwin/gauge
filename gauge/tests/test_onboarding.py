"""Fresh-install journey and failure isolation. Providers are fixtures, not live calls."""

from __future__ import annotations

import json
import os
import signal
import sqlite3
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import httpx

from gauge.local.state import bootstrap


class PrivateEnvironment(dict):
    def __repr__(self):
        return "<subprocess environment>"


def environment(tmp_path):
    env = {k: v for k, v in os.environ.items() if not k.startswith("GAUGE_")}
    env.update(GAUGE_HOME=str(tmp_path / "home"), GAUGE_JUDGE="off")
    for key in ("FIREWORKS_API_KEY", "PORTKEY_API_KEY", "OPENAI_API_KEY"):
        env.pop(key, None)
    return PrivateEnvironment(env)


def project_fixture(tmp_path):
    app = tmp_path / "application"
    app.mkdir()
    (app / "pyproject.toml").write_text('[project]\nname="customer-app"\nversion="0.1"\n')
    (app / "test_app.py").write_text("""
import httpx
from gauge.openai import OpenAI

def test_existing_application():
    reply={"id":"resp1","object":"response","created_at":1,"model":"reference",
           "status":"completed","output":[{"type":"message","role":"assistant",
           "content":[{"type":"output_text","text":"household","annotations":[]}]}]}
    with OpenAI(api_key="fixture", http_client=httpx.Client(transport=httpx.MockTransport(
            lambda request: httpx.Response(200,json=reply)))) as client:
        assert client.responses.create(model="reference",input=[
            {"role":"system","content":"Classify the product"},
            {"role":"user","content":"Dish soap"}]).output_text=="household"
""")
    return app


def run(args, app, env):
    result = subprocess.run(
        [sys.executable, *args], cwd=app, env=env, capture_output=True, text=True, timeout=30
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result.stdout


def test_atomic_identity_and_project_move(tmp_path, monkeypatch):
    monkeypatch.delenv("GAUGE_PROJECT_DIR", raising=False)
    root = tmp_path / "app"
    root.mkdir()
    with ThreadPoolExecutor(max_workers=8) as pool:
        projects = list(pool.map(lambda _: bootstrap(root), range(16)))
    assert len({p["project_id"] for p in projects}) == 1
    state = json.loads((root / ".gauge/project.json").read_text())
    assert "token" not in state and "user_id" not in state
    moved = tmp_path / "renamed"
    root.rename(moved)
    assert bootstrap(moved)["project_id"] == projects[0]["project_id"]
    assert bootstrap(moved)["database"].startswith(str(moved))


def test_tests_then_cli_then_dashboard_without_credentials(tmp_path):
    app, env = project_fixture(tmp_path), environment(tmp_path)
    run(["-m", "pytest", "-q"], app, env)
    state = json.loads((app / ".gauge/project.json").read_text())
    database = app / ".gauge/captures.sqlite"
    assert not (app / ".gauge/service.json").exists()  # No service started by application capture.
    with sqlite3.connect(database) as db:
        first = json.loads(db.execute("SELECT payload FROM gauge_captures").fetchone()[0])
        saved_run = json.loads(db.execute("SELECT payload FROM gauge_runs").fetchone()[0])
    assert first["project_id"] == state["project_id"]
    assert first["run_id"] == saved_run["id"]
    assert first["process_id"].startswith("process_")
    assert first["installation_id"].startswith("installation_")
    assert saved_run["status"] == "completed"
    try:
        listed = json.loads(run(["-m", "gauge", "requests", "--json"], app, env))
        assert listed["records"][0]["capture"]["call_id"] == first["call_id"]
        assert listed["records"][0]["comparisons"] == []
        inspected = json.loads(
            run(["-m", "gauge", "inspect", first["call_id"], "--json"], app, env)
        )
        assert inspected["capture"]["request"]["input"][0]["role"] == "system"
        url = run(["-m", "gauge", "dashboard", "--print-only"], app, env).strip()
        assert "run=" + saved_run["id"] in url and "ticket" not in url
        with httpx.Client(trust_env=False) as client:
            response = client.get(url)
            assert response.status_code == 200
            assert 'id="key"' not in response.text
            base = url.split("/dashboard")[0]
            assert client.get(base + "/v1/runs").status_code == 200
            assert client.get(base + "/stats").json()["comparisons_enabled"] is False
        run(["-m", "pytest", "-q"], app, env)
        assert (
            json.loads((app / ".gauge/project.json").read_text())["project_id"]
            == state["project_id"]
        )
        with sqlite3.connect(database) as db:
            assert db.execute("SELECT count(*) FROM gauge_runs").fetchone()[0] == 2
    finally:
        service = app / ".gauge/service.json"
        if service.exists():
            os.kill(json.loads(service.read_text())["pid"], signal.SIGTERM)


def test_pytest_workers_share_run_but_not_process_ids(tmp_path):
    app, env = project_fixture(tmp_path), environment(tmp_path)
    # Ensure each worker executes a case, rather than scheduling one item on one worker.
    source = (app / "test_app.py").read_text()
    (app / "test_second.py").write_text(source.replace("test_existing_application", "test_second"))
    run(["-m", "pytest", "-q", "-n", "2", "--dist=each"], app, env)
    with sqlite3.connect(app / ".gauge/captures.sqlite") as db:
        events = [json.loads(row[0]) for row in db.execute("SELECT payload FROM gauge_captures")]
    assert len(events) == 4
    assert len({e["run_id"] for e in events}) == 1
    assert len({e["process_id"] for e in events}) == 2
    assert len({e["project_id"] for e in events}) == 1
