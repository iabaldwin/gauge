import asyncio
import importlib.util
from pathlib import Path
from unittest.mock import AsyncMock

import httpx
import pytest
from openai import NotFoundError

from gauge import Case, Experiment


def missing_model():
    return NotFoundError(
        "model unavailable; provider body may contain sensitive data",
        response=httpx.Response(404, request=httpx.Request("POST", "https://test.invalid")),
        body={"error": "sensitive provider details"},
    )


def load_example():
    path = Path(__file__).parents[1] / "examples" / "openai_fireworks.py"
    spec = importlib.util.spec_from_file_location("gauge_live_example", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_report_explains_404_without_provider_body():
    class Target:
        def __init__(self, name):
            self.name = name

        def describe(self):
            return {"model": "unavailable"}

        async def invoke(self, case, *, instructions):
            raise missing_model()

    report = asyncio.run(
        Experiment(
            instructions="Task",
            reference=Target("ref"),
            candidates=[Target("candidate")],
        ).run([Case("one", "input")])
    )
    assert report.outcomes[0].error_status_code == 404
    diagnostic = report.diagnostics()["candidate"]
    assert diagnostic["statuses"] == {"NotFoundError": 1}
    assert diagnostic["http_statuses"] == {"404": 1}
    assert "catalog" in diagnostic["hint"]
    assert "sensitive" not in report.to_json()


def test_live_example_checks_candidate_before_generation_or_reference(monkeypatch):
    module = load_example()
    generation = AsyncMock(side_effect=AssertionError("Generation must not run"))
    monkeypatch.setattr(module, "generate_cases", generation)
    for key in ("OPENAI_API_KEY", "FIREWORKS_API_KEY"):
        monkeypatch.setenv(key, "test")
    monkeypatch.setenv("FIREWORKS_MODELS", "model-a,model-b")

    class Client:
        def __init__(self, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            pass

    calls = []

    class Target:
        def __init__(self, **kwargs):
            self.model = kwargs["model"]
            self.name = kwargs["name"]

        async def invoke(self, *args, **kwargs):
            calls.append(self.name)
            raise missing_model()

    monkeypatch.setattr(module, "AsyncOpenAI", Client)
    monkeypatch.setattr(module, "OpenAITarget", Target)
    with pytest.raises(SystemExit, match="HTTP 404") as failure:
        asyncio.run(module.main())
    assert "sensitive" not in str(failure.value)
    assert calls == ["fireworks/model-a", "fireworks/model-b"]
    generation.assert_not_called()
