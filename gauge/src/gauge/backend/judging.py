"""Bounded, disposable judge queue independent of comparison worker capacity."""

from __future__ import annotations

import os
import queue
import threading
import time
from typing import Any

from gauge.backend.judge import MODEL, RUBRIC, JevJudge
from gauge.backend.replay import ReplayUnavailable, materialize, response_text
from gauge.backend.store import DashboardStore


class JudgeQueue:
    def __init__(self, store: DashboardStore, *, capacity: int = 256, budget: int = 3000):
        self.store, self.budget = store, budget
        self.enabled = (
            bool(os.environ.get("PORTKEY_API_KEY"))
            and os.environ.get("GAUGE_JUDGE", "jev") != "off"
        )
        self.jobs: queue.Queue = queue.Queue(capacity)
        self.lock = threading.Lock()
        self.inflight: set[tuple[str, str]] = set()
        self.stats = dict(queued=0, active=0, calls_started=0, completed=0, uncertain=0, errors=0)
        if self.enabled:
            for index in range(2):
                threading.Thread(target=self.consume, daemon=True, name=f"jev-{index}").start()

    def snapshot(self) -> dict:
        with self.lock:
            return {**self.stats, "enabled": self.enabled, "workers": 2 if self.enabled else 0}

    def submit(self, event: dict, result: dict, messages: list[dict] | None = None) -> bool:
        if result["status"] != "completed" or result.get("finish_reason") != "stop":
            return False
        key = (result["call_id"], result["candidate"])
        with self.lock:
            prior = result.get("judgment", {})
            if key in self.inflight or (
                prior.get("status") in {"completed", "uncertain"}
                and prior.get("rubric_version") == RUBRIC
            ):
                return False
            reason = None
            if not self.enabled:
                reason = "JEV disabled or PORTKEY_API_KEY missing"
            elif self.stats["calls_started"] + self.stats["queued"] >= self.budget:
                reason = "Judge call budget exhausted"
            elif self.jobs.full():
                reason = "Judge queue full"
            judgment = {
                "status": "skipped" if reason else "pending",
                "model": MODEL,
                "rubric_version": RUBRIC,
                "queued_at": time.time(),
            }
            if reason:
                judgment["reason"] = reason
            if not self.store.update_judgment(*key, judgment, only_unjudged=True):
                return False
            if reason:
                return False
            self.inflight.add(key)
            self.jobs.put_nowait((event, result, messages))
            self.stats["queued"] += 1
            return True

    def consume(self) -> None:
        # Construct clients lazily within protected work: a bad proxy/config cannot kill the worker.
        judge = None
        while True:
            event, result, messages = self.jobs.get()
            key = (result["call_id"], result["candidate"])
            started = time.time()
            judgment: dict[str, Any] = {
                "status": "running",
                "model": MODEL,
                "rubric_version": RUBRIC,
                "started_at": started,
            }
            with self.lock:
                self.stats["queued"] -= 1
                self.stats["active"] += 1
                self.stats["calls_started"] += 1
            try:
                self.store.update_judgment(*key, judgment)
                if messages is None:
                    messages = materialize(event, self.store.response_capture)
                original = response_text(event["response"])
                candidate = result.get("candidate_output")
                if not isinstance(original, str) or not isinstance(candidate, str):
                    raise ValueError("Judge needs complete text outputs")
                judge = judge or JevJudge()
                judgment.update(judge.grade(messages, original, candidate))
            except ReplayUnavailable as exc:
                judgment.update(status="skipped", reason=str(exc))
            except Exception as exc:
                # Never persist exception text: upstream bodies/URLs can contain credentials.
                judgment.update(status="error", error=type(exc).__name__)
            judgment["finished_at"] = time.time()
            try:
                self.store.update_judgment(*key, judgment)
            except Exception:
                judgment["status"] = "error"
            finally:
                with self.lock:
                    self.inflight.discard(key)
                    self.stats["active"] -= 1
                    status = judgment["status"]
                    counter = status if status in {"completed", "uncertain"} else "errors"
                    self.stats[counter] += 1
                self.jobs.task_done()
