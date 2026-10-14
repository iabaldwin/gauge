import json
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest


@pytest.mark.parametrize("mode", ["saturation", "load"])
@pytest.mark.parametrize("phase_fails", [False, True])
def test_runner_saves_run_without_managing_server(monkeypatch, tmp_path, phase_fails, mode):
    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tools"))
    import live_gauge_saturation as runner

    posts = []

    class Response:
        def __init__(self, payload):
            self.payload = payload

        def raise_for_status(self):
            pass

        def json(self):
            return self.payload

    class Control:
        def __init__(self, **kwargs):
            assert kwargs["base_url"] == "http://127.0.0.1:8765"

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def post(self, path, json):
            posts.append((path, json))
            return Response({"run": {"id": "run_test"}})

        def get(self, path):
            return Response(
                {
                    "active": 0,
                    "queued": 0,
                    "received": 8 if mode == "load" else 4,
                    "queue_dropped": 0 if mode == "load" else 2,
                    "ineligible": 0,
                    "completed": 24 if mode == "load" else 2,
                    "failed": 0,
                    "results": [
                        {"agreement": True, "candidate": model} for model in ("one", "two", "three")
                    ],
                }
            )

    async def phase(cls, model, count, concurrency, start_index=0):
        if phase_fails:
            raise RuntimeError("Simulated test interruption")
        return [
            {"status": "completed", "answer": "household", "finish_reason": "stop", "latency_ms": 1}
            for _ in range(count)
        ]

    monkeypatch.setattr(
        runner, "settings", lambda: {"url": "http://127.0.0.1:8765", "token": "test"}
    )
    monkeypatch.setattr(runner, "source_settings", lambda: None)
    monkeypatch.setattr(runner, "phase", phase)
    monkeypatch.setattr(runner.httpx, "Client", Control)
    monkeypatch.setattr(
        runner,
        "captures",
        SimpleNamespace(
            flush=lambda timeout: True,
            close=lambda: None,
            exported=4,
            failed=0,
            dropped=0,
            export_dropped=0,
            _pending_bytes=0,
            _event_bytes=100,
        ),
    )
    output = tmp_path / "report.json"
    argv = ["stress-test", "--output", str(output)]
    if mode == "load":
        argv += [
            "--mode",
            "load",
            "--baseline-calls",
            "0",
            "--comparison-capacity",
            "24",
            "--comparison-max-calls",
            "24",
        ]
        for model in ("one", "two", "three"):
            argv += ["--candidate-model", model]
    monkeypatch.setattr(sys, "argv", argv)
    if phase_fails:
        with pytest.raises(SystemExit):
            runner.main()
    else:
        runner.main()
    assert posts[0][0] == "/v1/runs/start"
    assert posts[-2] == ("/control", {"paused": False})
    assert posts[-1][0] == "/v1/runs/end"
    assert posts[-1][1]["run_id"] == "run_test"
    report = json.loads(output.read_text())
    assert report["dashboard_url"] == "http://127.0.0.1:8765/dashboard?run=run_test"
    assert bool(report.get("test_error")) is phase_fails
