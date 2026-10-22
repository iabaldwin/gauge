"""Project discovery, original request inspection, and bounded evaluation commands."""

from __future__ import annotations

import json
import os
import signal
from pathlib import Path

from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from gauge.cli.client import ConsoleClient, ConsoleError
from gauge.cli.views import BLUE, plain, request_title, response_text
from gauge.local.state import bootstrap


def request_table(records: list[dict]) -> Table:
    table = Table(title="CAPTURED REQUESTS", header_style=BLUE, expand=True)
    for col in ("Request ID", "Input", "Original output", "Comparisons"):
        table.add_column(col)
    for record in records:
        e = record["capture"]
        table.add_row(
            Text(e["call_id"]),
            Text(request_title(e)[:100]),
            Text(plain(response_text(e.get("response")))[:150]),
            str(len(record["comparisons"])),
        )
    return table


def results_command(args, client: ConsoleClient, console) -> None:
    runs = client.request("GET", "/v1/runs")["runs"]
    run_id = getattr(args, "run_id", None) or (runs[0]["id"] if runs else None)
    if args.command == "compare":
        if not run_id:
            raise ConsoleError("No captured run. Run your tests with the Gauge client first.")
        if not 1 <= args.limit <= 1000:
            raise ConsoleError("Comparison capture limit must be 1–1000")
        result = client.request("POST", "/v1/comparisons", {"run_id": run_id, "limit": args.limit})
    else:
        records = client.records(run_id) if run_id else []
        if args.command == "inspect":
            # Search across runs; a call ID must resolve uniquely.
            records = client.records(None)
            found = [r for r in records if r["capture"]["call_id"].startswith(args.call_id)]
            if len(found) != 1:
                raise ConsoleError("Request ID must match exactly one captured request")
            result = found[0]
            if not args.json:
                console.print(
                    Panel(
                        Text(plain(json.dumps(result["capture"]["request"], indent=2))),
                        title="COMPLETE REQUEST CONTEXT",
                        border_style=BLUE,
                    )
                )
                console.print(
                    Panel(
                        Text(plain(response_text(result["capture"].get("response")))),
                        title="ORIGINAL OUTPUT",
                        border_style=BLUE,
                    )
                )
                for job in result["comparisons"]:
                    console.print(
                        Panel(
                            Text(
                                plain(
                                    job.get(
                                        "candidate_output",
                                        job.get("error", job.get("reason", "Waiting")),
                                    )
                                )
                            ),
                            title=plain(job["candidate"]),
                            border_style=BLUE,
                        )
                    )
                return
        else:
            result = {"run_id": run_id, "records": records}
            if not args.json:
                console.print(request_table(records))
                if not records:
                    console.print("Run your tests with the Gauge client to capture requests.")
                return
    if args.json:
        print(json.dumps(result))
    else:
        console.print_json(json.dumps(result))


def setup_command(args, console) -> None:
    project = bootstrap()
    folder = Path(project["root"]) / ".gauge"
    if args.command == "connect":
        from gauge.local.remote import connect

        try:
            result = connect(args.url, args.project_id)
        except (ValueError, OSError) as exc:
            raise ConsoleError(str(exc)) from exc
    elif args.command == "project":
        result = project
    elif args.command == "configure":
        from gauge.config import DEFAULT_FIREWORKS_MODELS

        if not 1 <= args.budget <= 3000 or not 1 <= args.max_output_tokens <= 2048:
            raise ConsoleError("Budget must be 1–3000; output tokens must be 1–2048")
        models = args.models or list(DEFAULT_FIREWORKS_MODELS)
        if not 1 <= len(models) <= 3 or len(set(models)) != len(models):
            raise ConsoleError("Choose one to three distinct candidate models")
        result = {
            "models": models,
            "automatic": args.automatic,
            "sample_rate": args.sample_rate,
            "budget": args.budget,
            "max_output_tokens": args.max_output_tokens,
        }
        client = ConsoleClient(args.connection)
        try:
            client.request("POST", "/v1/config", result)
        finally:
            client.close()
    else:
        from gauge.local.runtime import ensure_service

        if args.operation == "stop":
            state = folder / "service.json"
            if not state.exists():
                result = {"running": False}
            else:
                client = ConsoleClient(state)
                try:
                    stats = client.snapshot()["stats"]
                    if (
                        stats["active"]
                        or stats["queued"]
                        or stats["judging"]["active"]
                        or stats["judging"]["queued"]
                        or stats.get("reference_judging", {}).get("pending")
                    ):
                        raise ConsoleError(
                            "Wait for comparison and judge queues to drain before stopping"
                        )
                    config = json.loads(state.read_text())
                    if stats["pid"] != config["pid"]:
                        raise ConsoleError("Service identity changed")
                    os.kill(config["pid"], signal.SIGTERM)
                    state.unlink()
                    result = {"running": False, "saved_results": project["database"]}
                finally:
                    client.close()
        else:
            result = ensure_service()
    if args.json:
        print(json.dumps(result))
    else:
        console.print_json(json.dumps(result))
