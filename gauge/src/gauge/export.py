"""Bounded best-effort delivery to a separate Gauge backend, never inference."""

from __future__ import annotations

import json
import os
import time
from typing import Any
from urllib.parse import urlsplit

from gauge.wire import MAX_ENVELOPE_BYTES


class HTTPExporter:
    """Worker-owned transport, finite attempts, and a circuit breaker.

    An acknowledgement is only a delivery receipt. Evaluation is never awaited.
    HTTP timeouts bound individual network operations, not process scheduling.
    """

    def __init__(
        self,
        endpoint: str,
        *,
        timeout: float = 2,
        max_retries: int = 1,
        failure_threshold: int = 3,
        cooldown: float = 30,
        transport: Any = None,
    ) -> None:
        import math

        import httpx

        url = urlsplit(endpoint)
        if (
            url.scheme not in {"https", "http"}
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
            or (url.scheme == "http" and url.hostname not in {"localhost", "127.0.0.1", "::1"})
        ):
            raise ValueError("GAUGE_ENDPOINT must be HTTPS (HTTP allowed only on loopback)")
        if (
            not math.isfinite(timeout)
            or not 0 < timeout <= 10
            or type(max_retries) is not int
            or not 0 <= max_retries <= 2
            or type(failure_threshold) is not int
            or not 1 <= failure_threshold <= 10
            or not math.isfinite(cooldown)
            or not 1 <= cooldown <= 300
        ):
            raise ValueError("Invalid bounded export policy")
        self._identity: dict[str, str] = {}
        self.endpoint = endpoint
        self.max_retries = max_retries
        self.failure_threshold = failure_threshold
        self.cooldown = cooldown
        self._failures = 0
        self._open_until = 0.0
        self._client = httpx.Client(
            timeout=httpx.Timeout(timeout, pool=0.01),
            limits=httpx.Limits(max_connections=1, max_keepalive_connections=1),
            follow_redirects=False,
            trust_env=False,
            transport=transport,
        )

    @classmethod
    def from_env(cls) -> HTTPExporter | None:
        endpoint = os.environ.get("GAUGE_ENDPOINT", "")
        if not endpoint or os.environ.get("GAUGE_ENABLED", "1") == "0":
            return None
        exporter = cls(endpoint)
        if os.environ.get("GAUGE_STORAGE") != "memory":
            from gauge.local.state import bootstrap, installation_id

            exporter._identity = {
                "project_id": os.environ.get("GAUGE_PROJECT_ID") or bootstrap()["project_id"],
                "installation_id": installation_id(),
            }
        return exporter

    def send(self, payload: dict[str, Any]) -> bool:
        if time.monotonic() < self._open_until:
            return False
        # Input was bounded and decoded by the capture worker. No user objects here.
        payload = {**payload, **getattr(self, "_identity", {})}
        body = json.dumps(
            payload, ensure_ascii=False, allow_nan=False, separators=(",", ":")
        ).encode()
        if len(body) > MAX_ENVELOPE_BYTES:
            return False
        for attempt in range(self.max_retries + 1):
            retryable = True
            try:
                # Never read an acknowledgement body or follow a redirect.
                with self._client.stream(
                    "POST",
                    self.endpoint,
                    content=body,
                    headers={
                        "Content-Type": "application/json",
                        "Idempotency-Key": payload["call_id"],
                    },
                ) as response:
                    if 200 <= response.status_code < 300:
                        self._failures = 0
                        self._open_until = 0
                        return True
                    retryable = response.status_code in {408, 429, 500, 502, 503, 504}
            except Exception:
                pass
            if not retryable or attempt == self.max_retries:
                break
        self._failures += 1
        if self._failures >= self.failure_threshold:
            self._open_until = time.monotonic() + self.cooldown
        return False

    def close(self) -> None:
        self._client.close()
