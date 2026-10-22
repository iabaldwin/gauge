"""Benchmark semantics and history are checked independently of provider agreement."""

import asyncio
from types import SimpleNamespace

import pytest

from gauge.evaluation.scoring import grade
from gauge.validation.dispatch import Dispatcher
from gauge.validation.records import Scenario


def test_annotation_weighting_and_quac_exclusion():
    assert grade("coqa", {"answers": ["red", "red", "blue"]}, "red")["f1"] == 1
    assert grade("coqa", {"answers": ["red", "blue"]}, "red")["f1"] == 0.5
    assert (
        grade("quac", {"answers": ["red", "blue", "green"]}, "red")["reason"]
        == "low_human_agreement"
    )
    spec = {"answers": ["CANNOTANSWER", "red"]}
    assert grade("quac", spec, "CANNOTANSWER")["passed"]
    assert not grade("quac", spec, "cannotanswer")["passed"]
    assert grade("coqa", {"answers": ["the red fox"]}, "red")["f1"] == pytest.approx(2 / 3)


def test_native_instruction_checks_and_incomplete_outputs():
    pytest.importorskip("langdetect")
    spec = {"ids": ["change_case:english_capital"], "kwargs": [{}]}
    assert grade(
        "instruction",
        spec,
        "THIS IS AN ENGLISH SENTENCE ABOUT THE WEATHER AND THE BEAUTIFUL BLUE SKY.",
    )["passed"]
    assert not grade("instruction", spec, "Hello World")["passed"]
    assert grade("instruction", spec, "HELLO", False)["status"] == "not_scored"
    assert grade("instruction", {"ids": [], "kwargs": []}, "x")["status"] == "not_scored"


@pytest.mark.parametrize("mode", ["recorded", "source"])
def test_dispatch_preserves_selected_history_and_blocks_missing_source(monkeypatch, mode):
    requests = []

    async def exchange(client, scenario, index, history, *args):
        requests.append(list(history))
        if index == 1:
            raise RuntimeError("provider down")
        return SimpleNamespace(id=f"r{index}", _request_id=f"http{index}")

    monkeypatch.setattr("gauge.validation.dispatch.exchange", exchange)
    monkeypatch.setattr("gauge.validation.dispatch.outcome", lambda r: ({}, "generated", True))
    s = Scenario(
        "c",
        "coqa",
        "coqa",
        "chat",
        "Passage",
        [
            {"user": f"u{i}", "assistant": f"gold{i}", "expected": {"answers": [f"gold{i}"]}}
            for i in range(3)
        ],
        history_mode=mode,
    )
    ledger, scores = [], []
    dispatch = Dispatcher(None, "source", 100, ledger, scores)

    async def run():
        for i in range(3):
            await dispatch.turn(s, i)

    asyncio.run(run())
    assert requests[0] == []
    assert requests[1][1]["content"] == ("gold0" if mode == "recorded" else "generated")
    assert len(ledger) == 3
    assert len(requests) == (3 if mode == "recorded" else 2)
    if mode == "source":
        assert ledger[2]["error"] == "source_prefix_unavailable"
        assert scores[2]["score"]["status"] == "not_scored"
    else:
        assert requests[2][3]["content"] == "gold1"


def test_preflight_ignores_failures_in_other_saved_runs():
    from gauge.validation.drain import preflight

    class Backend:
        def get(self, path):
            return {"failed": 99, "completed": 4000, "judging": {"errors": 0}}

        def records(self, run_id):
            assert run_id == "current"
            return [
                {"comparisons": [{"status": "completed", "judgment": {"status": "completed"}}] * 3}
            ]

    asyncio.run(preflight(Backend(), "current"))


def test_grounded_adapters_keep_evidence_and_duplicate_annotations(tmp_path):
    import json

    from gauge.validation.conversations import coqa, quac
    from gauge.validation.prefixes import recorded_prefix

    path = tmp_path / "coqa.json"
    answers = [
        {"turn_id": i, "input_text": f"answer{i}", "span_text": f"evidence{i}"} for i in range(1, 5)
    ]
    path.write_text(
        json.dumps(
            {
                "data": [
                    {
                        "id": "c",
                        "source": "wikipedia",
                        "story": "Passage",
                        "questions": [{"turn_id": i, "input_text": f"q{i}"} for i in range(1, 5)],
                        "answers": answers,
                        "additional_answers": {"a": answers, "b": answers},
                    }
                ]
            }
        )
    )
    row = coqa(path)[0]
    assert row["turns"][0]["expected"]["answers"] == ["answer1"] * 3
    assert row["turns"][0]["evidence"] == "evidence1"
    scenario = Scenario("c", "coqa", "coqa", "chat", row["passage"], row["turns"])
    assert [m["content"] for m in recorded_prefix(scenario, 1)] == ["q1", "answer1", "q2"]
    path = tmp_path / "quac.json"
    path.write_text(
        json.dumps(
            {
                "data": [
                    {
                        "title": "Title",
                        "section_title": "Section",
                        "background": "Orientation",
                        "paragraphs": [
                            {
                                "id": "p",
                                "context": "Passage",
                                "qas": [
                                    {
                                        "id": str(i),
                                        "question": f"q{i}",
                                        "orig_answer": {"text": "CANNOTANSWER"},
                                        "answers": [{"text": "CANNOTANSWER"}],
                                    }
                                    for i in range(4)
                                ],
                            }
                        ],
                    }
                ]
            }
        )
    )
    row = quac(path)[0]
    assert (row["title"], row["section"], row["background"], row["passage"]) == (
        "Title",
        "Section",
        "Orientation",
        "Passage",
    )


def test_responses_only_dataset_supports_offline_correlation():
    from gauge.validation.checks import offline

    scenario = Scenario(
        "squad/question",
        "squad2",
        "extraction",
        "responses",
        "Passage",
        [{"user": "Question", "assistant": ""}],
        ["answer"],
    )
    result = offline([scenario])
    assert result["passed"]
    assert result["captured_calls"] == 5  # One source fixture plus independent duplicates.
