"""Pixel Lab terminal workspace. All data and mutations use the backend API."""

from __future__ import annotations

import asyncio
import json
import os
import time
import webbrowser
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Footer, Input, Static

from gauge.cli.client import ConsoleClient, ConsoleError, export_run
from gauge.cli.dialogs import ClearDialog, InspectDialog
from gauge.cli.views import (
    colored,
    model_table,
    plain,
    request_title,
    response_text,
    status_view,
    verdict,
)


class GaugeConsole(App):
    """Live operator console; exiting never stops the backend or its jobs."""

    TITLE = "Gauge / Pixel Lab"
    CSS = """
    Screen { background: #dce3ef; color: #142449; }
    #brand { height: 3; padding: 1 2; background: #142449; color: #fff260; text-style: bold; }
    #queues { height: 5; padding: 1 2; background: #f8faff; border-bottom: solid #b6c3d9; }
    #body { height: 1fr; padding: 1; }
    #sidebar { width: 40; margin-right: 1; border: solid #b6c3d9; background: #ffffff; }
    #sidebar-title { height: 2; padding: 0 1; color: #0649ff; text-style: bold; }
    #runs { height: 1fr; }
    #workspace { width: 1fr; }
    #models { height: auto; max-height: 12; padding-bottom: 1; }
    #search { height: 3; margin-bottom: 1; border: solid #b6c3d9;
              background: #ffffff; color: #142449; }
    #pairs { height: 1fr; border: solid #b6c3d9; }
    #notice { height: 2; padding: 0 2; background: #fff260; color: #142449; }
    DataTable { background: #ffffff; color: #142449; }
    DataTable > .datatable--header { background: #e9eef8; color: #0649ff; text-style: bold; }
    DataTable > .datatable--cursor { background: #0649ff; color: #ffffff; }
    DataTable > .datatable--hover { background: #e9eef8; color: #142449; }
    Footer { background: #142449; color: #ffffff; }
    Footer > .footer-key--key { background: #fff260; color: #142449; }
    .compact #body { layout: vertical; }
    .compact #brand { height: 2; padding: 0 1; }
    .compact #sidebar { width: 1fr; height: 5; margin: 0 0 1 0; }
    .compact #sidebar-title { height: 1; }
    .compact #workspace { width: 1fr; height: 1fr; }
    .compact #queues { height: 3; padding: 0 1; }
    .compact #models { display: none; }
    .compact #notice { height: 1; }
    ClearDialog, InspectDialog { align: center middle; background: #14244999; }
    #clear-dialog { width: 66; height: auto; max-height: 95%; padding: 1 2;
                    background: #ffffff; border: thick #bc5a12; }
    #clear-title { color: #bc5a12; text-style: bold; margin-bottom: 1; }
    #clear-names { margin-bottom: 1; color: #0649ff; }
    #clear-confirm { margin: 1 0; background: #f8faff; border: solid #b6c3d9; color: #142449; }
    .dialog-actions { height: 3; align: right middle; }
    Button { margin-left: 1; }
    #inspect-dialog { width: 94%; height: 90%; padding: 1 2;
                      background: #f8faff; border: thick #0649ff; }
    #inspect-title { height: auto; max-height: 4; color: #0649ff; text-style: bold; }
    #output-columns { height: 1fr; }
    .answer { width: 1fr; padding: 1 2; background: #ffffff;
              margin-right: 1; border: solid #b6c3d9; }
    .answer-title { color: #0649ff; text-style: bold; margin-bottom: 1; }
    TabPane { padding: 1 0; }
    TabbedContent { height: 1fr; }
    Tab { background: #e9eef8; color: #667390; }
    Tab.-active { background: #0649ff; color: #ffffff; }
    #close-inspector { background: #142449; color: #fff260; }
    """
    BINDINGS = [
        ("q", "quit", "Quit"),
        ("r", "refresh", "Refresh"),
        ("slash", "search", "Find"),
        ("e", "export", "Export"),
        ("j", "judge", "Judge 30"),
        ("p", "pause", "Pause/resume"),
        ("o", "dashboard", "Browser"),
        ("d", "clear", "Clear run"),
        Binding("D", "clear_all", "Clear all", show=False),
    ]

    def __init__(self, client: ConsoleClient, *, color: bool | None = None):
        original = os.environ.get("NO_COLOR")
        if color is True:
            os.environ.pop("NO_COLOR", None)
        elif color is False:
            os.environ["NO_COLOR"] = "1"
        try:
            super().__init__()
        finally:
            # Textual consumes this variable; preserve the launching environment.
            if original is not None:
                os.environ["NO_COLOR"] = original
            else:
                os.environ.pop("NO_COLOR", None)
        self.client = client
        self.selected_run: str | None = None
        self.records: list[dict] = []
        self.pairs: dict[str, tuple[dict, dict]] = {}
        self.stats: dict = {}
        self.refreshing = False
        self.runs_version = self.records_version = ""

    def compose(self) -> ComposeResult:
        yield Static(
            "✣  GAUGE  / PIXEL LAB                              EVALUATION CONSOLE", id="brand"
        )
        yield Static("Connecting to saved results…", id="queues")
        with Horizontal(id="body"):
            with Vertical(id="sidebar"):
                yield Static("SAVED RUNS", id="sidebar-title")
                yield DataTable(id="runs", cursor_type="row", show_row_labels=False)
            with Vertical(id="workspace"):
                yield Static("Select a saved run", id="models")
                yield Input(placeholder="Find request, ID, model, or output…", id="search")
                yield DataTable(id="pairs", cursor_type="row", show_row_labels=False)
        yield Static(
            "↑↓ Navigate · Enter inspects a response · D previews clearing all saved runs",
            id="notice",
        )
        yield Footer()

    def on_mount(self) -> None:
        runs = self.query_one("#runs", DataTable)
        runs.add_column("Run", width=22)
        runs.add_column("Status", width=10)
        pairs = self.query_one("#pairs", DataTable)
        for name, width in (
            ("Request", 36),
            ("Model", 22),
            ("Evaluation", 11),
            ("Confidence", 10),
            ("Latency", 10),
        ):
            pairs.add_column(name, width=width)
        self.set_interval(2.5, self.action_refresh)
        self.action_refresh()

    def on_resize(self, event) -> None:
        self.set_class(event.size.width < 110, "compact")

    def tell(self, message: str) -> None:
        self.query_one("#notice", Static).update(Text(plain(message)))

    def action_refresh(self) -> None:
        if not self.refreshing:
            self.run_worker(self.refresh_data())

    async def refresh_data(self) -> None:
        self.refreshing = True
        try:
            snapshot = await asyncio.to_thread(self.client.snapshot)
            self.stats = snapshot["stats"]
            self.query_one("#queues", Static).update(status_view(self.stats))
            runs = snapshot["runs"]
            if self.selected_run not in {r["id"] for r in runs}:
                self.selected_run = runs[0]["id"] if runs else None
                self.records_version = ""
            version = json.dumps(runs)
            if version != self.runs_version:
                table = self.query_one("#runs", DataTable)
                table.clear()
                for row in runs:
                    table.add_row(Text(plain(row["name"])), colored(row["status"]), key=row["id"])
                index = next((i for i, r in enumerate(runs) if r["id"] == self.selected_run), 0)
                if runs:
                    table.move_cursor(row=index)
                self.runs_version = version
            selection = self.selected_run
            records = await asyncio.to_thread(self.client.records, selection) if selection else []
            if selection != self.selected_run:
                return
            version = json.dumps(records)
            if version != self.records_version:
                self.records, self.records_version = records, version
                self.query_one("#models", Static).update(model_table(records))
                self.render_pairs()
        except (ConsoleError, ValueError) as exc:
            self.tell(str(exc))
        finally:
            self.refreshing = False

    def render_pairs(self) -> None:
        query = self.query_one("#search", Input).value.casefold()
        table = self.query_one("#pairs", DataTable)
        cursor = table.cursor_row
        table.clear()
        self.pairs = {}
        for record in self.records:
            capture = record["capture"]
            common = (
                request_title(capture) + capture["call_id"] + response_text(capture.get("response"))
            )
            jobs = record.get("comparisons", []) or [
                {
                    "candidate": "Original only",
                    "status": "original",
                    "reason": "Comparisons have not been dispatched for this capture.",
                }
            ]
            for job in jobs:
                searchable = common + job["candidate"] + str(job.get("candidate_output", ""))
                if query and query not in searchable.casefold():
                    continue
                key = capture["call_id"] + "|" + job["candidate"]
                self.pairs[key] = (record, job)
                confidence = job.get("judgment", {}).get("confidence")
                latency = job.get("latency_ms")
                table.add_row(
                    Text(request_title(capture)[:64]),
                    Text(plain(job["candidate"].rsplit("/", 1)[-1])),
                    colored(verdict(job)),
                    f"{confidence:.0%}" if confidence is not None else "—",
                    f"{latency:.0f} ms" if latency is not None else "—",
                    key=key,
                )
        if table.row_count:
            table.move_cursor(row=min(cursor, table.row_count - 1))

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "search":
            self.render_pairs()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        key = str(event.row_key.value)
        if event.data_table.id == "runs":
            self.selected_run = key
            self.records_version = ""
            self.action_refresh()
        elif key in self.pairs:
            self.push_screen(InspectDialog(*self.pairs[key]))

    def action_search(self) -> None:
        self.query_one("#search", Input).focus()

    async def operate(self, fn, message: str) -> None:
        try:
            result = await asyncio.to_thread(fn)
            self.tell(message if result is None else message + " " + str(result))
            self.action_refresh()
        except (ConsoleError, OSError) as exc:
            self.tell(str(exc))

    def action_export(self) -> None:
        if self.selected_run:
            run_id = self.selected_run
            output = Path("out") / f"{run_id}-{time.time_ns()}.json"
            self.run_worker(
                self.operate(lambda: export_run(self.client, run_id, output), "Exported:")
            )

    def action_judge(self) -> None:
        if self.selected_run:
            run_id = self.selected_run
            self.run_worker(self.operate(lambda: self.client.judge(run_id), "Judge queue:"))

    def action_pause(self) -> None:
        paused = not self.stats.get("paused", False)

        def change():
            self.client.request("POST", "/control", {"paused": paused})

        self.run_worker(
            self.operate(
                change, "Comparison dispatch paused" if paused else "Comparison dispatch resumed"
            )
        )

    def action_dashboard(self) -> None:
        def open_dashboard():
            webbrowser.open(self.client.dashboard(self.selected_run))

        self.run_worker(self.operate(open_dashboard, "Opened saved results in the browser"))

    def action_clear(self) -> None:
        if self.selected_run:
            self.run_worker(self.preview_clear([self.selected_run]))

    def action_clear_all(self) -> None:
        self.run_worker(self.preview_clear(None))

    async def preview_clear(self, run_ids: list[str] | None) -> None:
        try:
            preview = await asyncio.to_thread(self.client.preview, run_ids)
            if not preview["run_ids"]:
                self.tell("No saved runs to clear")
                return

            def clear_preview():
                result = self.client.clear(preview)
                return result["backup"]

            def confirmed(accepted: bool | None) -> None:
                if accepted:
                    self.run_worker(self.operate(clear_preview, "Cleared; backup:"))

            self.push_screen(ClearDialog(preview), confirmed)
        except ConsoleError as exc:
            self.tell(str(exc))
