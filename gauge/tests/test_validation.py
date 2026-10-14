import json

import pytest

from gauge.backend.correlation import correlate, workload_id
from gauge.backend.replay import ReplayUnavailable, materialize
from gauge.validation.records import Scenario, read_suite, write_suite
from gauge.validation.scoring import aggregate, grade


def capture(call, response, previous=None, conversation=None, content="question", answer="answer"):
    request = {"model": "source", "instructions": "contract", "input": content}
    if previous:
        request["previous_response_id"] = previous
    if conversation:
        request["conversation"] = conversation
    return {
        "call_id": call,
        "operation": "responses.create",
        "endpoint_host": "api.test",
        "status": "completed",
        "ineligible_reason": "state_or_tools" if previous or conversation else None,
        "request": request,
        "response": {
            "id": response,
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": answer}]}],
        },
    }


def test_explicit_branches_order_independence_and_conversation_membership():
    a = capture("a", "ra")
    b = capture("b", "rb", "ra")
    c = capture("c", "rc", "ra")
    graph = correlate([c, b, a])
    assert graph["b"]["parent_call_id"] == graph["c"]["parent_call_id"] == "a"
    assert len({r["trace_id"] for r in graph.values()}) == 1
    x, y = capture("x", "rx", conversation="conv"), capture("y", "ry", conversation={"id": "conv"})
    graph = correlate([x, y])
    assert graph["x"]["trace_id"] == graph["y"]["trace_id"]
    assert graph["y"]["parent_call_id"] is None  # membership doesn't establish ordering


def test_independent_duplicates_ambiguous_provider_ids_and_missing_parents():
    a, b = capture("a", "same"), capture("b", "same")
    child = capture("child", "rc", "same")
    missing = capture("missing", "rm", "outside")
    graph = correlate([a, b, child, missing])
    assert graph["a"]["trace_id"] != graph["b"]["trace_id"]
    assert graph["child"]["parent_call_id"] is None
    assert graph["child"]["evidence"] == graph["missing"]["evidence"] == "unresolved_parent"
    other = capture("other", "same")
    other["endpoint_host"] = "different.test"
    graph = correlate([a, child, other])
    assert graph["child"]["parent_call_id"] == "a"


def test_history_does_not_establish_identity_and_contract_ignores_input_and_model():
    a = capture("a", "ra")
    b = capture(
        "b",
        "rb",
        content=[
            {"role": "user", "content": "question"},
            {"role": "assistant", "content": "answer"},
            {"role": "user", "content": "follow up"},
        ],
    )
    b["request"]["model"] = "different"
    graph = correlate([a, b])
    assert graph["b"]["evidence"] == "history_without_id"
    assert graph["b"]["parent_call_id"] is None
    assert workload_id(a) == workload_id(b)
    b["request"]["instructions"] = "changed contract"
    assert workload_id(a) != workload_id(b)


def test_chain_replay_expands_actual_parent_output_and_current_instructions():
    a = capture("a", "ra", answer="actual model answer")
    b = capture("b", "rb", "ra", content="follow up")
    b["request"]["instructions"] = "new instructions"
    messages = materialize(b, lambda rid, host: a if rid == "ra" and host == "api.test" else None)
    assert messages == [
        {"role": "system", "content": "new instructions"},
        {"role": "user", "content": "question"},
        {"role": "assistant", "content": "actual model answer"},
        {"role": "user", "content": "follow up"},
    ]
    with pytest.raises(ReplayUnavailable, match="parent_context_unavailable"):
        materialize(b, lambda *_: None)
    with pytest.raises(ReplayUnavailable, match="conversation"):
        materialize(capture("c", "rc", conversation="conv"), lambda *_: None)
    a["request"]["tools"] = [{"type": "function"}]
    with pytest.raises(ReplayUnavailable, match="unsupported"):
        materialize(b, lambda *_: a)


def test_missing_or_failed_context_and_cycles_never_replay():
    a, b = capture("a", "ra", "rb"), capture("b", "rb", "ra")
    graph = correlate([a, b])
    assert all(row["evidence"] == "invalid_parent_cycle" for row in graph.values())
    assert graph["a"]["trace_id"] != graph["b"]["trace_id"]
    with pytest.raises(ReplayUnavailable, match="cycle"):
        materialize(a, lambda rid, _: {"ra": a, "rb": b}[rid])
    failed = capture("failed", "rf")
    failed["status"] = "error"
    failed["response"] = None
    with pytest.raises(ReplayUnavailable):
        materialize(failed, lambda *_: None)


def test_gold_scoring_does_not_depend_on_original_model_agreement():
    assert grade("classification", "correct", "wrong")["accuracy"] == 0
    assert grade("classification", "correct", " correct ")["accuracy"] == 1
    assert grade("extraction", [], '{"answer": null}')["exact_match"] == 1
    assert grade("extraction", ["a blue boat"], '{"answer": "blue boat"}')["f1"] == 1
    assert grade("extraction", ["boat"], "boat")["schema_valid"] is False
    assert grade("extraction", ["boat"], '{"answer":"boat","extra":true}')["schema_valid"] is False
    assert grade("extraction", ["boat"], '{"answer":null}')["exact_match"] == 0
    assert grade("dialogue", None, "plausible answer")["status"] == "not_scored"
    rows = [
        {
            "dataset": "banking77",
            "target": "candidate",
            "score": grade("classification", "yes", text, ok),
        }
        for text, ok in [("yes", True), ("no", True), (None, False)]
    ]
    summary = aggregate(rows)["banking77/candidate"]
    assert summary["calls"] == 3 and summary["scored"] == 2 and summary["not_scored"] == 1
    assert summary["metrics"]["accuracy"] == {"mean": 0.5, "n": 2}


def test_manifest_is_reproducible_and_detects_modified_content(tmp_path):
    scenario = Scenario(
        "id",
        "dataset",
        "classification",
        "chat",
        "label only",
        [{"user": "input", "assistant": ""}],
        "label",
    )
    first, second = tmp_path / "a.json", tmp_path / "b.json"
    write_suite(first, [scenario], [], "test")
    write_suite(second, [scenario], [], "test")
    assert first.read_bytes() == second.read_bytes()
    assert read_suite(first)[1] == [scenario]
    data = json.loads(first.read_text())
    data["scenarios"][0]["expected"] = "modified"
    first.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="modified"):
        read_suite(first)


def test_real_shape_adapters_keep_dialogue_order_and_ids(tmp_path):
    from gauge.validation.datasets import banking, dialogues

    bank = tmp_path / "bank.csv"
    bank.write_text("text,category\nhello,greeting\n")
    assert banking(bank) == [{"id": "0", "text": "hello", "label": "greeting"}]
    chat = tmp_path / "chat.json"
    chat.write_text(
        json.dumps(
            [
                {
                    "dialogue_id": "dialogue",
                    "turns": [
                        {"speaker": role, "utterance": str(i)}
                        for i in range(4)
                        for role in ("USER", "SYSTEM")
                    ],
                }
            ]
        )
    )
    assert dialogues(chat)[0]["turns"] == [{"user": str(i), "assistant": str(i)} for i in range(4)]


def test_offline_suite_detects_real_grouping_without_oracle_annotations():
    from gauge.validation.checks import offline

    scenarios = [
        Scenario(
            "single",
            "banking77",
            "classification",
            "chat",
            "classify",
            [{"user": "same", "assistant": "same"}],
            "label",
        )
    ]
    for protocol in ("chat", "responses", "chain", "conversation"):
        scenarios.append(
            Scenario(
                protocol,
                "multiwoz22",
                "dialogue",
                protocol,
                "assist",
                [{"user": "same", "assistant": "same"}, {"user": "next", "assistant": "next"}],
            )
        )
    score = offline(scenarios)
    assert score["passed"] and score["correct_parent_links"] == 1
    assert score["history_only_turns_left_unresolved"] == 2
    assert score["false_trace_merges"] == 0


def test_trace_cycles_are_unresolved_without_merging_the_cycle():
    a, b = capture("a", "ra", "rb"), capture("b", "rb", "ra")
    for events in ([a, b], [b, a]):
        graph = correlate(events)
        assert all(r["evidence"] == "invalid_parent_cycle" for r in graph.values())
        assert graph["a"]["trace_id"] != graph["b"]["trace_id"]


def test_cache_integrity_is_checked_without_network(tmp_path, monkeypatch):
    import hashlib
    from dataclasses import asdict

    import httpx

    from gauge.validation.sources import Source, fetch

    source = Source("test", "https://example.test/pinned-file", "revision", "test", "test", "test")
    key = hashlib.sha256(source.url.encode()).hexdigest()[:20]
    (tmp_path / key).write_bytes(b"original")
    (tmp_path / (key + ".json")).write_text(
        json.dumps({**asdict(source), "sha256": hashlib.sha256(b"original").hexdigest()})
    )
    monkeypatch.setattr(httpx, "Client", lambda **kw: pytest.fail("Cache hit must not use network"))
    assert fetch(source, tmp_path)[0].read_bytes() == b"original"
    (tmp_path / key).write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        fetch(source, tmp_path)


def test_parquet_question_adapter_retains_answer_aliases_and_unanswerable(tmp_path):
    pa = pytest.importorskip("pyarrow")
    pq = pytest.importorskip("pyarrow.parquet")
    from gauge.validation.datasets import questions

    rows = [
        {
            "id": "a",
            "context": "A blue boat",
            "question": "What?",
            "answers": {"text": ["a blue boat", "blue boat"], "answer_start": [0, 2]},
        },
        {
            "id": "b",
            "context": "A blue boat",
            "question": "Who?",
            "answers": {"text": [], "answer_start": []},
        },
    ]
    path = tmp_path / "questions.parquet"
    pq.write_table(pa.Table.from_pylist(rows), path)
    assert questions(path) == rows


def test_backend_chain_replay_uses_persisted_context_and_graph_survives_restart(
    tmp_path, monkeypatch
):
    from gauge.backend.server import Backend
    from gauge.backend.store import DashboardStore
    from gauge.wire import CaptureEvent

    monkeypatch.setattr(Backend, "consume", lambda self: None)
    path = str(tmp_path / "backend.sqlite")
    backend = Backend(path, "candidate", 10, 10)

    def send(i, previous=None, conversation=None):
        sample = capture("unused", "r" + str(i), previous, conversation, answer="answer " + str(i))
        envelope = CaptureEvent(
            "call_" + f"{i:032x}",
            "responses.create",
            json.dumps(sample["request"]).encode(),
            json.dumps(sample["response"]).encode(),
            "completed",
            10,
            float(i),
            endpoint_host="api.test",
        ).to_dict()
        backend.accept(json.dumps(envelope).encode(), envelope["call_id"])
        return envelope

    root = send(1)
    child = send(2, "r1")
    missing = send(3, "absent")
    assert backend.snapshot()["ineligible"] == 1
    with backend.jobs.mutex:
        work = list(backend.jobs.queue)
    assert work[1]["messages"][2] == {"role": "assistant", "content": "answer 1"}
    graph = backend.dashboard.trace_graph()
    assert graph[child["call_id"]]["parent_call_id"] == root["call_id"]
    assert graph[missing["call_id"]]["evidence"] == "unresolved_parent"
    assert DashboardStore(path).trace_graph() == graph


def test_fixed_prefixes_never_use_new_model_outputs():
    import asyncio
    from types import SimpleNamespace

    from gauge.validation.prefixes import recorded_prefix, verify_prefixes
    from gauge.validation.protocols import exchange

    scenario = Scenario(
        "conversation",
        "multiwoz22",
        "dialogue",
        "chat",
        "assist",
        [
            {"user": "u1", "assistant": "a1"},
            {"user": "u2", "assistant": "a2"},
            {"user": "u3", "assistant": "a3"},
        ],
    )
    assert recorded_prefix(scenario, 0) == [{"role": "user", "content": "u1"}]
    assert [m["content"] for m in recorded_prefix(scenario, 1)] == ["u1", "a1", "u2"]
    assert [m["content"] for m in recorded_prefix(scenario, 2)] == ["u1", "a1", "u2", "a2", "u3"]
    records, ledger = [], []

    async def create(**request):
        i = len(records)
        records.append(
            {
                "capture": {
                    "call_id": f"call_{i}",
                    "request": request,
                    "response": {"id": f"resp_{i}"},
                }
            }
        )
        return f"e{i + 1}"

    client = SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))

    async def dispatch():
        for turn in range(3):
            generated = await exchange(
                client, scenario, turn, recorded_prefix(scenario, turn)[:-1], None, None, "model"
            )
            assert generated == f"e{turn + 1}"
            ledger.append({"response_id": f"resp_{turn}", "scenario_id": scenario.id, "turn": turn})

    asyncio.run(dispatch())
    assert verify_prefixes(records, ledger, [scenario])["passed"]
    records[2]["capture"]["request"]["messages"][2]["content"] = "e1"
    result = verify_prefixes(records, ledger, [scenario])
    assert not result["passed"] and result["mismatches"] == ["call_2"]


def test_fixed_suite_samples_unique_conversations_with_self_contained_protocols(
    tmp_path, monkeypatch
):
    from gauge.validation import suite

    monkeypatch.setattr(
        suite, "fetch", lambda source, cache: (tmp_path / "data", {"name": source.name})
    )
    monkeypatch.setattr(
        suite.datasets,
        "banking",
        lambda _: [
            {"id": str(i), "label": f"intent_{i % 77}", "text": f"bank {i}"} for i in range(400)
        ],
    )
    monkeypatch.setattr(
        suite.datasets,
        "questions",
        lambda _: [
            {
                "id": str(i),
                "context": "passage",
                "question": "question",
                "answers": {"text": ["passage"] if i % 2 else []},
            }
            for i in range(200)
        ],
    )
    monkeypatch.setattr(
        suite.datasets,
        "dialogues",
        lambda _: [
            {"id": str(i), "turns": [{"user": f"u{j}", "assistant": f"a{j}"} for j in range(4)]}
            for i in range(100)
        ],
    )
    path = tmp_path / "fixed.json"
    manifest = suite.prepare(path, tmp_path, "full", fixed_prefix=True)
    _, scenarios = read_suite(path)
    chats = [s for s in scenarios if s.task == "dialogue"]
    assert manifest["dispatch_mode"] == "fixed-prefix"
    assert manifest["source_calls"] == 1000
    assert len(chats) == len({s.provenance["source_id"] for s in chats}) == 100
    assert {s.protocol for s in scenarios} == {"chat", "responses"}
