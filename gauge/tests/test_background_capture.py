"""Failure-injection tests at the real native SDK/HTTP boundary, without inference."""

import asyncio
import json
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor

import httpx
import pytest
from openai import AsyncOpenAI, OpenAI
from pydantic import BaseModel, model_serializer
from test_openai import chat_json, response_json

import gauge.capture as capture_module
from gauge import CaptureBuffer, wrap
from gauge.openai import OpenAI as GaugeOpenAI


@pytest.fixture
def buffers():
    created = []

    def make(**kwargs):
        buffer = CaptureBuffer(**kwargs)
        created.append(buffer)
        return buffer

    yield make
    for buffer in created:
        buffer.close()


def native(handler=None, cls=OpenAI):
    return cls(
        api_key="test",
        max_retries=0,
        http_client=httpx.Client(
            transport=httpx.MockTransport(
                handler or (lambda request: httpx.Response(200, json=response_json())),
            )
        ),
    )


class StalledExporter:
    def __init__(self):
        self.started = threading.Event()
        self.release = threading.Event()
        self.payloads = []

    def send(self, payload):
        self.started.set()
        assert self.release.wait(5)
        self.payloads.append(payload)
        return True

    def close(self):
        pass


def test_contested_queue_drops_instead_of_waiting(buffers):
    buffer = buffers()
    with native() as client, ThreadPoolExecutor(max_workers=1) as pool:
        wrap(client, buffer=buffer)
        buffer._lock.acquire()
        try:
            result = pool.submit(client.responses.create, model="ref", input="soap")
            assert result.result(timeout=2).output_text == "household"
            assert buffer.dropped == 1
        finally:
            buffer._lock.release()
    assert buffer.drain() == []


@pytest.mark.parametrize("asynchronous", [False, True])
def test_blocked_export_and_full_queue_do_not_delay_results(buffers, asynchronous):
    exporter = StalledExporter()
    buffer = buffers(capacity=1, exporter=exporter)

    async def async_run():
        async with AsyncOpenAI(
            api_key="test",
            http_client=httpx.AsyncClient(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=response_json()),
                )
            ),
        ) as client:
            wrap(client, buffer=buffer)
            assert (await client.responses.create(model="ref", input="first")).output_text
            assert await asyncio.to_thread(exporter.started.wait, 2)
            for i in range(2):
                result = await asyncio.wait_for(
                    client.responses.create(model="ref", input=str(i)),
                    timeout=2,
                )
                assert result.output_text == "household"

    try:
        if asynchronous:
            asyncio.run(async_run())
        else:
            with native() as client, ThreadPoolExecutor(max_workers=1) as pool:
                wrap(client, buffer=buffer)
                client.responses.create(model="ref", input="first")
                assert exporter.started.wait(2)
                for i in range(2):
                    result = pool.submit(client.responses.create, model="ref", input=str(i))
                    assert result.result(timeout=2).output_text == "household"
        assert buffer.dropped == 1
    finally:
        exporter.release.set()
        assert buffer.flush()


@pytest.mark.parametrize("api", ["responses", "chat.completions"])
def test_capture_never_calls_application_serializer(buffers, api):
    class Label(BaseModel):
        category: str
        dumps: int = 0

        @model_serializer
        def serializer(self):
            self.dumps += 1
            raise RuntimeError("Must never execute during capture")

    payload = (response_json if api == "responses" else chat_json)('{"category":"household"}')
    buffer = buffers()
    with native(lambda request: httpx.Response(200, json=payload)) as client:
        wrap(client, buffer=buffer)
        if api == "responses":
            response = client.responses.parse(model="ref", input="soap", text_format=Label)
            parsed = response.output_parsed
        else:
            response = client.chat.completions.parse(
                model="ref",
                messages=[{"role": "user", "content": "soap"}],
                response_format=Label,
            )
            parsed = response.choices[0].message.parsed
        events = buffer.drain()
        assert parsed.dumps == 0
        assert buffer.failed == 0
        assert len(events) == 1
        assert events[0].response == payload
        # Schema capture is the native generated JSON, not another schema-hook invocation.
        assert ("text" if api == "responses" else "response_format") in events[0].request


def test_capture_pair_is_immutable_after_application_mutations(buffers):
    exporter = StalledExporter()
    buffer = buffers(exporter=exporter)
    try:
        with native() as client:
            wrap(client, buffer=buffer)
            client.responses.create(model="ref", input="first")
            assert exporter.started.wait(2)
            messages = [{"role": "user", "content": "original"}]
            response = client.responses.create(model="ref", input=messages)
            messages[0]["content"] = "changed"
            response.output[0].content[0].text = "changed"
        exporter.release.set()
        event = buffer.drain()[1]
        assert event.request["input"][0]["content"] == "original"
        assert event.response["output"][0]["content"][0]["text"] == "household"
        decoded = event.request
        decoded["input"][0]["content"] = "changed again"
        assert event.request["input"][0]["content"] == "original"
    finally:
        exporter.release.set()


def test_large_body_not_retained_and_queue_has_byte_budget(buffers):
    buffer = buffers(max_payload_bytes=1000)
    with native(lambda request: httpx.Response(200, json=response_json("x" * 1_000_000))) as client:
        wrap(client, buffer=buffer)
        response = client.responses.create(model="ref", input="soap")
        assert len(response.output_text) == 1_000_000
        event = buffer.drain()[0]
        assert event.response_json is None
        assert event.omission_reason == "response_too_large"
        assert event.size_bytes < 3000
        assert not event.to_dict()["replay_eligible"]

    exporter = StalledExporter()
    buffer = buffers(capacity=100, max_buffer_bytes=3000, exporter=exporter)
    try:
        with native() as client:
            wrap(client, buffer=buffer)
            client.responses.create(model="ref", input="first")
            assert exporter.started.wait(2)
            for i in range(5):
                client.responses.create(model="ref", input=str(i))
            assert len(buffer._pending) == 1
            assert buffer._pending_bytes <= 3000
            assert buffer.dropped == 4
    finally:
        exporter.release.set()


def test_capture_worker_start_failure_keeps_native_client(buffers, monkeypatch):
    buffer = buffers()
    monkeypatch.setattr(buffer, "start", lambda: (_ for _ in ()).throw(RuntimeError("No worker")))
    with native() as client:
        original = client.request
        assert wrap(client, buffer=buffer) is client
        assert client.request == original
        assert client.responses.create(model="ref", input="soap").output_text == "household"
        assert buffer.failed == 1


def test_failed_install_rolls_back_every_method(buffers):
    class RejectInstall(OpenAI):
        def __setattr__(self, name, value):
            if name == "_process_response":
                raise RuntimeError("Injected setup failure")
            super().__setattr__(name, value)

    buffer = buffers()
    with native(cls=RejectInstall) as client:
        original = client.request
        wrap(client, buffer=buffer)
        assert client.request == original
        assert "request" not in vars(client)
        assert "_build_request" not in vars(client)
        assert client.responses.create(model="ref", input="soap").output_text == "household"
        assert buffer.failed == 1


def test_import_swap_and_clone_ignore_instrumentation_failure(buffers, monkeypatch):
    import gauge.openai as facade

    def fail(*args, **kwargs):
        raise RuntimeError("Gauge setup failed")

    monkeypatch.setattr(facade, "wrap", fail)
    with native(cls=GaugeOpenAI) as client:
        assert client.responses.create(model="ref", input="soap").output_text == "household"
    with native() as client:
        wrap(client, buffer=buffers())
        monkeypatch.setattr(capture_module, "wrap", fail)
        clone = client.with_options(max_retries=0)
        assert clone.responses.create(model="ref", input="soap").output_text == "household"


def test_native_retries_stay_native_and_capture_once(buffers):
    attempts = []

    def handle(request):
        attempts.append(request)
        return httpx.Response(500 if len(attempts) < 3 else 200, json=response_json())

    buffer = buffers()
    with native(handle) as client:
        client.max_retries = 2
        client._sleep_for_retry = lambda **kwargs: None
        wrap(client, buffer=buffer)
        assert client.responses.create(model="ref", input="soap").output_text == "household"
        events = buffer.drain()
        assert len(attempts) == 3
        assert len(events) == 1
        assert events[0].attempts == 3


def test_async_cancellation_propagates_and_is_not_retried(buffers):
    async def run():
        started = asyncio.Event()
        calls = 0

        async def handle(request):
            nonlocal calls
            calls += 1
            started.set()
            await asyncio.Event().wait()

        buffer = buffers()
        async with AsyncOpenAI(
            api_key="test",
            http_client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
        ) as client:
            wrap(client, buffer=buffer)
            task = asyncio.create_task(client.responses.create(model="ref", input="soap"))
            await started.wait()
            task.cancel()
            with pytest.raises(asyncio.CancelledError):
                await task
            events = await asyncio.to_thread(buffer.drain)
            assert calls == 1
            assert events[0].status == "cancelled"

    asyncio.run(run())


def test_cached_raw_helpers_capture_and_stream_bodies_remain_unread(buffers):
    class Body(httpx.SyncByteStream):
        consumed = False

        def __iter__(self):
            self.consumed = True
            yield json.dumps(response_json()).encode()

    body = Body()
    buffer = buffers()
    with native(
        lambda request: httpx.Response(
            200,
            stream=body,
            headers={"content-type": "application/json"},
        )
    ) as client:
        helper = client.responses.with_streaming_response
        wrap(client, buffer=buffer)
        with helper.create(model="ref", input="soap") as response:
            assert not body.consumed
            event = buffer.drain()[0]
            assert event.status == "stream_passthrough"
            assert event.response is None
            assert not event.to_dict()["replay_eligible"]
            assert response.parse().output_text == "household"
        assert body.consumed

    buffer = buffers()
    with native() as client:
        helper = client.responses.with_raw_response
        wrap(client, buffer=buffer)
        response = helper.create(model="ref", input="soap", metadata={"tag": "x"})
        assert response.parse().output_text == "household"
        assert buffer.drain()[0].request["metadata"] == {"tag": "x"}


def test_runtime_disable_and_unsupported_sdk_are_passthrough(buffers, monkeypatch):
    buffer = buffers()
    with native() as client:
        wrap(client, buffer=buffer)
        monkeypatch.setenv("GAUGE_ENABLED", "0")
        assert client.responses.create(model="ref", input="soap").output_text == "household"
        assert buffer.drain() == []
    monkeypatch.delenv("GAUGE_ENABLED")
    monkeypatch.setattr("openai.__version__", "99.0.0")
    with native() as client:
        wrap(client, buffer=buffer)
        assert client.responses.create(model="ref", input="soap").output_text == "household"
        assert buffer.drain() == []
        assert buffer.failed == 1


def test_import_swap_does_not_import_backend_modules():
    code = """
import sys
from gauge.openai import OpenAI
assert 'gauge.experiment' not in sys.modules
assert 'gauge.providers' not in sys.modules
assert 'gauge.ingestion' not in sys.modules
assert 'gauge.html' not in sys.modules
"""
    subprocess.run([sys.executable, "-c", code], check=True, timeout=10)


def test_fork_discards_inherited_locks_and_recovers_on_client_setup():
    import os

    if not hasattr(os, "fork"):
        pytest.skip("POSIX fork only")
    code = """
import os
import httpx
from openai import OpenAI
from gauge import CaptureBuffer, wrap
buffer = CaptureBuffer()
client = OpenAI(api_key="test", http_client=httpx.Client(transport=httpx.MockTransport(
    lambda request: httpx.Response(200, json={"id":"r","object":"response","output":[]})
)))
wrap(client, buffer=buffer)
client.responses.create(model="ref", input="warmup")
assert buffer.flush()
buffer._lock.acquire()
pid = os.fork()
if pid == 0:
    try:
        assert not buffer._accepting
        client.responses.create(model="ref", input="inherited")
        assert buffer.dropped >= 1
        wrap(client, buffer=buffer)
        client.responses.create(model="ref", input="recovered")
        assert buffer.flush()
        assert len(buffer.drain()) == 1
        os._exit(0)
    except BaseException:
        os._exit(1)
buffer._lock.release()
_, status = os.waitpid(pid, 0)
assert os.waitstatus_to_exitcode(status) == 0
buffer.close()
client.close()
"""
    subprocess.run(
        [sys.executable, "-W", "ignore::DeprecationWarning", "-c", code], check=True, timeout=10
    )


def test_exit_does_not_require_application_flush_or_close():
    code = """
import threading
from gauge import CaptureBuffer
from gauge.wire import CaptureEvent
class StuckExporter:
    started = threading.Event()
    def send(self, payload):
        self.started.set()
        threading.Event().wait(60)
    def close(self):
        pass
exporter = StuckExporter()
buffer = CaptureBuffer(exporter=exporter)
buffer.start()
buffer.submit(CaptureEvent("call_" + "a"*32, "responses.create", b"{}", b"{}", "completed", 0, 0))
assert exporter.started.wait(2)
# Interpreter exits now, even though export is stuck and no lifecycle methods were called.
"""
    subprocess.run([sys.executable, "-c", code], check=True, timeout=5)


@pytest.mark.parametrize("asynchronous", [False, True])
def test_httpx2_transport_compatibility(buffers, asynchronous):
    import httpx2

    buffer = buffers()
    transport = httpx2.MockTransport(lambda request: httpx2.Response(200, json=response_json()))
    if asynchronous:

        async def run():
            async with AsyncOpenAI(
                api_key="test", http_client=httpx2.AsyncClient(transport=transport)
            ) as client:
                wrap(client, buffer=buffer)
                assert (
                    await client.responses.create(model="ref", input="soap")
                ).output_text == "household"

        asyncio.run(run())
    else:
        with OpenAI(api_key="test", http_client=httpx2.Client(transport=transport)) as client:
            wrap(client, buffer=buffer)
            assert client.responses.create(model="ref", input="soap").output_text == "household"
    assert buffer.drain()[0].response["id"] == "resp_1"
