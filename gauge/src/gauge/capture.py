"""Disposable wire capture. No comparisons, user serializers, or export waits."""

from __future__ import annotations

import asyncio
import functools
import inspect
import os
import threading
import time
import weakref
from collections import deque
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar, Token
from dataclasses import dataclass
from itertools import islice
from typing import Any, TypeVar
from uuid import uuid4

from gauge.wire import MAX_BODY_BYTES, CaptureEvent

_SUPPRESSED: ContextVar[bool] = ContextVar("gauge_suppressed", default=False)
_ACTIVE: ContextVar[_Call | None] = ContextVar("gauge_call", default=None)
_CLIENT_MARKER = "_gauge_capture_binding"
_BUFFERS: weakref.WeakSet[CaptureBuffer] = weakref.WeakSet()
ClientT = TypeVar("ClientT")


@contextmanager
def suppress_capture() -> Iterator[None]:
    token = _SUPPRESSED.set(True)
    try:
        yield
    finally:
        _SUPPRESSED.reset(token)


class CaptureBuffer:
    """Bounded queue/history. A producer never waits to acquire either lock.

    Limits apply separately to pending events and local history. One additional
    event may be in flight. Decode/export only run on the daemon worker.
    """

    def __init__(
        self,
        capacity: int = 100,
        max_payload_bytes: int = MAX_BODY_BYTES,
        max_buffer_bytes: int = 4 * 1024 * 1024,
        *,
        exporter: Any = None,
    ) -> None:
        if (
            any(
                type(n) is not int or n < 1
                for n in (
                    capacity,
                    max_payload_bytes,
                    max_buffer_bytes,
                )
            )
            or max_payload_bytes > MAX_BODY_BYTES
        ):
            raise ValueError("Positive integer limits required; payload limit must be <= 65536")
        self.capacity = capacity
        self.max_payload_bytes = max_payload_bytes
        self.max_buffer_bytes = max_buffer_bytes
        self.dropped = 0
        self.failed = 0
        self.exported = 0
        self.export_dropped = 0
        self._exporter = exporter
        self._closed = False
        self._reset()
        _BUFFERS.add(self)

    def _reset(self) -> None:
        # Also used after fork: never acquire locks inherited from vanished threads.
        self._pid = os.getpid()
        self._pending: deque[CaptureEvent] = deque()
        self._events: deque[CaptureEvent] = deque()
        self._pending_bytes = 0
        self._event_bytes = 0
        self._lock = threading.Lock()
        self._history_lock = threading.Lock()
        self._start_lock = threading.Lock()
        self._worker: threading.Thread | None = None
        self._accepting = False
        self._submitted = 0
        self._finished = 0

    def start(self) -> None:
        """Only client setup starts/restarts workers; never an inference call."""
        with self._start_lock:
            if not self._closed and (self._worker is None or not self._worker.is_alive()):
                self._worker = threading.Thread(
                    target=self._consume, name="gauge-export", daemon=True
                )
                self._accepting = True
                try:
                    self._worker.start()
                except Exception:
                    self._accepting = False
                    raise

    def submit(self, event: CaptureEvent) -> bool:
        if (
            self._pid != os.getpid()
            or self._closed
            or not self._accepting
            or type(event) is not CaptureEvent
        ):
            self.dropped += 1
            return False
        size = event.size_bytes
        if not self._lock.acquire(blocking=False):
            self.dropped += 1
            return False
        try:
            if (
                len(self._pending) >= self.capacity
                or self._pending_bytes + size > self.max_buffer_bytes
            ):
                self.dropped += 1
                return False
            self._pending.append(event)
            self._pending_bytes += size
            self._submitted += 1
            return True
        finally:
            self._lock.release()

    def _consume(self) -> None:
        exporter = self._exporter
        try:
            if exporter is None:
                from gauge.export import HTTPExporter

                exporter = HTTPExporter.from_env()
            while True:
                with self._lock:
                    event = self._pending.popleft() if self._pending else None
                    if event is not None:
                        self._pending_bytes -= event.size_bytes
                if event is None:
                    if self._closed:
                        return
                    time.sleep(0.01)
                    continue
                try:
                    # Validate bounded JSON off the serving thread, including depth/node limits.
                    payload = event.to_dict()
                    self.append(event)
                    if exporter is not None:
                        if exporter.send(payload):
                            self.exported += 1
                        else:
                            self.export_dropped += 1
                except Exception:
                    self.failed += 1
                finally:
                    self._finished += 1
        except Exception:
            self.failed += 1
        finally:
            self._accepting = False
            if exporter is not None:
                try:
                    exporter.close()
                except Exception:
                    pass

    def flush(self, timeout: float = 5) -> bool:
        """Optional diagnostics only. Application calls never flush or join."""
        deadline = time.monotonic() + max(0, timeout)
        target = self._submitted
        while self._finished < target:
            if time.monotonic() >= deadline or not self._accepting:
                return False
            time.sleep(min(0.005, max(0, deadline - time.monotonic())))
        return True

    def close(self) -> None:
        """Best effort only. Never join, flush, or delay application shutdown."""
        self._closed = True

    def append(self, event: CaptureEvent) -> None:
        with self._history_lock:
            size = event.size_bytes
            if size > self.max_buffer_bytes:
                self.dropped += 1
                return
            while self._events and (
                len(self._events) >= self.capacity
                or self._event_bytes + size > self.max_buffer_bytes
            ):
                self._event_bytes -= self._events.popleft().size_bytes
                self.dropped += 1
            self._events.append(event)
            self._event_bytes += size

    def drain(self, *, timeout: float = 5) -> list[CaptureEvent]:
        self.flush(timeout)
        with self._history_lock:
            events = list(self._events)
            self._events.clear()
            self._event_bytes = 0
            return events


def _after_fork() -> None:
    _ACTIVE.set(None)
    _SUPPRESSED.set(False)
    for buffer in list(_BUFFERS):
        buffer._reset()
        # An inherited exporter can contain stale socket/lock state. Recreate from env.
        buffer._exporter = None
    # Inherited clients drop captures; constructing/wrapping a client restarts capture.


if hasattr(os, "register_at_fork"):
    os.register_at_fork(after_in_child=_after_fork)

captures = CaptureBuffer()


@dataclass
class _Binding:
    buffer: CaptureBuffer
    enabled: bool = False


@dataclass
class _Call:
    binding: _Binding
    id: str
    started: float
    created_at: float
    operation: str = ""
    request: bytes | None = None
    response: bytes | None = None
    reason: str | None = None
    stream: bool = False
    endpoint_host: str | None = None
    provider_request_id: str | None = None
    attempts: int = 0


def _begin(binding: _Binding) -> tuple[_Call, Token[_Call | None]] | None:
    try:
        if not binding.enabled or _SUPPRESSED.get() or os.environ.get("GAUGE_ENABLED", "1") == "0":
            return None
        call = _Call(binding, f"call_{uuid4().hex}", time.perf_counter(), time.time())
        return call, _ACTIVE.set(call)
    except Exception:
        return None


def _finish(state: tuple[_Call, Token[_Call | None]] | None, error: BaseException | None) -> None:
    if state is None:
        return
    call, token = state
    try:
        if call.operation:
            status = (
                "cancelled"
                if isinstance(error, asyncio.CancelledError)
                else "error"
                if error is not None
                else "stream_passthrough"
                if call.stream
                else "completed"
            )
            call.binding.buffer.submit(
                CaptureEvent(
                    id=call.id,
                    operation=call.operation,
                    request_json=call.request,
                    response_json=call.response if error is None else None,
                    status=status,
                    latency_ms=(time.perf_counter() - call.started) * 1000,
                    created_at=call.created_at,
                    error=type(error).__name__[:128] if error else None,
                    omission_reason=call.reason,
                    provider_request_id=call.provider_request_id,
                    endpoint_host=call.endpoint_host,
                    attempts=call.attempts,
                )
            )
    except Exception:
        pass
    finally:
        try:
            _ACTIVE.reset(token)
        except Exception:
            pass


def _request_hook(method: Any, binding: _Binding) -> Any:
    if inspect.iscoroutinefunction(method):

        @functools.wraps(method)
        async def async_request(*args: Any, **kwargs: Any) -> Any:
            state = _begin(binding)
            try:
                result = await method(*args, **kwargs)
            except BaseException as exc:
                _finish(state, exc)
                raise
            _finish(state, None)
            return result

        return async_request

    @functools.wraps(method)
    def request(*args: Any, **kwargs: Any) -> Any:
        state = _begin(binding)
        try:
            result = method(*args, **kwargs)
        except BaseException as exc:
            _finish(state, exc)
            raise
        _finish(state, None)
        return result

    return request


def _observe_request(call: _Call, request: Any) -> None:
    # Built-in HTTPX wire objects only; never call properties on application subclasses.
    if type(request) not in _wire_types()[0]:
        return
    raw_path = request.url.raw_path
    if type(raw_path) is not bytes or len(raw_path) > 4096:
        return
    path = request.url.path
    operation = (
        "chat.completions.create"
        if path.endswith("/chat/completions")
        else "responses.create"
        if path.endswith("/responses")
        else ""
    )
    if request.method != "POST" or not operation:
        return
    call.operation = operation
    call.endpoint_host = request.url.host[:253]
    body = vars(request).get("_content")
    if type(body) is not bytes:
        call.request, call.reason = None, "unbuffered_request"
    elif len(body) > call.binding.buffer.max_payload_bytes:
        call.request, call.reason = None, "request_too_large"
    elif _header(request.headers, b"content-type").split(";")[0] != "application/json":
        call.request, call.reason = None, "unsupported_content_type"
    else:
        call.request, call.reason = body, None
    call.stream = _header(request.headers, b"x-stainless-raw-response") == "stream"


@functools.lru_cache(maxsize=1)
def _wire_types() -> tuple[tuple[type, ...], tuple[type, ...], tuple[type, ...]]:
    import httpx

    requests: list[type] = [httpx.Request]
    responses: list[type] = [httpx.Response]
    headers: list[type] = [httpx.Headers]
    try:
        import httpx2
    except ImportError:
        pass
    else:
        requests.append(httpx2.Request)
        responses.append(httpx2.Response)
        headers.append(httpx2.Headers)
    return tuple(requests), tuple(responses), tuple(headers)


def _header(headers: Any, name: bytes) -> str:
    # Bound lookup work too: never join arbitrarily many/large duplicate headers.
    if type(headers) not in _wire_types()[2]:
        return ""
    items = vars(headers).get("_list")
    if type(items) is not list:
        return ""
    for item in islice(items, 128):
        if type(item) is tuple and len(item) == 3 and type(item[1]) is bytes:
            if item[1] == name and type(item[2]) is bytes:
                return item[2][:256].decode("ascii", errors="replace")
    return ""


def _build_hook(method: Any, binding: _Binding) -> Any:
    @functools.wraps(method)
    def build(*args: Any, **kwargs: Any) -> Any:
        request = method(*args, **kwargs)
        try:
            call = _ACTIVE.get()
            if binding.enabled and call is not None and call.binding is binding:
                call.attempts += 1
                _observe_request(call, request)
        except Exception:
            pass
        return request

    return build


def _response_hook(method: Any, binding: _Binding) -> Any:
    def observe(kwargs: dict[str, Any]) -> None:
        try:
            call = _ACTIVE.get()
            response: Any = kwargs.get("response")
            if not binding.enabled or call is None or call.binding is not binding:
                return
            if type(response) not in _wire_types()[1]:
                call.reason = "unsupported_response_type"
                return
            _observe_request(call, response.request)
            call.stream = call.stream or kwargs.get("stream") is True
            request_id = _header(response.headers, b"x-request-id")
            call.provider_request_id = request_id[:256] if request_id else None
            if call.stream:
                call.reason = call.reason or "stream_unconsumed"
                return
            body = vars(response).get("_content")
            if type(body) is not bytes:
                call.reason = call.reason or "unbuffered_response"
            elif len(body) > binding.buffer.max_payload_bytes:
                call.reason = call.reason or "response_too_large"
            elif _header(response.headers, b"content-type").split(";")[0] != "application/json":
                call.reason = call.reason or "unsupported_content_type"
            elif call.reason is None:
                call.response = body
        except Exception:
            pass

    if inspect.iscoroutinefunction(method):

        @functools.wraps(method)
        async def async_process(*args: Any, **kwargs: Any) -> Any:
            observe(kwargs)
            return await method(*args, **kwargs)

        return async_process

    @functools.wraps(method)
    def process(*args: Any, **kwargs: Any) -> Any:
        observe(kwargs)
        return method(*args, **kwargs)

    return process


def _clone_hook(method: Any, binding: _Binding) -> Any:
    @functools.wraps(method)
    def clone(*args: Any, **kwargs: Any) -> Any:
        with suppress_capture():
            result = method(*args, **kwargs)
        if binding.enabled:
            try:
                wrap(result, buffer=binding.buffer)
            except Exception:
                pass
        return result

    return clone


def wrap(client: ClientT, *, buffer: CaptureBuffer | None = None) -> ClientT:
    """Attach optional wire capture transactionally; return the same native client."""
    from openai import AsyncOpenAI, OpenAI

    if not isinstance(client, (OpenAI, AsyncOpenAI)):
        raise TypeError("gauge.wrap supports native OpenAI and AsyncOpenAI clients")
    chosen = captures if buffer is None else buffer
    binding = _Binding(chosen)
    previous: dict[str, tuple[bool, Any]] = {}
    try:
        if os.environ.get("GAUGE_ENABLED", "1") == "0":
            return client
        existing = getattr(client, _CLIENT_MARKER, None)
        if isinstance(existing, _Binding):
            existing.buffer.start()
            return client
        # Private SDK integration is deliberately limited to the tested minor release.
        from openai import __version__

        if __version__.split(".")[:2] != ["3", "29"]:
            chosen.failed += 1
            return client
        _wire_types()  # Imports/cache initialization only during setup.
        replacements = {
            "request": _request_hook(client.request, binding),
            "_build_request": _build_hook(client._build_request, binding),
            "_process_response": _response_hook(client._process_response, binding),
            "copy": _clone_hook(client.copy, binding),
            "with_options": _clone_hook(client.with_options, binding),
            _CLIENT_MARKER: binding,
        }
        for name, replacement in replacements.items():
            previous[name] = (name in vars(client), vars(client).get(name))
            setattr(client, name, replacement)
        chosen.start()
        binding.enabled = True
    except Exception:
        binding.enabled = False
        chosen.failed += 1
        for name, (present, value) in previous.items():
            # Restore instance attributes directly even if an application setter failed.
            if present:
                vars(client)[name] = value
            else:
                vars(client).pop(name, None)
    return client
