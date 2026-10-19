"""In-memory execution IDs. No filesystem, network, or test-runner imports."""

from __future__ import annotations

import os
from uuid import uuid4

_pid = os.getpid()
_process_id = "process_" + uuid4().hex
_fallback_run = "run_" + uuid4().hex


def identity() -> dict[str, str]:
    global _pid, _process_id, _fallback_run
    if _pid != os.getpid():
        _pid = os.getpid()
        _process_id = "process_" + uuid4().hex
        _fallback_run = "run_" + uuid4().hex
    return {
        "run_id": os.environ.get("GAUGE_RUN_ID") or _fallback_run,
        "process_id": _process_id,
    }
