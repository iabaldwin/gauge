import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace

import pytest

from gauge.benchmark.statistics import schedule, summarize


def test_matched_schedule_is_seeded_and_spends_exactly_each_arm_budget():
    batches = schedule(103, 20, 42)
    assert batches == schedule(103, 20, 42)
    assert {tuple(b["order"]) for b in batches} == {("native", "gauge"), ("gauge", "native")}
    for arm in ("native", "gauge"):
        assert [i for b in batches for a in b["order"] if a == arm for i in b["indices"]] == list(
            range(103)
        )


def test_statistics_keep_errors_and_incomplete_out_of_success_percentiles():
    rows = [
        {
            "arm": "native",
            "pair": 0,
            "status": "completed",
            "finish_reason": "stop",
            "latency_ms": 100,
        },
        {
            "arm": "gauge",
            "pair": 0,
            "status": "completed",
            "finish_reason": "stop",
            "latency_ms": 110,
        },
        {"arm": "native", "pair": 1, "status": "error", "latency_ms": 30000},
        {
            "arm": "gauge",
            "pair": 1,
            "status": "completed",
            "finish_reason": "length",
            "latency_ms": 900,
        },
    ]
    value = summarize(
        rows, [{"arm": "native", "wall_seconds": 2}, {"arm": "gauge", "wall_seconds": 4}], 2
    )
    assert value["arms"]["native"]["latencies_ms"] == [100]
    assert value["arms"]["native"]["attempt_latencies_ms"] == [100, 30000]
    assert value["arms"]["native"]["errors"] == value["arms"]["gauge"]["incomplete"] == 1
    assert value["arms"]["gauge"]["throughput"] == 0.25
    assert value["delta"]["p95_ms"]["absolute"] == 10
    assert value["delta"]["p95_ms"]["percent"] == pytest.approx(10)
    assert value["paired_mean_delta_ms"] == 10 and value["matched_pairs"] == 1
    empty = summarize([], [], 1000)
    assert empty["delta"]["p50_ms"]["absolute"] is None
    assert empty["arms"]["native"]["throughput"] is None


def test_worker_bounds_concurrency_and_does_not_retry_errors():
    path = Path(__file__).resolve().parents[2] / "tools/gauge_ab_worker.py"
    spec = importlib.util.spec_from_file_location("ab_worker", path)
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    calls, active, peak = [], 0, 0

    async def create(**kwargs):
        nonlocal active, peak
        calls.append(kwargs)
        active += 1
        peak = max(peak, active)
        await asyncio.sleep(0.001)
        active -= 1
        if kwargs["messages"][-1]["content"] == "Apples":
            raise TimeoutError("provider body must not be retained")
        return SimpleNamespace(
            id="response_fixture",
            usage=None,
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="household"), finish_reason="stop")
            ],
        )

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))
    result = asyncio.run(worker.measure(client, "test-model", list(range(9)), 2))
    assert len(calls) == len(result["samples"]) == 9
    assert peak == 2
    assert all(c["max_completion_tokens"] == 32 for c in calls)
    assert [r["pair"] for r in result["samples"]] == list(range(9))
    assert sum(r["status"] == "error" for r in result["samples"]) == 2
    assert "provider body" not in str(result)


def test_controller_persists_equal_arms_and_flushes_before_backend_drain(monkeypatch, tmp_path):
    import importlib
    import json
    import sys

    import httpx

    monkeypatch.syspath_prepend(str(Path(__file__).resolve().parents[2] / "tools"))
    runner = importlib.import_module("live_gauge_ab")
    monkeypatch.setenv("OPENAI_API_KEY", "fixture-only")
    monkeypatch.setattr(
        runner,
        "ensure_service",
        lambda: {"url": "http://127.0.0.1:8765", "project_id": "project_fixture"},
    )
    calls, posts, stopped = {}, [], set()

    class FakeArm:
        def __init__(self, name, model, env):
            self.name = name
            calls[name] = []
            assert "FIREWORKS_API_KEY" not in env and "PORTKEY_API_KEY" not in env
            assert ("GAUGE_ENDPOINT" in env) == (name == "gauge")

        def send(self, command, timeout):
            if command.get("stop"):
                stopped.add(self.name)
                return {"flushed": True} if self.name == "gauge" else {"gauge_imported": False}
            calls[self.name].extend(command["indices"])
            return {
                "wall_seconds": 0.1,
                "samples": [
                    {"pair": i, "status": "completed", "finish_reason": "stop", "latency_ms": 100}
                    for i in command["indices"]
                ],
            }

        def close(self):
            pass

    def handler(request):
        path = request.url.path
        if request.method == "POST":
            body = json.loads(request.content)
            posts.append((path, body))
            return httpx.Response(200, json={"run": {"id": "run_fixture"}})
        if path == "/stats":
            if calls:
                assert stopped == {"gauge", "native"}
            return httpx.Response(
                200,
                json={
                    "comparisons_enabled": True,
                    "active_run_id": None,
                    "queued": 0,
                    "active": 0,
                    "judging": {"queued": 0, "active": 0},
                },
            )
        assert path == "/v1/dashboard" and request.url.params["run_id"] == "run_fixture"
        return httpx.Response(
            200,
            json={
                "records": [
                    {
                        "comparisons": [
                            {"status": "completed", "finish_reason": "stop"} for _ in range(3)
                        ]
                    }
                    for _ in range(7)
                ],
                "next_cursor": None,
            },
        )

    real_client = httpx.Client
    monkeypatch.setattr(
        runner.httpx,
        "Client",
        lambda **kw: real_client(**kw, transport=httpx.MockTransport(handler)),
    )
    monkeypatch.setattr(runner, "Arm", FakeArm)
    output = tmp_path / "report.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "ab",
            "--calls-per-arm",
            "7",
            "--concurrency",
            "2",
            "--batch-size",
            "4",
            "--output",
            str(output),
        ],
    )
    runner.main()
    report = json.loads(output.read_text())
    assert calls == {"native": list(range(7)), "gauge": list(range(7))}
    assert len(report["samples"]) == 14
    assert all(report["checks"].values())
    assert posts[-1][0] == "/v1/runs/end"
    assert posts[-1][1]["report"]["performance"]["arms"]["native"]["calls"] == 7
    assert sum(path == "/v1/runs/progress" for path, _ in posts) == 5
