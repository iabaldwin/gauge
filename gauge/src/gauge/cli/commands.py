"""Terminal command routing; rendering dependencies load only when invoked."""

from __future__ import annotations

import json
import sys
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from gauge.cli.client import ConsoleClient, ConsoleError, export_run
from gauge.cli.views import BLUE, colored, model_table, plain, status_view


def configure(commands) -> None:
    for name, help_text in (
        ("tui", "Open the live Pixel Lab terminal console"),
        ("status", "Show backend queues and judge status"),
        ("runs", "List, inspect, export, or clear saved runs"),
        ("judge", "Judge up to 100 saved comparison pairs"),
        ("dashboard", "Open the selected saved run in the browser"),
        ("requests", "List captured requests, including original-only results"),
        ("inspect", "Inspect complete request context and outputs"),
        ("compare", "Dispatch bounded comparisons for saved captures"),
        ("project", "Show the automatically discovered project"),
        ("connect", "Connect this project to an internal shared backend"),
        ("configure", "Set project comparison models and budgets"),
        ("backend", "Start, inspect, or stop the local service"),
        ("pause", "Pause backend comparison dispatch"),
        ("resume", "Resume backend comparison dispatch"),
    ):
        parser = commands.add_parser(name, help=help_text)
        parser.add_argument("--connection", type=Path, default=None)
        if name == "tui":
            colors = parser.add_mutually_exclusive_group()
            colors.add_argument("--color", dest="color", action="store_true", help="Enable colors")
            colors.add_argument("--no-color", dest="color", action="store_false", help="Monochrome")
            parser.set_defaults(color=None)
        if name in {
            "status",
            "runs",
            "judge",
            "pause",
            "resume",
            "requests",
            "inspect",
            "compare",
            "project",
            "configure",
            "backend",
        }:
            parser.add_argument("--json", action="store_true", help="Machine-readable output")
        if name == "connect":
            parser.add_argument("url")
            parser.add_argument("--project-id")
        if name == "dashboard":
            parser.add_argument("run_id", nargs="?")
            parser.add_argument("--print-only", action="store_true")
        if name in {"requests", "compare"}:
            parser.add_argument("run_id", nargs="?")
        if name == "compare":
            parser.add_argument("--limit", type=int, default=100)
        if name == "inspect":
            parser.add_argument("call_id")
        if name == "configure":
            parser.add_argument("--models", nargs="+")
            parser.add_argument("--automatic", action="store_true")
            parser.add_argument("--sample-rate", type=float, default=1.0)
            parser.add_argument("--budget", type=int, default=300)
            parser.add_argument("--max-output-tokens", type=int, default=2048)
        if name == "backend":
            parser.add_argument("operation", choices=["start", "status", "stop"])
        if name == "runs":
            parser.add_argument("operation", choices=["list", "show", "export", "clear"])
            parser.add_argument("run_id", nargs="?")
            parser.add_argument("--all", action="store_true", help="Select all saved runs")
            parser.add_argument("--yes", action="store_true", help="Confirm the displayed cleanup")
            parser.add_argument("--dry-run", action="store_true", help="Preview cleanup only")
            parser.add_argument("--output", type=Path)
        if name == "judge":
            parser.add_argument("run_id")
            parser.add_argument(
                "--limit", type=int, default=30, choices=range(1, 101), metavar="1–100"
            )


def run(args) -> None:
    console = Console()
    if args.command in {"project", "configure", "backend", "connect"}:
        from gauge.cli.onboarding import setup_command

        setup_command(args, console)
        return
    client = ConsoleClient(args.connection)
    result: dict | None
    try:
        if args.command == "tui":
            from gauge.cli.tui import GaugeConsole

            if not sys.stdin.isatty() or not sys.stdout.isatty():
                raise ConsoleError(
                    "TUI needs an interactive terminal; use status or runs list --json"
                )
            GaugeConsole(client, color=args.color).run()
            return
        if args.command == "dashboard":
            url = client.dashboard(args.run_id)
            print(url)
            if not args.print_only:
                import webbrowser

                webbrowser.open(url)
            return
        if args.command in {"requests", "inspect", "compare"}:
            from gauge.cli.onboarding import results_command

            results_command(args, client, console)
            return
        if args.command == "status":
            result = client.snapshot()
            if args.json:
                print(json.dumps(result))
            else:
                console.print(
                    Panel(
                        status_view(result["stats"]), title="GAUGE / PIXEL LAB", border_style=BLUE
                    )
                )
            return
        if args.command in {"pause", "resume"}:
            result = client.request("POST", "/control", {"paused": args.command == "pause"})
        elif args.command == "judge":
            result = client.judge(args.run_id, args.limit)
        else:
            result = run_operation(args, client, console)
        if result is not None:
            if args.json:
                print(json.dumps(result))
            elif args.command != "runs" or args.operation in {"clear", "export"}:
                console.print_json(json.dumps(result))
    finally:
        client.close()


def run_operation(args, client: ConsoleClient, console: Console) -> dict | None:
    if args.operation == "clear":
        if bool(args.run_id) == bool(args.all):
            raise ConsoleError("Select one run ID or --all")
        preview = client.preview(None if args.all else [args.run_id])
        if args.dry_run or not preview["run_ids"]:
            if not args.json:
                console.print(
                    Panel(
                        Text(
                            f"{len(preview['run_ids'])} runs · {preview['captures']} captures · "
                            f"{preview['comparisons']} comparisons · "
                            f"{preview['judgments']} judgments\n"
                            "Preview only. A database backup is saved before clearing.",
                        ),
                        title="CLEANUP PREVIEW",
                        border_style="#bc5a12",
                    )
                )
                return None
            return preview
        if not args.yes:
            if args.json or not sys.stdin.isatty():
                raise ConsoleError("Use --dry-run to preview, then --yes to clear these runs")
            console.print(
                Text(
                    f"Clear {len(preview['run_ids'])} runs, {preview['captures']} captures, "
                    f"and {preview['comparisons']} comparisons? A backup will be saved.",
                    style="bold #bc5a12",
                )
            )
            if console.input("Type CLEAR to proceed: ") != "CLEAR":
                console.print("Cancelled")
                return None
        return client.clear(preview)
    runs = client.request("GET", "/v1/runs")["runs"]
    if args.operation == "list":
        if args.json:
            return {"runs": runs}
        table = Table(title="GAUGE / SAVED RUNS", header_style=BLUE)
        for column in ("Run ID", "Name", "Status"):
            table.add_column(column)
        for row in runs:
            table.add_row(plain(row["id"]), plain(row["name"]), colored(row["status"]))
        console.print(table)
        return None
    if not args.run_id:
        raise ConsoleError("A run ID is required")
    if not any(r["id"] == args.run_id for r in runs):
        raise ConsoleError("Run not found")
    if args.operation == "export":
        output = args.output or Path("out") / f"{args.run_id}.json"
        return {"export": str(export_run(client, args.run_id, output))}
    records = client.records(args.run_id)
    if args.json:
        return {"run": next(r for r in runs if r["id"] == args.run_id), "records": records}
    console.print(model_table(records))
    from gauge.cli.onboarding import request_table

    console.print(request_table(records))
    return None
