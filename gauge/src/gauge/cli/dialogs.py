"""Focused inspection and explicit cleanup confirmation for the terminal console."""

from __future__ import annotations

import json

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Static, TabbedContent, TabPane

from gauge.cli.views import plain, request_title, response_text, verdict


class ClearDialog(ModalScreen[bool]):
    BINDINGS = [("escape", "cancel", "Cancel")]

    def __init__(self, preview: dict):
        super().__init__()
        self.preview = preview

    def compose(self) -> ComposeResult:
        p = self.preview
        with Vertical(id="clear-dialog"):
            yield Static("CLEAR SAVED RESULTS", id="clear-title")
            names = "\n".join(plain(r["name"]) for r in p["runs"][:5])
            yield Static(Text(names), id="clear-names")
            yield Static(
                Text(
                    f"{len(p['run_ids'])} runs · {p['captures']:,} captures\n"
                    f"{p['comparisons']:,} comparisons · {p['judgments']:,} judgments\n\n"
                    "A database backup will be saved before clearing.\n"
                    "The backend must be idle. Type CLEAR to confirm."
                )
            )
            yield Input(placeholder="CLEAR", id="clear-confirm")
            with Horizontal(classes="dialog-actions"):
                yield Button("Cancel", id="cancel-clear")
                yield Button("Clear results", id="confirm-clear", variant="error", disabled=True)

    def on_input_changed(self, event: Input.Changed) -> None:
        self.query_one("#confirm-clear", Button).disabled = event.value != "CLEAR"

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "confirm-clear")

    def action_cancel(self) -> None:
        self.dismiss(False)


class InspectDialog(ModalScreen):
    BINDINGS = [("escape", "dismiss", "Back")]

    def __init__(self, record: dict, job: dict):
        super().__init__()
        self.record, self.job = record, job

    def compose(self) -> ComposeResult:
        capture, job = self.record["capture"], self.job
        with Vertical(id="inspect-dialog"):
            yield Static(Text(request_title(capture)), id="inspect-title")
            yield Static(Text(f"{plain(job['candidate'])}  ·  {verdict(job).upper()}"))
            with TabbedContent():
                with TabPane("Outputs"):
                    with Horizontal(id="output-columns"):
                        with VerticalScroll(classes="answer"):
                            yield Static("ORIGINAL", classes="answer-title")
                            yield Static(Text(plain(response_text(capture.get("response")))))
                        with VerticalScroll(classes="answer"):
                            yield Static("CANDIDATE", classes="answer-title")
                            text = job.get(
                                "candidate_output", job.get("error", job.get("reason", "Waiting"))
                            )
                            yield Static(Text(plain(text)))
                with TabPane("Captured input"):
                    with VerticalScroll():
                        yield Static(Text(plain(json.dumps(capture.get("request"), indent=2))))
                with TabPane("Judgment"):
                    with VerticalScroll():
                        yield Static(
                            Text(
                                plain(
                                    json.dumps(
                                        job.get("judgment", {"status": "unjudged"}), indent=2
                                    )
                                )
                            )
                        )
            yield Button("Back  [Esc]", id="close-inspector")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss()
