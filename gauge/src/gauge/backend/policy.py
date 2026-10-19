"""Project comparison configuration and bounded dispatch of already captured requests."""

from __future__ import annotations

import hashlib
import json
import secrets
import sqlite3
import time
from pathlib import Path

from gauge.backend.replay import ReplayUnavailable, materialize
from gauge.ingestion import CaptureConflict


def configure(backend, config: dict) -> dict:
    from gauge.local.state import write_json

    models = config.get("models")
    budget = config.get("budget")
    tokens = config.get("max_output_tokens", 2048)
    rate = config.get("sample_rate", 1.0)
    automatic = config.get("automatic", False)
    if (
        not isinstance(models, list)
        or not 1 <= len(models) <= 3
        or any(not isinstance(m, str) or not 1 <= len(m) <= 256 for m in models)
        or len(set(models)) != len(models)
        or type(budget) is not int
        or not 1 <= budget <= 3000
        or type(tokens) is not int
        or not 1 <= tokens <= 2048
        or type(rate) not in {int, float}
        or not 0 <= rate <= 1
        or type(automatic) is not bool
    ):
        raise ValueError("Invalid comparison configuration")
    with backend.lock:
        if backend.jobs.unfinished_tasks or backend.judging.inflight:
            raise CaptureConflict("Wait for pending evaluations before changing configuration")
        policy = {
            "sample_rate": rate,
            "automatic": automatic,
            "models": models,
            "budget": budget,
            "max_output_tokens": tokens,
            "configuration_id": "config_" + secrets.token_hex(16),
        }
        write_json(Path(backend.db).parent / "settings.json", policy)
        backend.policy = policy
        backend.models, backend.max_calls, backend.output_tokens = models, budget, tokens
        return policy


def dispatch(backend, config: dict) -> dict:
    from gauge.config import DEFAULT_FIREWORKS_MODELS

    if not backend.comparisons_enabled:
        raise CaptureConflict(
            "Set FIREWORKS_API_KEY in the backend environment, then restart the backend"
        )
    limit = config.get("limit", 100)
    run_id = config.get("run_id")
    if type(limit) is not int or not 1 <= limit <= 1000:
        raise ValueError("Comparison limit must be 1–1000 captured requests")
    if run_id is not None and not any(r["id"] == run_id for r in backend.dashboard.runs()):
        raise ValueError("Run not found in selected project")
    path = Path(backend.db).parent / "settings.json"
    if not getattr(backend, "policy", None):
        if path.exists():
            backend.policy = json.loads(path.read_text())
            backend.models = backend.policy["models"]
            backend.max_calls = backend.policy["budget"]
            backend.output_tokens = backend.policy["max_output_tokens"]
        else:
            configure(
                backend,
                {
                    "models": list(DEFAULT_FIREWORKS_MODELS),
                    "budget": 300,
                    "max_output_tokens": 2048,
                },
            )
    accepted, seen, cursor = 0, 0, None
    while seen < limit:
        page = backend.dashboard.page(run_id, cursor)
        for record in page["records"][: limit - seen]:
            seen += 1
            event = record["capture"]
            fraction = int(hashlib.sha256(event["call_id"].encode()).hexdigest()[:8], 16) / 2**32
            if fraction >= backend.policy.get("sample_rate", 1.0):
                continue
            try:
                messages = materialize(event, backend.dashboard.response_capture)
            except ReplayUnavailable:
                continue
            with backend.lock:
                for model in backend.models:
                    with sqlite3.connect(backend.db, timeout=5) as db:
                        existing = db.execute(
                            "SELECT payload FROM gauge_comparisons WHERE call_id=? AND candidate=?",
                            (event["call_id"], model),
                        ).fetchone()
                        used = db.execute(
                            "SELECT count(*) FROM gauge_comparisons "
                            "WHERE json_extract(payload, '$.configuration_id')=?",
                            (backend.policy["configuration_id"],),
                        ).fetchone()[0]
                    if existing or used >= backend.max_calls or backend.jobs.full():
                        continue
                    job = {
                        "id": "comparison_" + secrets.token_hex(16),
                        "attempt_id": "attempt_" + secrets.token_hex(16),
                        "configuration_id": backend.policy["configuration_id"],
                        "call_id": event["call_id"],
                        "candidate": model,
                        "status": "pending",
                        "max_output_tokens": backend.output_tokens,
                        "received_at": time.time(),
                    }
                    backend.dashboard.put(job)
                    backend.jobs.put_nowait({"event": event, "job": job, "messages": messages})
                    backend.stats["comparison_jobs"] += 1
                    accepted += 1
        cursor = page["next_cursor"]
        if cursor is None:
            break
    return {
        "accepted": accepted,
        "captures_considered": seen,
        "configuration": backend.policy,
        "run_id": run_id,
    }
