"""Automatic pytest execution grouping and bounded capture delivery at test teardown."""

from __future__ import annotations

import os
from uuid import uuid4

import pytest


@pytest.hookimpl(tryfirst=True)
def pytest_configure(config):
    worker = getattr(config, "workerinput", {})
    config._gauge_previous = {
        name: os.environ.get(name)
        for name in ("GAUGE_RUN_ID", "GAUGE_RUN_NAME", "GAUGE_PROJECT_DIR")
    }
    config._gauge_run = worker.get("gauge_run_id") or "run_" + uuid4().hex
    os.environ.update(
        GAUGE_RUN_ID=config._gauge_run,
        GAUGE_RUN_NAME=f"pytest · {config.rootpath.name}",
        GAUGE_PROJECT_DIR=os.environ.get("GAUGE_PROJECT_DIR") or str(config.rootpath),
    )


@pytest.hookimpl(optionalhook=True)
def pytest_configure_node(node):
    node.workerinput["gauge_run_id"] = node.config._gauge_run


@pytest.hookimpl(trylast=True)
def pytest_sessionfinish(session, exitstatus):
    try:
        from gauge.capture import _BUFFERS
        from gauge.local.state import project_root
        from gauge.local.storage import finish_run

        if os.environ.get("GAUGE_STORAGE") == "memory":
            return
        import time

        deadline = time.monotonic() + 5
        buffers = [b for b in list(_BUFFERS) if b._pid == os.getpid()]
        flushed = all([b.flush(timeout=max(0, deadline - time.monotonic())) for b in buffers])
        metrics = {
            name: sum(getattr(b, name) for b in buffers)
            for name in ("exported", "export_dropped", "queue_dropped", "failed")
        }
        if any(metrics.values()) or any(b._submitted for b in buffers):
            from gauge.execution import identity
            from gauge.local.state import write_json

            execution = identity()
            folder = project_root() / ".gauge/diagnostics"
            folder.mkdir(parents=True, exist_ok=True)
            write_json(
                folder / (execution["run_id"] + "_" + execution["process_id"] + ".json"),
                {**execution, **metrics, "flushed": flushed},
            )
            if (
                not flushed
                or metrics["export_dropped"]
                or metrics["failed"]
                or metrics["queue_dropped"]
            ):
                reporter = session.config.pluginmanager.get_plugin("terminalreporter")
                if reporter:
                    reporter.write_line(
                        "Gauge: capture loss recorded; inspect gauge status for details."
                    )
        if not hasattr(session.config, "workerinput"):
            finish_run(project_root(), session.config._gauge_run, int(exitstatus))
    except Exception:
        # Diagnostics cannot change the application's/test suite's result.
        pass


def pytest_unconfigure(config):
    for name, value in getattr(config, "_gauge_previous", {}).items():
        if value is None:
            os.environ.pop(name, None)
        else:
            os.environ[name] = value
