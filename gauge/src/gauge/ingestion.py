"""Backend-only validation and durable idempotent inbox; never imported by capture.

An HTTP handler should enforce MAX_ENVELOPE_BYTES while reading, authenticate the
producer, call accept(), then acknowledge. Comparison workers read eligible rows
and correlate their results by call_id. No inference is performed at ingestion.
"""

from __future__ import annotations

import hashlib
import json
import math
import re
import sqlite3
from typing import Any

from gauge.wire import (
    MAX_BODY_BYTES,
    MAX_ENVELOPE_BYTES,
    SCHEMA_VERSION,
    decode_object,
    replay_reason,
)


class CaptureConflict(ValueError):
    """The same ID was reused with a different original request/result pair."""


def validate_capture(raw: bytes) -> dict[str, Any]:
    event = decode_object(raw, limit=MAX_ENVELOPE_BYTES)
    if event.get("schema_version") != SCHEMA_VERSION:
        raise ValueError("Unsupported capture schema")
    if not isinstance(event.get("call_id"), str) or not re.fullmatch(
        r"call_[0-9a-f]{32}", event["call_id"]
    ):
        raise ValueError("Invalid call ID")
    if event.get("operation") not in {"responses.create", "chat.completions.create"}:
        raise ValueError("Unsupported operation")
    if event.get("reference_source") != "original_response":
        raise ValueError("Reference must be the original response")
    if event.get("status") not in {"completed", "error", "cancelled", "stream_passthrough"}:
        raise ValueError("Invalid capture status")
    for key in ("created_at", "latency_ms"):
        value: Any = event.get(key)
        if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
            raise ValueError(f"Invalid {key}")
    if type(event.get("attempts")) is not int or event["attempts"] < 0:
        raise ValueError("Invalid attempt count")
    for key in ("error", "ineligible_reason", "provider_request_id", "endpoint_host"):
        value = event.get(key)
        if value is not None and (type(value) is not str or len(value) > 256):
            raise ValueError(f"Invalid {key}")
    request: Any = event.get("request")
    response: Any = event.get("response")
    for key, value in (("request", request), ("response", response)):
        if value is None and key == "response":
            continue
        if type(value) is not dict:
            raise ValueError(f"Invalid {key}")
        encoded = json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode()
        if len(encoded) > MAX_BODY_BYTES:
            raise ValueError(f"Oversized {key}")
    reason = event.get("ineligible_reason")
    if event["status"] != "completed" or response is None:
        reason = reason or "no_completed_response"
    elif "capture_omitted" in request:
        reason = reason or "incomplete_request"
    else:
        reason = reason or replay_reason(event["operation"], request, response)
    # Never trust producer eligibility; preserve conservative producer exclusions too.
    event["replay_eligible"] = reason is None
    event["ineligible_reason"] = reason
    return event


class CaptureInbox:
    """One connection per backend worker. SQLite uniqueness survives restarts.

    An existing service can implement the same contract in its own datastore.
    Duplicate delivery acknowledges the original; conflicting reuse is rejected.
    """

    def __init__(self, path: str) -> None:
        self._db = sqlite3.connect(path, timeout=1)
        self._db.execute("""
            CREATE TABLE IF NOT EXISTS gauge_captures (
                call_id TEXT PRIMARY KEY,
                digest TEXT NOT NULL,
                replay_eligible INTEGER NOT NULL,
                payload TEXT NOT NULL
            )
        """)
        self._db.commit()

    def accept(self, raw: bytes) -> tuple[str, bool]:
        event = validate_capture(raw)
        payload = json.dumps(event, sort_keys=True, separators=(",", ":"), allow_nan=False)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        with self._db:
            cursor = self._db.execute(
                "INSERT OR IGNORE INTO gauge_captures VALUES (?, ?, ?, ?)",
                (event["call_id"], digest, int(event["replay_eligible"]), payload),
            )
            if cursor.rowcount == 0:
                original = self._db.execute(
                    "SELECT digest FROM gauge_captures WHERE call_id = ?",
                    (event["call_id"],),
                ).fetchone()
                if original is None or original[0] != digest:
                    raise CaptureConflict("Call ID already contains a different capture")
            return event["call_id"], cursor.rowcount == 1

    def get(self, call_id: str) -> dict[str, Any] | None:
        row = self._db.execute(
            "SELECT payload FROM gauge_captures WHERE call_id = ?",
            (call_id,),
        ).fetchone()
        return json.loads(row[0]) if row else None

    def close(self) -> None:
        self._db.close()
