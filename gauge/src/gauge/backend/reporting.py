"""Retain terminal outcomes during storage outages without repeating inference.

Only backend workers call finish. Each worker holds at most one outcome and waits
for its write to recover before taking another job, bounding retained results by
the number of comparison and judge workers. No application capture thread waits.
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from copy import deepcopy
from typing import Any

Key = tuple[str, str, str]


class TerminalWrites:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.pending: dict[Key, dict[str, Any]] = {}

    def snapshot(self) -> dict[Key, dict[str, Any]]:
        with self.lock:
            return deepcopy(self.pending)

    def finish(self, key: Key, payload: dict, write: Callable[[], Any]) -> None:
        failures = 0
        while True:
            try:
                write()
            except Exception as exc:
                failures += 1
                with self.lock:
                    self.pending[key] = {
                        "payload": deepcopy(payload),
                        "persistence": {
                            "status": "retrying",
                            "failures": failures,
                            "error": type(exc).__name__,
                        },
                    }
                # Only storage writes retry. Provider calls have already finished.
                time.sleep(min(0.25 * 2 ** min(failures - 1, 7), 30))
            else:
                with self.lock:
                    self.pending.pop(key, None)
                return
