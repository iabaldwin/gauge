import json
import threading
import time
from types import SimpleNamespace

import pytest

from gauge.ingestion import CaptureInbox
from gauge.wire import CaptureEvent


@pytest.fixture
def backend_module(monkeypatch):
    monkeypatch.setenv("FIREWORKS_API_KEY", "unused-fixture")
    from gauge.backend import server

    return server


def event(number=1):
    return CaptureEvent(
        id="call_" + f"{number:032x}",
        operation="chat.completions.create",
        request_json=b'{"model":"reference","messages":[{"role":"user","content":"Soap"}]}',
        response_json=b'{"choices":[{"finish_reason":"stop","message":{"content":"household"}}]}',
        status="completed",
        latency_ms=40,
        created_at=time.time(),
    ).to_dict()


def wait_for(predicate):
    deadline = time.monotonic() + 3
    while not predicate():
        assert time.monotonic() < deadline, "State transition timed out"
        time.sleep(0.01)


def test_worker_states_and_outputs_are_persisted(backend_module, monkeypatch, tmp_path):
    entered, release = threading.Event(), threading.Event()

    class FakeClient:
        def __init__(self, **kwargs):
            kwargs["http_client"].close()
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def create(self, **kwargs):
            entered.set()
            assert release.wait(3)
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="household"), finish_reason="stop"
                    )
                ],
                usage=SimpleNamespace(
                    model_dump=lambda: {
                        "prompt_tokens": 10,
                        "completion_tokens": 20,
                        "completion_tokens_details": {"reasoning_tokens": 16},
                    }
                ),
            )

    monkeypatch.setenv("FIREWORKS_API_KEY", "unused-test-key")
    monkeypatch.setattr(backend_module, "OpenAI", FakeClient)
    backend = backend_module.Backend(str(tmp_path / "test.sqlite"), "candidate", 1, 1)
    item = event()
    backend.accept(json.dumps(item).encode(), item["call_id"])
    try:
        assert entered.wait(3)
        assert backend.dashboard.page()["records"][0]["comparisons"][0]["status"] == "running"
    finally:
        release.set()
    wait_for(lambda: backend.snapshot()["completed"] == 1)
    row = backend.dashboard.page()["records"][0]
    assert row["capture"]["call_id"] == row["comparisons"][0]["call_id"]
    assert row["comparisons"][0]["candidate_output"] == "household"
    assert row["comparisons"][0]["agreement"] is True
    assert row["comparisons"][0]["usage"]["completion_tokens_details"]["reasoning_tokens"] == 16
    # Exhausted budget is a comparison failure; original remains completed.
    second = event(2)
    backend.accept(json.dumps(second).encode(), second["call_id"])
    wait_for(lambda: backend.snapshot()["failed"] == 1)
    rows = backend.dashboard.page()["records"]
    assert rows[0]["capture"]["status"] == "completed"
    assert rows[0]["comparisons"][0]["status"] == "error"
    # New store instances preserve completed/failed outcomes across restart.
    assert backend_module.DashboardStore(backend.db).page()["records"] == rows


def test_pending_queue_drop_and_restart(backend_module, monkeypatch, tmp_path):
    monkeypatch.setattr(backend_module.Backend, "consume", lambda self: None)
    backend = backend_module.Backend(str(tmp_path / "test.sqlite"), "candidate", 1, 2)
    for i in (1, 2):
        item = event(i)
        backend.accept(json.dumps(item).encode(), item["call_id"])
    rows = backend.dashboard.page()["records"]
    assert rows[0]["comparisons"][0]["status"] == "skipped"
    assert rows[0]["comparisons"][0]["reason"] == "Comparison queue full"
    assert rows[1]["comparisons"][0]["status"] == "pending"
    backend.accept(json.dumps(item).encode(), item["call_id"])
    assert len(backend.dashboard.page()["records"]) == 2
    restarted = backend_module.DashboardStore(backend.db).page()["records"]
    assert restarted[1]["comparisons"][0]["status"] == "skipped"
    assert "restarted" in restarted[1]["comparisons"][0]["reason"]


def test_old_capture_has_no_fabricated_comparison(backend_module, tmp_path):
    path = str(tmp_path / "test.sqlite")
    inbox = CaptureInbox(path)
    inbox.accept(json.dumps(event()).encode())
    inbox.close()
    assert backend_module.DashboardStore(path).page()["records"][0]["comparisons"] == []


def test_run_pages_include_older_results_and_survive_restart(backend_module, tmp_path):
    path = str(tmp_path / "paged.sqlite")
    inbox = CaptureInbox(path)
    store = backend_module.DashboardStore(path)
    store.save_run({"id": "run_saved", "name": "200-call run", "status": "completed"})
    for number in range(180):
        item = event(number)
        inbox.accept(json.dumps(item).encode())
        store.attach(item["call_id"], "run_saved")
    inbox.close()
    first = store.page("run_saved")
    second = store.page("run_saved", first["next_cursor"])
    assert first["total"] == 180
    assert len(first["records"]) == 100
    assert len(second["records"]) == 80
    assert second["next_cursor"] is None
    ids = {r["capture"]["call_id"] for r in first["records"] + second["records"]}
    assert len(ids) == 180
    restarted = backend_module.DashboardStore(path)
    assert restarted.runs()[0]["id"] == "run_saved"
    assert restarted.page("run_missing")["total"] == 0


def test_run_exclusivity_and_completion(backend_module, monkeypatch, tmp_path):
    monkeypatch.setattr(backend_module.Backend, "consume", lambda self: None)
    backend = backend_module.Backend(str(tmp_path / "runs.sqlite"), "candidate", 1, 2)
    config = {"model": "candidate", "capacity": 2, "max_calls": 3}
    run = backend.start_run(config)
    with pytest.raises(backend_module.CaptureConflict):
        backend.start_run(config)
    backend.end_run(
        {
            "run_id": run["id"],
            "report": {
                "checks": {"passed": True},
                "baseline": {"calls": 20},
                "during_saturation": {"calls": 180},
            },
        }
    )
    assert backend.active_run is None
    assert backend.dashboard.runs()[0]["summary"]["during_saturation"]["calls"] == 180
    assert backend.dashboard.runs()[0]["status"] == "completed"
    assert backend.start_run(config)["id"] != run["id"]


def test_multiple_candidates_share_capture_without_overwrites_or_pagination_duplicates(tmp_path):
    from gauge.backend.store import DashboardStore

    path = str(tmp_path / "multi.sqlite")
    inbox = CaptureInbox(path)
    store = DashboardStore(path)
    for number in range(105):
        item = event(number)
        inbox.accept(json.dumps(item).encode())
        for candidate in ("qwen", "gpt-oss"):
            store.put({"call_id": item["call_id"], "candidate": candidate, "status": "completed"})
    inbox.close()
    first = store.page()
    second = store.page(before=first["next_cursor"])
    assert first["total"] == 105
    assert len(first["records"]) == 100 and len(second["records"]) == 5
    assert len({r["capture"]["call_id"] for r in first["records"] + second["records"]}) == 105
    assert all(len(r["comparisons"]) == 2 for r in first["records"] + second["records"])


@pytest.mark.parametrize("budget", [6, 2])
def test_three_model_fanout_workers_and_global_budget(
    backend_module, monkeypatch, tmp_path, budget
):
    attempts = []
    guard = threading.Lock()

    class FakeClient:
        def __init__(self, **kwargs):
            kwargs["http_client"].close()
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def create(self, **kwargs):
            with guard:
                attempts.append(kwargs["model"])
            if kwargs["model"] == "broken":
                raise RuntimeError("Candidate failed independently")
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="household"), finish_reason="stop"
                    )
                ],
                usage=None,
            )

    monkeypatch.setenv("FIREWORKS_API_KEY", "unused-test-key")
    monkeypatch.setattr(backend_module, "OpenAI", FakeClient)
    backend = backend_module.Backend(str(tmp_path / "fanout.sqlite"), "unused", 20, budget, 3)
    run = backend.start_run(
        {"models": ["one", "broken", "three"], "capacity": 20, "max_calls": budget}
    )
    for i in range(4):
        item = event(i)
        raw = json.dumps(item).encode()
        backend.accept(raw, item["call_id"])
        backend.accept(raw, item["call_id"])
    wait_for(lambda: backend.snapshot()["completed"] + backend.snapshot()["failed"] == 12)
    wait_for(lambda: backend.jobs.unfinished_tasks == 0)
    stats = backend.snapshot()
    assert len(attempts) == stats["calls_started"] == budget
    assert stats["active"] == stats["queued"] == 0
    assert stats["comparison_jobs"] == 12 and stats["received"] == stats["duplicates"] == 4
    records = backend.dashboard.page(run["id"])["records"]
    assert len(records) == 4
    for row in records:
        assert row["capture"]["status"] == "completed"
        assert {j["candidate"] for j in row["comparisons"]} == {"one", "broken", "three"}
        assert all(j["call_id"] == row["capture"]["call_id"] for j in row["comparisons"])
        if budget == 12:
            assert {j["candidate"]: j["status"] for j in row["comparisons"]} == {
                "one": "completed",
                "broken": "error",
                "three": "completed",
            }
