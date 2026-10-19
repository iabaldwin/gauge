import json
import os
import selectors
import subprocess
import sys

import httpx

from gauge.wire import MAX_ENVELOPE_BYTES, CaptureEvent


def test_loopback_ingestion_contract_without_provider_calls(tmp_path):
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "gauge.backend",
            "--port",
            "0",
            "--db",
            str(tmp_path / "inbox.sqlite"),
        ],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={**os.environ, "GAUGE_API_KEY": "local-test-token", "FIREWORKS_API_KEY": "unused"},
    )
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(10), "Server startup timed out"
        ready = json.loads(process.stdout.readline())
        with httpx.Client(
            base_url=f"http://127.0.0.1:{ready['port']}", trust_env=False, timeout=3
        ) as client:
            assert client.get("/stats").status_code == 200
            assert client.get("/v1/dashboard").status_code == 200
            shell = client.get("/dashboard")
            assert shell.status_code == 200
            assert "local-test-token" not in shell.text
            assert "frame-ancestors 'none'" in shell.headers["Content-Security-Policy"]
            assert client.get("/pixel-lab/pixel-lab.js").status_code == 200
            assert client.get("/dashboard/gauge-adapter.js").status_code == 200
            assert client.get("/dashboard/../local_backend.py").status_code != 200
            assert client.post("/control", json={"paused": True}).status_code == 200
            assert (
                client.post("/v1/captures", content=b"x" * (MAX_ENVELOPE_BYTES + 1)).status_code
                == 413
            )
            item = CaptureEvent(
                id="call_" + "1" * 32,
                operation="chat.completions.create",
                request_json=b'{"model":"test","messages":[]}',
                response_json=None,
                status="stream_passthrough",
                latency_ms=1,
                created_at=1,
            ).to_dict()
            assert client.post("/v1/captures", json=item).status_code == 400
            headers = {"Idempotency-Key": item["call_id"]}
            assert client.post("/v1/captures", json=item, headers=headers).status_code == 202
            assert client.post("/v1/captures", json=item, headers=headers).status_code == 202
            stats = client.get("/stats").json()
            assert stats["received"] == stats["duplicates"] == 1
            assert stats["comparison_jobs"] == 0
            assert stats["calls_started"] == stats["queued"] == 0
            feed = client.get("/v1/dashboard")
            assert feed.headers["Cache-Control"] == "no-store"
            record = feed.json()["records"][0]
            assert record["capture"]["call_id"] == item["call_id"]
            assert record["comparisons"] == []
    finally:
        process.terminate()
        process.wait(timeout=5)
