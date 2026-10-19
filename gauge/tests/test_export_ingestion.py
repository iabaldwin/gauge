import json
import time
from dataclasses import replace

import httpx
import pytest
from test_openai import response_json

from gauge.export import HTTPExporter
from gauge.ingestion import CaptureConflict, CaptureInbox, validate_capture
from gauge.wire import MAX_ENVELOPE_BYTES, CaptureEvent


def event(**kwargs):
    return replace(
        CaptureEvent(
            id="call_" + "a" * 32,
            operation="responses.create",
            request_json=b'{"model":"ref","input":"soap"}',
            response_json=json.dumps(response_json()).encode(),
            status="completed",
            latency_ms=10,
            created_at=time.time(),
            attempts=1,
        ),
        **kwargs,
    )


def encoded(item):
    return json.dumps(item.to_dict(), separators=(",", ":")).encode()


def test_export_retries_are_bounded_and_keep_same_call_id(monkeypatch):
    attempts = []
    monkeypatch.setenv("OPENAI_API_KEY", "never-export-this")

    def handle(request):
        attempts.append(request)
        return httpx.Response(503)

    exporter = HTTPExporter(
        "https://gauge.invalid/v1/captures",
        max_retries=1,
        failure_threshold=2,
        transport=httpx.MockTransport(handle),
    )
    try:
        payload = event().to_dict()
        assert not exporter.send(payload)
        assert not exporter.send(payload)
        assert len(attempts) == 4
        assert not exporter.send(payload)  # Circuit is now open: no HTTP request.
        assert len(attempts) == 4
        assert {r.headers["idempotency-key"] for r in attempts} == {payload["call_id"]}
        assert all("authorization" not in r.headers for r in attempts)
        assert all(b"never-export-this" not in r.content for r in attempts)
        exporter._open_until = 0
        assert not exporter.send(payload)  # Recovery probe is bounded too.
        assert len(attempts) == 6
    finally:
        exporter.close()


def test_success_never_reads_ack_body_and_redirect_is_not_followed():
    class Unreadable(httpx.SyncByteStream):
        def __iter__(self):
            pytest.fail("Exporter must not consume receipt body")
            yield b""

    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(202, stream=Unreadable())

    exporter = HTTPExporter(
        "https://gauge.invalid/v1/captures", transport=httpx.MockTransport(handle)
    )
    try:
        assert exporter.send(event().to_dict())
        assert len(requests) == 1
    finally:
        exporter.close()

    def redirect(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://elsewhere.invalid/"})

    exporter = HTTPExporter(
        "https://gauge.invalid/v1/captures", transport=httpx.MockTransport(redirect)
    )
    try:
        assert not exporter.send(event().to_dict())
        assert len(requests) == 2
    finally:
        exporter.close()


def test_network_exceptions_open_circuit_without_escaping():
    attempts = []

    def timeout(request):
        attempts.append(request)
        raise httpx.ReadTimeout("offline", request=request)

    exporter = HTTPExporter(
        "https://gauge.invalid/v1/captures",
        transport=httpx.MockTransport(timeout),
        failure_threshold=1,
        max_retries=1,
    )
    try:
        assert not exporter.send(event().to_dict())
        assert not exporter.send(event().to_dict())
        assert len(attempts) == 2
    finally:
        exporter.close()


@pytest.mark.parametrize(
    "endpoint",
    [
        "http://remote.invalid/captures",
        "https://user:secret@host/captures",
        "https://host/captures?token=secret",
        "ftp://host/captures",
    ],
)
def test_bad_endpoint_rejected(endpoint):
    with pytest.raises(ValueError):
        HTTPExporter(endpoint)


def test_durable_ingestion_is_idempotent_and_never_replaces_reference(tmp_path):
    path = str(tmp_path / "inbox.sqlite")
    item = event()
    inbox = CaptureInbox(path)
    try:
        assert inbox.accept(encoded(item)) == (item.id, True)
        assert inbox.accept(encoded(item)) == (item.id, False)
        with pytest.raises(CaptureConflict):
            inbox.accept(encoded(replace(item, request_json=b'{"model":"ref","input":"changed"}')))
        assert inbox.get(item.id)["request"]["input"] == "soap"
        assert inbox.get(item.id)["replay_eligible"]
    finally:
        inbox.close()
    restarted = CaptureInbox(path)
    try:
        assert restarted.accept(encoded(item)) == (item.id, False)
    finally:
        restarted.close()


@pytest.mark.parametrize(
    "changes,reason",
    [
        ({"status": "cancelled", "response_json": None}, "no_completed_response"),
        ({"status": "stream_passthrough", "response_json": None}, "no_completed_response"),
        ({"request_json": None, "omission_reason": "request_too_large"}, "request_too_large"),
        (
            {"request_json": b'{"model":"ref","input":"x","previous_response_id":"old"}'},
            "state_or_tools",
        ),
        (
            {
                "request_json": (
                    b'{"model":"ref","input":[{"role":"user","content":[{"type":"input_image"}]}]}'
                )
            },
            "unsupported_input",
        ),
        (
            {"response_json": json.dumps(response_json(status="incomplete")).encode()},
            "incomplete_output",
        ),
    ],
)
def test_backend_recomputes_eligibility(changes, reason):
    payload = event(**changes).to_dict()
    payload["replay_eligible"] = True
    if reason not in {"request_too_large"}:
        payload["ineligible_reason"] = None
    result = validate_capture(json.dumps(payload).encode())
    assert not result["replay_eligible"]
    assert result["ineligible_reason"] == reason


@pytest.mark.parametrize(
    "raw",
    [
        b"x" * (MAX_ENVELOPE_BYTES + 1),
        b"[]",
        b"{}",
        b'{"a":' + b"[" * 2000 + b"0" + b"]" * 2000 + b"}",
        b'{"a":' + b"[" * 40 + b"0" + b"]" * 40 + b"}",
        b'{"a":[' + b"0," * 9000 + b"0]}",
    ],
)
def test_backend_rejects_oversized_invalid_and_complex_payloads(raw):
    with pytest.raises(ValueError):
        validate_capture(raw)


def test_worker_export_to_backend_inbox_round_trip(tmp_path, monkeypatch):
    from openai import OpenAI

    from gauge import CaptureBuffer, wrap

    inbox = CaptureInbox(str(tmp_path / "received.sqlite"))
    seen = []

    def receive(request):
        # Simulates the backend HTTP boundary; SQLite connection belongs to this worker.
        worker_inbox = CaptureInbox(str(tmp_path / "received.sqlite"))
        try:
            call_id, inserted = worker_inbox.accept(request.content)
            assert call_id == request.headers["idempotency-key"]
            assert inserted
            seen.append(call_id)
        finally:
            worker_inbox.close()
        return httpx.Response(202)

    exporter = HTTPExporter(
        "https://gauge.invalid/v1/captures", transport=httpx.MockTransport(receive)
    )
    monkeypatch.setattr(HTTPExporter, "from_env", classmethod(lambda cls: exporter))
    buffer = CaptureBuffer()
    try:
        with OpenAI(
            api_key="provider-secret",
            http_client=httpx.Client(
                transport=httpx.MockTransport(
                    lambda request: httpx.Response(200, json=response_json()),
                )
            ),
        ) as client:
            wrap(client, buffer=buffer)
            result = client.responses.create(
                model="ref",
                input="soap",
                extra_headers={"secret-header": "do-not-capture"},
            )
            assert result.output_text == "household"
            assert buffer.flush()
        assert buffer.exported == 1
        record = inbox.get(seen[0])
        assert record["reference_source"] == "original_response"
        assert record["response"]["id"] == result.id
        assert record["request"] == {"model": "ref", "input": "soap"}
        assert "provider-secret" not in json.dumps(record)
        assert "do-not-capture" not in json.dumps(record)
    finally:
        buffer.close()
        inbox.close()
