import asyncio
import json

import httpx
import pytest
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel

from gauge import CaptureBuffer, Case, Experiment, OpenAITarget, captures, wrap
from gauge.openai import AsyncOpenAI as GaugeAsyncOpenAI
from gauge.openai import OpenAI as GaugeOpenAI


def response_json(text="household", status="completed"):
    return {
        "id": "resp_1",
        "object": "response",
        "created_at": 1,
        "model": "reference",
        "status": status,
        "parallel_tool_calls": False,
        "tool_choice": "auto",
        "tools": [],
        "output": [
            {
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "status": "completed",
                "content": [{"type": "output_text", "text": text, "annotations": []}],
            }
        ],
        "usage": {"input_tokens": 10, "output_tokens": 1, "total_tokens": 11},
    }


def chat_json(text="household"):
    return {
        "id": "chat_1",
        "object": "chat.completion",
        "created": 1,
        "model": "candidate",
        "choices": [
            {"index": 0, "finish_reason": "stop", "message": {"role": "assistant", "content": text}}
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": 1, "total_tokens": 11},
    }


def test_sync_capture_preserves_identity_copy_headers_and_native_output():
    seen = []

    def handle(request):
        seen.append(request)
        return httpx.Response(200, json=response_json())

    buffer = CaptureBuffer()
    with OpenAI(
        api_key="secret",
        base_url="https://test.invalid/v1",
        http_client=httpx.Client(transport=httpx.MockTransport(handle)),
    ) as client:
        assert wrap(client, buffer=buffer) is client
        assert wrap(client) is client
        response = client.responses.create(
            model="reference", input="Dish soap", extra_headers={"x-secret": "hidden"}
        )
        assert response.output_text == "household"
        assert seen[0].headers["x-secret"] == "hidden"
        clone = client.with_options(max_retries=0)
        clone.responses.create(model="reference", input="Apples")
        events = buffer.drain()
        assert len(events) == 2
        assert events[0].request == {"model": "reference", "input": "Dish soap"}
        assert events[0].response["id"] == response.id
        assert "secret" not in json.dumps(events[0].request)


def test_import_swap_and_copy_capture_once():
    captures.drain()
    with GaugeOpenAI(
        api_key="test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=chat_json())),
        ),
    ) as client:
        clone = client.copy(max_retries=0)
        response = clone.chat.completions.create(
            model="model",
            messages=[{"role": "user", "content": "soap"}],
        )
        assert response.choices[0].message.content == "household"
        assert len(captures.drain()) == 1


@pytest.mark.parametrize("api", ["responses", "chat.completions"])
def test_typed_parse_keeps_pydantic_model(api):
    class Label(BaseModel):
        category: str

    payload = (
        response_json('{"category":"household"}')
        if api == "responses"
        else chat_json(
            '{"category":"household"}',
        )
    )
    buffer = CaptureBuffer()
    with OpenAI(
        api_key="test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(200, json=payload)),
        ),
    ) as client:
        wrap(client, buffer=buffer)
        if api == "responses":
            result = client.responses.parse(model="ref", input="soap", text_format=Label)
            parsed = result.output_parsed
        else:
            result = client.chat.completions.parse(
                model="ref",
                messages=[{"role": "user", "content": "soap"}],
                response_format=Label,
            )
            parsed = result.choices[0].message.parsed
        assert isinstance(parsed, Label)
        assert parsed.category == "household"
        assert len(buffer.drain()) == 1


def test_async_adapters_use_native_payloads_and_suppress_recursive_capture():
    async def scenario():
        seen = []

        async def handle(request):
            seen.append(json.loads(request.content))
            body = response_json() if request.url.path.endswith("responses") else chat_json()
            details = (
                "output_tokens_details"
                if request.url.path.endswith("responses")
                else "completion_tokens_details"
            )
            body["usage"][details] = {"reasoning_tokens": 0, "text_tokens": 1}
            return httpx.Response(200, json=body)

        buffer = CaptureBuffer()
        async with AsyncOpenAI(
            api_key="test",
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handle),
            ),
        ) as client:
            wrap(client, buffer=buffer)
            report = await Experiment(
                instructions="Classify",
                reference=OpenAITarget(
                    name="ref",
                    client=client,
                    model="reference",
                    api="responses",
                ),
                candidates=[
                    OpenAITarget(
                        name="qwen",
                        client=client,
                        model="candidate",
                        api="chat.completions",
                    )
                ],
            ).run([Case("one", "Dish soap")])
            assert report.summary()["qwen"]["baseline_agreement"] == 1
            assert report.token_summary()["ref"]["final_answer_tokens"] == 1
            assert report.token_summary()["qwen"]["reasoning_tokens"] == 0
            assert report.token_summary()["qwen"]["total_output_tokens"] == 1
            assert seen[0]["input"] == "Dish soap"
            assert seen[1]["messages"][-1]["content"] == "Dish soap"
            assert buffer.drain() == []
            clone = client.with_options(max_retries=0)
            await clone.responses.create(model="reference", input="soap")
            assert len(buffer.drain()) == 1

    asyncio.run(scenario())


def test_stream_is_returned_unconsumed_and_closed_natively():
    chunk = chat_json()
    chunk["object"] = "chat.completion.chunk"
    chunk["choices"] = [{"index": 0, "delta": {"content": "household"}, "finish_reason": None}]
    body = f"data: {json.dumps(chunk)}\n\ndata: [DONE]\n\n".encode()
    buffer = CaptureBuffer()
    with OpenAI(
        api_key="test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200, content=body, headers={"content-type": "text/event-stream"}
                ),
            )
        ),
    ) as client:
        wrap(client, buffer=buffer)
        with client.chat.completions.create(model="ref", messages=[], stream=True) as stream:
            event = buffer.drain()[0]
            assert event.status == "stream_passthrough"
            assert event.response is None
            assert next(iter(stream)).choices[0].delta.content == "household"
        assert stream.response.is_closed


def test_capture_failure_never_changes_application_result():
    class BrokenBuffer(CaptureBuffer):
        def append(self, event):
            raise RuntimeError("Capture backend down")

    with OpenAI(
        api_key="test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=response_json()),
            )
        ),
    ) as client:
        wrap(client, buffer=BrokenBuffer())
        assert client.responses.create(model="ref", input="soap").output_text == "household"


def test_buffer_evicts_and_omits_oversized_payloads():
    buffer = CaptureBuffer(capacity=1, max_payload_bytes=10)
    with OpenAI(
        api_key="test",
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(200, json=response_json()),
            )
        ),
    ) as client:
        wrap(client, buffer=buffer)
        for _ in range(2):
            client.responses.create(model="ref", input="soap")
            assert buffer.flush()
    assert buffer.drain()[0].request == {"capture_omitted": "request_too_large"}
    assert buffer.dropped == 1


def test_provider_rejects_unsupported_replay_before_requests():
    async def scenario():
        async with AsyncOpenAI(
            api_key="test",
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: pytest.fail("Preflight must not dispatch requests"),
                )
            ),
        ) as client:
            with pytest.raises(ValueError, match="Unsupported evaluation parameters"):
                OpenAITarget(name="ref", client=client, model="ref", parameters={"tools": []})

    asyncio.run(scenario())


@pytest.mark.parametrize("status", ["incomplete", "refused", "unsupported"])
def test_responses_adapter_preserves_noncomparable_status(status):
    body = response_json(status="incomplete" if status == "incomplete" else "completed")
    if status == "refused":
        body["output"][0]["content"] = [{"type": "refusal", "refusal": "Cannot answer"}]
    elif status == "unsupported":
        body["output"] = [
            {
                "id": "tool_1",
                "type": "function_call",
                "call_id": "call_1",
                "name": "external_action",
                "arguments": "{}",
            }
        ]

    async def scenario():
        async with AsyncOpenAI(
            api_key="test",
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(lambda request: httpx.Response(200, json=body)),
            ),
        ) as client:
            output = await OpenAITarget(name="ref", client=client, model="ref").invoke(
                Case("one", "soap"),
                instructions="Task",
            )
            assert output.status == status
            assert output.response["id"] == "resp_1"

    asyncio.run(scenario())


def test_async_import_swap_parse_and_sdk_errors():
    class Label(BaseModel):
        category: str

    async def scenario():
        def handle(request):
            if json.loads(request.content).get("input") == "fail":
                return httpx.Response(400, json={"error": {"message": "bad input"}})
            return httpx.Response(200, json=response_json('{"category":"household"}'))

        captures.drain()
        async with GaugeAsyncOpenAI(
            api_key="test",
            max_retries=0,
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(handle),
            ),
        ) as client:
            result = await client.copy().responses.parse(
                model="ref",
                input="soap",
                text_format=Label,
            )
            assert isinstance(result.output_parsed, Label)
            from openai import BadRequestError

            with pytest.raises(BadRequestError):
                await client.responses.create(model="ref", input="fail")
        events = captures.drain()
        assert [event.status for event in events] == ["completed", "error"]
        assert events[1].error == "BadRequestError"
        assert events[1].response is None

    asyncio.run(scenario())
