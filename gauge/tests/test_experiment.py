import asyncio
import json

import pytest

from gauge import Case, Experiment, Grade, ModelOutput, generate_cases


class FakeTarget:
    def __init__(self, name, function):
        self.name = name
        self.function = function

    def describe(self):
        return {"model": self.name}

    async def invoke(self, case, *, instructions):
        return await self.function(case, instructions)


def test_reference_and_candidate_dispatch_concurrently_without_answer_leakage():
    async def scenario():
        ready = asyncio.Event()
        seen = []

        async def infer(case, instructions):
            seen.append((case.input, instructions))
            if len(seen) == 2:
                ready.set()
            await ready.wait()  # A sequential runner deadlocks and hits its deadline.
            return ModelOutput("household")

        report = await Experiment(
            instructions="Classify.",
            reference=FakeTarget("ref", infer),
            candidates=[FakeTarget("candidate", infer)],
            timeout=1,
        ).run([Case("one", "Dish soap")])
        assert seen == [("Dish soap", "Classify.")] * 2
        assert report.summary()["candidate"]["baseline_agreement"] == 1
        assert report.summary()["candidate"]["accuracy"] is None
        assert json.loads(report.to_json())["reference_is_ground_truth"] is False

    asyncio.run(scenario())


def test_limits_order_errors_and_independent_grader():
    async def scenario():
        active, peak = 0, 0

        async def infer(case, instructions):
            nonlocal active, peak
            active += 1
            peak = max(active, peak)
            await asyncio.sleep(0)
            active -= 1
            if case.id == "bad":
                raise RuntimeError("do not retain sensitive provider error contents")
            return ModelOutput("wrong answer")

        class Judge:
            async def grade(self, **kwargs):
                return Grade(
                    "independent",
                    "v1",
                    "candidate_correctness",
                    0,
                    metadata={"reference_correct": False},
                )

        report = await Experiment(
            instructions="Task",
            reference=FakeTarget("ref", infer),
            candidates=[FakeTarget("a", infer), FakeTarget("b", infer)],
            max_concurrency=2,
            grader=Judge(),
        ).run([Case("ok", "one"), Case("bad", "two")])
        assert peak == 2
        assert [(o.case_id, o.target) for o in report.outcomes] == [
            (case, target) for case in ("ok", "bad") for target in ("ref", "a", "b")
        ]
        assert report.comparisons[0].agreement.value == 1
        assert report.comparisons[0].assessment.value == 0
        assert report.summary()["a"] == {
            "total": 2,
            "scored": 1,
            "not_scored": 1,
            "baseline_agreement": 1,
            "accuracy": None,
        }
        assert "sensitive" not in report.to_json()

    asyncio.run(scenario())


def test_preflight_rejects_before_invocation():
    async def infer(case, instructions):
        pytest.fail("Preflight must not dispatch calls")

    runner = Experiment(
        instructions="Task",
        reference=FakeTarget("ref", infer),
        candidates=[FakeTarget("candidate", infer)],
        max_calls=1,
    )
    with pytest.raises(ValueError, match="max_calls"):
        asyncio.run(runner.run([Case("one", "input")]))
    with pytest.raises(ValueError, match="unique"):
        asyncio.run(runner.run([Case("one", "input"), Case("one", "input")]))


def test_timeout_and_incomplete_outputs_are_not_disagreement():
    async def stuck(case, instructions):
        await asyncio.Event().wait()

    async def partial(case, instructions):
        return ModelOutput("partial", status="incomplete")

    report = asyncio.run(
        Experiment(
            instructions="Task",
            reference=FakeTarget("ref", stuck),
            candidates=[FakeTarget("candidate", partial)],
            timeout=0.01,
        ).run([Case("one", "input")])
    )
    assert report.outcomes[0].error == "TimeoutError"
    assert report.comparisons[0].agreement.status == "not_scored"
    assert report.summary()["candidate"]["baseline_agreement"] is None


def test_cancellation_cleans_up_all_inflight_calls():
    async def scenario():
        ready = asyncio.Event()
        active, cleaned = 0, 0

        async def blocked(case, instructions):
            nonlocal active, cleaned
            active += 1
            if active == 2:
                ready.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned += 1

        task = asyncio.create_task(
            Experiment(
                instructions="Task",
                reference=FakeTarget("ref", blocked),
                candidates=[FakeTarget("candidate", blocked)],
            ).run([Case("one", "input")])
        )
        await asyncio.wait_for(ready.wait(), 1)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert cleaned == 2

    asyncio.run(scenario())


def test_grader_failure_keeps_agreement_and_outcomes():
    async def infer(case, instructions):
        return ModelOutput("yes")

    class BrokenGrader:
        async def grade(self, **kwargs):
            raise ValueError("secret")

    report = asyncio.run(
        Experiment(
            instructions="Task",
            reference=FakeTarget("ref", infer),
            candidates=[FakeTarget("candidate", infer)],
            grader=BrokenGrader(),
        ).run([Case("one", "input")])
    )
    assert report.comparisons[0].agreement.value == 1
    assert report.comparisons[0].assessment.status == "error"
    assert "secret" not in report.to_json()


@pytest.mark.parametrize("text", ['["one", "one"]', '["one"]', "{}", '["one", 2]', "bad"])
def test_invalid_synthetic_datasets_rejected(text):
    async def generate(case, instructions):
        return ModelOutput(text)

    with pytest.raises(ValueError):
        asyncio.run(generate_cases(generator=FakeTarget("gen", generate), task="Task", count=2))


def test_synthetic_dataset_has_generation_lineage_and_no_expected_labels():
    async def generate(case, instructions):
        assert "Do not include answers" in instructions
        return ModelOutput('["Dish soap", "Apples"]', response={"id": "response_123"})

    cases = asyncio.run(
        generate_cases(
            generator=FakeTarget("gen", generate),
            task="Classification",
            count=2,
        )
    )
    assert [case.input for case in cases] == ["Dish soap", "Apples"]
    assert cases[0].provenance["source_response_id"] == "response_123"
    assert not hasattr(cases[0], "expected")
