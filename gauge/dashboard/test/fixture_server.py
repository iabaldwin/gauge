"""Browser-test server with an explicit fake provider; never contacts inference APIs."""

import sys
import time
from types import SimpleNamespace

from gauge.backend import agreement, server


class FixtureProvider:
    def __init__(self, **kwargs):
        kwargs["http_client"].close()
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self.create))

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def create(self, **kwargs):
        time.sleep(0.15)
        if kwargs["messages"][-1]["content"] == "Provider failure":
            raise RuntimeError("Test provider failure")
        return SimpleNamespace(
            choices=[
                SimpleNamespace(message=SimpleNamespace(content="household"), finish_reason="stop")
            ],
            usage=SimpleNamespace(
                model_dump=lambda: {
                    "prompt_tokens": 25,
                    "completion_tokens": 35,
                    "completion_tokens_details": {"reasoning_tokens": 30},
                }
            ),
        )


class FixtureJudge:
    def grade_answers(self, context, references, output):
        assert references == ["household cleaning supplies"] * 3
        assert output == "household"
        return {
            "status": "completed",
            "verdict": "equivalent",
            "confidence": 0.99,
            "reason": "none",
            "rubric_version": "reference-answers-v1",
        }

    def grade_reference(self, context, reference, output):
        return {
            "status": "completed",
            "verdict": "equivalent" if reference == output else "different",
            "confidence": 0.99,
            "reason": "none" if reference == output else "fact",
        }


class FixtureAgreement(agreement.AgreementEvaluator):
    def __init__(self, store, **kwargs):
        super().__init__(store, enabled=True)


agreement.JevJudge = FixtureJudge
server.AgreementEvaluator = FixtureAgreement
server.OpenAI = FixtureProvider
server.serve(0, sys.argv[1], "test/fireworks-candidate", 1, 8)
