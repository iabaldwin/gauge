import json
import sqlite3
import threading

import httpx
import pytest
from test_dashboard_backend import event, wait_for

from gauge.backend.agreement import AgreementEvaluator, summarize
from gauge.backend.judge import JevJudge
from gauge.backend.maintenance import RunMaintenance
from gauge.backend.store import DashboardStore
from gauge.ingestion import CaptureInbox


def row(turn, verdict, conversation="dialogue"):
    return {
        "conversation": conversation,
        "dataset": "multiwoz22",
        "turn": turn,
        "outputs": {"candidate": {"judgment": {"verdict": verdict, "status": "completed"}}},
    }


def test_first_divergence_does_not_imply_recovery_or_hide_unknown_prefix():
    data = summarize(
        [
            row(2, "equivalent"),
            row(0, "equivalent"),
            row(1, "different"),
            row(0, "uncertain", "unknown"),
            row(1, "different", "unknown"),
            row(1, "different", "missing-first"),
            row(0, "equivalent", "good"),
            row(1, "equivalent", "good"),
        ]
    )
    c = {c["id"]: c for c in data["conversations"]}
    assert c["dialogue"]["models"]["candidate"]["first_divergence"] == {"turn": 2, "exact": True}
    assert not c["dialogue"]["models"]["candidate"]["all_agree"]
    assert c["unknown"]["models"]["candidate"]["first_divergence"] == {"turn": 2, "exact": False}
    assert not c["missing-first"]["models"]["candidate"]["first_divergence"]["exact"]
    assert data["models"]["candidate"]["first_divergence"] == {"2": 1, "earlier_unresolved": 2}
    assert data["models"]["candidate"]["all_agree"] == 1
    assert data["models"]["candidate"]["agreement_through"]["3"] == {"agreed": 0, "total": 1}


@pytest.mark.parametrize(
    "valid,compliance,expected",
    [
        ("valid", "compliant", "equivalent"),
        ("questionable", "compliant", "uncertain"),
        ("valid", "violation", "different"),
        ("valid", "uncertain", "uncertain"),
    ],
)
def test_reference_judge_gates_semantic_agreement(monkeypatch, valid, compliance, expected):
    monkeypatch.setenv("PORTKEY_API_KEY", "fixture")

    def respond(request):
        payload = json.loads(request.content)
        assert payload["state"]["reply_a"] == "reference"
        assert payload["state"]["reply_b"] == "candidate"
        choices = {
            "semantic": "equivalent",
            "reference_validity": valid,
            "candidate_compliance": compliance,
            "original_compliance": "compliant",
            "reason": "none",
        }
        answers = {}
        for name, question in payload["questions"].items():
            chosen = choices[name]
            keys = question["criteria"]
            answers[name] = {
                "choice": chosen,
                "confidence": 0.99,
                "probabilities": {k: 0.98 if k == chosen else 0.02 / (len(keys) - 1) for k in keys},
            }
        return httpx.Response(200, json={"answers": answers})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = JevJudge(client).grade_reference([], "reference", "candidate")
    assert result["verdict"] == expected
    assert result["rubric_version"] == "reference-v1"


def setup_store(tmp_path):
    path = str(tmp_path / "data.sqlite")
    inbox = CaptureInbox(path)
    e = event()
    inbox.accept(json.dumps(e).encode())
    inbox.close()
    store = DashboardStore(path)
    store.save_run({"id": "run", "status": "completed", "models": ["candidate"]})
    with sqlite3.connect(path) as db:
        db.execute("INSERT INTO gauge_run_captures VALUES (?, ?)", (e["call_id"], "run"))
    store.put(
        {
            "call_id": e["call_id"],
            "candidate": "candidate",
            "status": "completed",
            "finish_reason": "stop",
            "candidate_output": "other",
            "judgment": {"verdict": "different"},
        }
    )
    case = {
        "run_id": "run",
        "conversation": "dialogue",
        "dataset": "multiwoz22",
        "turn": 0,
        "reference": "reference",
        "call_id": e["call_id"],
    }
    return store, case


def test_reference_queue_is_bounded_deduplicated_persistent_and_separate(tmp_path, monkeypatch):
    store, case = setup_store(tmp_path)
    entered, release = threading.Event(), threading.Event()
    calls = []

    class Judge:
        def grade_reference(self, context, reference, output):
            calls.append(output)
            assert reference == "reference"
            assert context[0]["content"] == "Soap"
            entered.set()
            assert release.wait(3)
            return {"status": "completed", "verdict": "equivalent"}

    monkeypatch.setattr("gauge.backend.agreement.JevJudge", Judge)
    queue = AgreementEvaluator(store, enabled=True, budget=1)
    queue.register(case)
    queue.register(case)
    with pytest.raises(ValueError):
        queue.register({**case, "reference": "changed"})
    with pytest.raises(ValueError):
        queue.register({**case, "turn": 1, "run_id": "other-project-run"})
    assert queue.submit("run", 20)["accepted"] == 1
    try:
        assert entered.wait(3)
        assert queue.submit("run", 20)["accepted"] == 0
        assert queue.stats()["pending"] == 1
    finally:
        release.set()
    wait_for(lambda: queue.jobs.unfinished_tasks == 0)
    assert len(calls) == 1
    saved = AgreementEvaluator(store, enabled=False).snapshot("run")
    assert saved["statuses"] == {"completed": 1, "unjudged": 1}
    assert store.page("run")["records"][0]["comparisons"][0]["judgment"] == {"verdict": "different"}
    maintenance = RunMaintenance(store.path)
    plan = maintenance.preview(["run"])
    maintenance.clear(["run"], plan["fingerprint"])
    assert queue.snapshot("run")["conversations"] == []


def test_failed_outputs_and_restart_never_count_as_agreement(tmp_path):
    store, case = setup_store(tmp_path)
    queue = AgreementEvaluator(store, enabled=False)
    queue.register({**case, "call_id": None})
    assert queue.snapshot("run")["statuses"] == {"unavailable": 2}
    row = queue.rows("run")[0]
    row["outputs"]["candidate"]["judgment"] = {"status": "pending"}
    with queue.db() as db:
        queue.save(db, "run", "dialogue", 0, row)
    restarted = AgreementEvaluator(store, enabled=False)
    assert restarted.snapshot("run")["models"]["candidate"]["unresolved"] == 1
    assert restarted.rows("run")[0]["outputs"]["candidate"]["judgment"]["status"] == "unavailable"


def test_semantic_departure_is_visible_even_when_reference_is_questionable():
    turn = row(0, "uncertain")
    turn["outputs"]["candidate"]["judgment"]["answers"] = {
        "semantic": {"verdict": "different"},
        "reference_validity": {"verdict": "questionable"},
    }
    data = summarize([turn])
    model = data["conversations"][0]["models"]["candidate"]
    assert model["first_divergence"] is None
    assert model["first_departure"] == {"turn": 1, "exact": True, "reference": "questionable"}
    assert data["models"]["candidate"]["first_departure"] == {"1": 1}
    assert data["models"]["candidate"]["first_divergence"] == {}


def test_missing_or_duplicate_response_ids_never_misjoin_reference_cases():
    from gauge.validation.agreement import cases

    report = {
        "dispatch_mode": "fixed-prefix",
        "run_id": "run",
        "records": [{"capture": {"response": {"output": []}, "call_id": "bad"}}],
        "ledger": [
            {
                "task": "dialogue",
                "scenario_id": "dialogue",
                "turn": 0,
                "dataset": "multiwoz22",
                "recorded_reference": "reference",
                "response_id": None,
            }
        ],
    }
    assert cases(report)[0]["call_id"] is None
    report["records"] = [
        {"capture": {"response": {"id": "same"}, "call_id": call}} for call in ("one", "two")
    ]
    with pytest.raises(ValueError, match="Ambiguous"):
        cases(report)


def test_qa_metrics_persist_but_never_decide_agreement(tmp_path):
    store, case = setup_store(tmp_path)
    queue = AgreementEvaluator(store, enabled=False)
    case.update(
        dataset="coqa", assessment={"task": "coqa", "expected": {"answers": ["other", "other"]}}
    )
    queue.register(case)
    snapshot = queue.snapshot("run")
    assert snapshot["mode"] == "reference-agreement"
    output = snapshot["conversations"][0]["turns"][0]["outputs"]["candidate"]
    assert output["score"]["f1"] == 1
    assert output["judgment"]["status"] == "unjudged"
    assert output["judgment"]["verdict"] == "uncertain"
    assert snapshot["models"]["candidate"]["all_agree"] == 0
    assert queue.stats()["calls_finished"] == 0
    assert AgreementEvaluator(store, enabled=False).snapshot("run") == snapshot
    with pytest.raises(ValueError, match="conflicts"):
        queue.register(
            {**case, "assessment": {"task": "coqa", "expected": {"answers": ["changed"]}}}
        )


def test_qa_reference_queue_uses_all_annotations_and_does_not_gate_on_f1(tmp_path, monkeypatch):
    store, case = setup_store(tmp_path)
    refs = ["some other answer", "some other answer", "another answer"]
    case.update(dataset="coqa", assessment={"task": "coqa", "expected": {"answers": refs}})
    calls = []

    class Judge:
        def grade_answers(self, context, references, output):
            assert context[0]["content"] == "Soap"
            assert references == refs
            calls.append(output)
            return {
                "status": "completed",
                "verdict": "equivalent",
                "reason": "none",
                "rubric_version": "reference-answers-v1",
            }

    monkeypatch.setattr("gauge.backend.agreement.JevJudge", Judge)
    evaluator = AgreementEvaluator(store, enabled=True)
    evaluator.register(case)
    before = evaluator.snapshot("run")
    assert before["models"]["candidate"]["first_divergence"] == {}
    assert evaluator.rows("run")[0]["outputs"]["candidate"]["score"]["f1"] < 1
    assert evaluator.submit("run", 10)["accepted"] == 2  # Original and candidate both judged.
    wait_for(lambda: evaluator.jobs.unfinished_tasks == 0)
    assert len(calls) == 2
    saved = AgreementEvaluator(store, enabled=False).snapshot("run")
    assert saved["models"]["candidate"]["all_agree"] == 1
    assert saved["models"]["candidate"]["first_divergence"] == {}
    assert evaluator.rows("run")[0]["outputs"]["candidate"]["score"]["f1"] < 1


@pytest.mark.parametrize(
    "semantic,valid,expected",
    [
        ("equivalent", "valid", "equivalent"),
        ("different", "valid", "different"),
        ("different", "questionable", "uncertain"),
        ("uncertain", "valid", "uncertain"),
    ],
)
def test_qa_judge_payload_and_reference_gating(monkeypatch, semantic, valid, expected):
    monkeypatch.setenv("PORTKEY_API_KEY", "fixture")
    references = ["running for president"] * 3 + ["on running for president"]
    messages = [
        {"role": "system", "content": "Rubio will decide whether to run for president."},
        {"role": "user", "content": "What's Rubio going to decide?"},
    ]

    def respond(request):
        payload = json.loads(request.content)
        assert payload["state"]["reference_answers"] == references
        assert payload["state"]["conversation"] == messages
        assert payload["state"]["candidate_answer"] == "Whether to run for president or not"
        assert "f1" not in payload["state"]
        assert "candidate_compliance" not in payload["questions"]
        choices = {"semantic": semantic, "reference_validity": valid, "reason": "none"}
        answers = {}
        for name, question in payload["questions"].items():
            chosen = choices[name]
            keys = question["criteria"]
            answers[name] = {
                "choice": chosen,
                "confidence": 0.99,
                "probabilities": {k: 0.98 if k == chosen else 0.02 / (len(keys) - 1) for k in keys},
            }
        return httpx.Response(200, json={"answers": answers})

    with httpx.Client(transport=httpx.MockTransport(respond)) as client:
        result = JevJudge(client).grade_answers(
            messages, references, "Whether to run for president or not"
        )
    assert result["verdict"] == expected
    assert result["rubric_version"] == "reference-answers-v1"


def test_instruction_results_remain_native(tmp_path):
    pytest.importorskip("langdetect")
    store, case = setup_store(tmp_path)
    case.update(
        dataset="multiif",
        assessment={
            "task": "instruction",
            "expected": {"ids": ["change_case:english_capital"], "kwargs": [{}]},
        },
    )
    evaluator = AgreementEvaluator(store, enabled=True)
    evaluator.register(case)
    assert evaluator.snapshot("run")["statuses"] == {"scored": 2}
    assert evaluator.submit("run", 10)["accepted"] == 0


def test_explicit_reference_retry_remains_within_budget(tmp_path, monkeypatch):
    store, case = setup_store(tmp_path)
    case.update(dataset="coqa", assessment={"task": "coqa", "expected": {"answers": ["other"]}})
    calls = []

    class Judge:
        def grade_answers(self, *args):
            calls.append(args)
            if len(calls) == 1:
                raise ValueError("malformed decision")
            return {"status": "completed", "verdict": "equivalent"}

    monkeypatch.setattr("gauge.backend.agreement.JevJudge", Judge)
    evaluator = AgreementEvaluator(store, enabled=True, budget=2)
    evaluator.register(case)
    assert evaluator.submit("run", 1)["accepted"] == 1
    wait_for(lambda: evaluator.jobs.unfinished_tasks == 0)
    assert evaluator.snapshot("run")["statuses"].get("error") == 1
    assert evaluator.submit("run", 10)["accepted"] == 1
    wait_for(lambda: evaluator.jobs.unfinished_tasks == 0)
    assert evaluator.submit("run", 10)["accepted"] == 0
    assert len(calls) == 2
