import json
import threading

import httpx
import pytest
from test_dashboard_backend import event, wait_for

from gauge.backend.judge import JevJudge, questions, validate_answer
from gauge.backend.judging import JudgeQueue
from gauge.backend.store import DashboardStore


def answer(choice="equivalent", confidence=0.95):
    keys = questions()["semantic"]["criteria"]
    return {
        "choice": choice,
        "confidence": confidence,
        "probabilities": {key: 0.96 if key == choice else 0.02 for key in keys},
    }


def response():
    return {
        "answers": {
            "semantic": answer(),
            **{
                key: {
                    "choice": "compliant",
                    "confidence": 0.99,
                    "probabilities": {"compliant": 0.98, "violation": 0.01, "uncertain": 0.01},
                }
                for key in ("original_compliance", "candidate_compliance")
            },
        }
    }


def test_jev_contract_context_probabilities_and_separate_compliance(monkeypatch):
    monkeypatch.setenv("PORTKEY_API_KEY", "test-key")

    def handler(request):
        assert str(request.url) == "https://cybertron-service-gateway.doordash.team/v1/decisions"
        assert request.headers["x-portkey-api-key"] == "test-key"
        assert request.headers["x-portkey-custom-host"] == "https://openrouter.ai/api/alpha"
        body = json.loads(request.content)
        assert body["model"] == "@openrouter/typesafe/jev-1.13"
        assert body["state"]["conversation"][0]["content"] == "Do not book"
        assert body["state"]["reply_a"] == "Welcome"
        assert body["state"]["reply_b"] == "You are welcome!"
        assert len(body["questions"]) == 3
        return httpx.Response(200, json=response())

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = JevJudge(client).grade(
            [{"role": "system", "content": "Do not book"}], "Welcome", "You are welcome!"
        )
    assert result["status"] == "completed"
    assert result["verdict"] == "equivalent"
    assert result["probabilities"]["equivalent"] == 0.96
    assert result["answers"]["candidate_compliance"]["verdict"] == "compliant"


@pytest.mark.parametrize(
    "change",
    [
        {"confidence": float("nan")},
        {"confidence": True},
        {"choice": "unlisted"},
        {"probabilities": {}},
        {"probabilities": {"equivalent": 0.8, "different": 0.9, "uncertain": 0.1}},
    ],
)
def test_malformed_verdicts_are_rejected(change):
    with pytest.raises(ValueError):
        validate_answer({**answer(), **change}, questions()["semantic"]["criteria"])


def test_low_confidence_and_ambiguous_distribution_abstain():
    criteria = questions()["semantic"]["criteria"]
    assert validate_answer(answer(confidence=0.5), criteria)["verdict"] == "uncertain"
    value = answer()
    value["probabilities"] = {"equivalent": 0.5, "different": 0.45, "uncertain": 0.05}
    assert validate_answer(value, criteria)["verdict"] == "uncertain"


def test_judge_failure_preserves_inference_and_worker_recovers(monkeypatch, tmp_path):
    from gauge.backend import judging

    monkeypatch.setenv("GAUGE_JUDGE", "jev")
    monkeypatch.setenv("PORTKEY_API_KEY", "test")
    entered, release = threading.Event(), threading.Event()

    class BrokenThenGood:
        def grade(self, *args):
            entered.set()
            assert release.wait(3)
            if args[-1] == "bad":
                raise httpx.ReadTimeout("contains secret that must not be saved")
            return {"status": "completed", "verdict": "equivalent"}

    monkeypatch.setattr(judging, "JevJudge", BrokenThenGood)
    store = DashboardStore(str(tmp_path / "judge.sqlite"))
    queue = JudgeQueue(store)
    jobs = []
    for n, output in enumerate(["bad", "good"]):
        e = event(n)
        job = {
            "call_id": e["call_id"],
            "candidate": "candidate",
            "status": "completed",
            "finish_reason": "stop",
            "candidate_output": output,
            "agreement": False,
        }
        store.put(job)
        jobs.append(job)
        assert queue.submit(e, job, [{"role": "user", "content": "Soap"}])
        assert not queue.submit(e, job)  # in-flight duplicates do not spend quota
    try:
        assert entered.wait(3)
        assert queue.snapshot()["active"] >= 1
        assert queue.snapshot()["completed"] == 0
    finally:
        release.set()
    wait_for(lambda: queue.jobs.unfinished_tasks == 0)
    import sqlite3

    with sqlite3.connect(store.path) as db:
        saved = [json.loads(p) for (p,) in db.execute("select payload from gauge_comparisons")]
    assert all(j["status"] == "completed" and j["agreement"] is False for j in saved)
    assert saved[0]["judgment"]["error"] == "ReadTimeout"
    assert "secret" not in json.dumps(saved)
    assert saved[1]["judgment"]["verdict"] == "equivalent"
    assert queue.snapshot()["errors"] == queue.snapshot()["completed"] == 1


def test_judge_queue_budget_drop_and_restart_are_explicit(monkeypatch, tmp_path):
    monkeypatch.setenv("GAUGE_JUDGE", "jev")
    monkeypatch.setenv("PORTKEY_API_KEY", "test")
    monkeypatch.setattr(JudgeQueue, "consume", lambda self: None)
    store = DashboardStore(str(tmp_path / "bounded.sqlite"))
    queue = JudgeQueue(store, capacity=1, budget=2)
    for n in range(2):
        e = event(n)
        job = {
            "call_id": e["call_id"],
            "candidate": "qwen",
            "status": "completed",
            "finish_reason": "stop",
            "candidate_output": "ok",
        }
        store.put(job)
        assert queue.submit(e, job) is (n == 0)
    import sqlite3

    with sqlite3.connect(store.path) as db:
        saved = [json.loads(p) for (p,) in db.execute("select payload from gauge_comparisons")]
    assert saved[1]["judgment"]["reason"] == "Judge queue full"
    DashboardStore(store.path)
    with sqlite3.connect(store.path) as db:
        saved = json.loads(
            db.execute("select payload from gauge_comparisons limit 1").fetchone()[0]
        )
    assert saved["status"] == "completed"
    assert saved["judgment"]["status"] == "skipped"
    assert "restarted" in saved["judgment"]["reason"]


def test_slow_judge_does_not_hold_comparison_worker(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from gauge.backend import judging, server

    monkeypatch.setenv("GAUGE_JUDGE", "jev")
    monkeypatch.setenv("PORTKEY_API_KEY", "test")
    monkeypatch.setenv("FIREWORKS_API_KEY", "test")
    release = threading.Event()

    class SlowJudge:
        def grade(self, *args):
            assert release.wait(3)
            return {"status": "completed", "verdict": "equivalent"}

    class FastProvider:
        def __init__(self, **kwargs):
            kwargs["http_client"].close()
            self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def create(self, **kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="household"), finish_reason="stop"
                    )
                ],
                usage=None,
            )

    monkeypatch.setattr(judging, "JevJudge", SlowJudge)
    monkeypatch.setattr(server, "OpenAI", FastProvider)
    backend = server.Backend(str(tmp_path / "independent.sqlite"), "candidate", 4, 2)
    try:
        for n in range(2):
            e = event(n)
            backend.accept(json.dumps(e).encode(), e["call_id"])
        wait_for(lambda: backend.snapshot()["completed"] == 2)
        wait_for(lambda: backend.judging.snapshot()["active"] == 2)
        assert backend.snapshot()["active"] == 0
        assert backend.judging.snapshot()["completed"] == 0
    finally:
        release.set()
    wait_for(lambda: backend.judging.jobs.unfinished_tasks == 0)
    rows = backend.dashboard.page()["records"]
    assert all(row["capture"]["status"] == "completed" for row in rows)
    assert all(row["comparisons"][0]["judgment"]["verdict"] == "equivalent" for row in rows)


def test_rubric_upgrade_preserves_history_and_deduplicates_current_version(monkeypatch, tmp_path):
    from gauge.backend.judge import RUBRIC
    from gauge.ingestion import CaptureInbox

    monkeypatch.setenv("GAUGE_JUDGE", "jev")
    monkeypatch.setenv("PORTKEY_API_KEY", "test")
    monkeypatch.setattr(JudgeQueue, "consume", lambda self: None)
    store = DashboardStore(str(tmp_path / "versions.sqlite"))
    e = event()
    inbox = CaptureInbox(store.path)
    inbox.accept(json.dumps(e).encode())
    inbox.close()
    old = {"status": "uncertain", "rubric_version": "semantic-v1", "confidence": 0.46}
    job = {
        "call_id": e["call_id"],
        "candidate": "qwen",
        "status": "completed",
        "finish_reason": "stop",
        "candidate_output": "You are welcome!",
        "judgment": old,
    }
    store.put(job)
    work = store.judgment_work(run_id=None, call_id=e["call_id"], limit=3, rubric_version=RUBRIC)
    assert len(work) == 1
    queue = JudgeQueue(store)
    assert queue.submit(*work[0])
    saved = store.page()["records"][0]["comparisons"][0]
    assert saved["judgment_history"] == [old]
    assert saved["judgment"]["rubric_version"] == RUBRIC
    assert saved["candidate_output"] == job["candidate_output"]
    assert saved["status"] == "completed"
    store.update_judgment(
        e["call_id"],
        "qwen",
        {"status": "completed", "verdict": "equivalent", "rubric_version": RUBRIC},
    )
    assert (
        store.judgment_work(run_id=None, call_id=e["call_id"], limit=3, rubric_version=RUBRIC) == []
    )
    # A stale caller cannot overwrite or re-spend a completed judgment of the current rubric.
    assert not store.update_judgment(
        e["call_id"], "qwen", {"status": "pending", "rubric_version": RUBRIC}, only_unjudged=True
    )
