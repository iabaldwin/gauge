"""Pure presentation of observed backend data; no provider calls or invented scores."""

from __future__ import annotations

import statistics
from collections import defaultdict
from typing import Any

from rich.table import Table
from rich.text import Text

BLUE, GREEN, ORANGE, MUTED = "#0649ff", "#00814b", "#bc5a12", "#667390"


def plain(value: Any) -> str:
    # Prevent stored prompts, names, and model outputs injecting terminal control sequences.
    text = str(value)
    return "".join(c for c in text if c in "\n\t" or (ord(c) >= 32 and not 127 <= ord(c) <= 159))


def response_text(response: dict | None) -> str:
    if not response:
        return ""
    if response.get("choices"):
        return response["choices"][0].get("message", {}).get("content") or ""
    return "\n".join(
        p["text"]
        for m in response.get("output", [])
        if m.get("type") == "message"
        for p in m.get("content", [])
        if p.get("type") == "output_text"
    )


def request_title(capture: dict) -> str:
    request = capture.get("request", {})
    messages = request.get("messages", request.get("input", ""))
    if isinstance(messages, str):
        return plain(messages)
    for m in reversed(messages):
        if m.get("role") == "user":
            content = m.get("content", "")
            if isinstance(content, list):
                content = " ".join(p.get("text", "") for p in content)
            return plain(content)
    return capture["call_id"]


def verdict(job: dict) -> str:
    if job.get("status") != "completed":
        return str(job.get("status", "unknown"))
    if job.get("finish_reason") not in {None, "stop"}:
        return "incomplete"
    judgment = job.get("judgment", {})
    if judgment.get("status") in {"completed", "uncertain"}:
        return str(judgment.get("verdict", "unjudged"))
    return str(judgment.get("status", "unjudged"))


def colored(value: str) -> Text:
    style = (
        GREEN
        if value in {"equivalent", "completed"}
        else ("#d32746" if value in {"error", "failed"} else ORANGE)
    )
    return Text(plain(value), style=style)


def models(records: list[dict]) -> list[dict]:
    groups: dict[str, list] = defaultdict(list)
    for record in records:
        for job in record.get("comparisons", []):
            groups[job["candidate"]].append(job)
    result = []
    for name, jobs in sorted(groups.items()):
        completed = [
            j
            for j in jobs
            if j["status"] == "completed" and j.get("finish_reason") in {None, "stop"}
        ]
        judged = [j for j in completed if verdict(j) in {"equivalent", "different"}]
        latencies = [
            j["latency_ms"] for j in completed if isinstance(j.get("latency_ms"), (int, float))
        ]
        result.append(
            {
                "model": name,
                "completed": len(completed),
                "total": len(jobs),
                "judged": len(judged),
                "equivalent": sum(verdict(j) == "equivalent" for j in judged),
                "uncertain": sum(verdict(j) == "uncertain" for j in jobs),
                "errors": sum(j["status"] == "error" for j in jobs),
                "latency_ms": statistics.median(latencies) if latencies else None,
            }
        )
    return result


def model_table(records: list[dict]) -> Table:
    table = Table(expand=True, border_style=MUTED, header_style=BLUE)
    for column in ("Model", "Completed", "Equivalent", "Uncertain", "Median", "Errors"):
        table.add_column(column)
    for row in models(records):
        n = row["judged"]
        agreement = f"{row['equivalent'] / n:.0%} ({row['equivalent']}/{n})" if n else "—"
        ms = row["latency_ms"]
        table.add_row(
            plain(row["model"].rsplit("/", 1)[-1]),
            f"{row['completed']}/{row['total']}",
            Text(agreement, style=GREEN),
            str(row["uncertain"]),
            f"{ms:.0f} ms" if ms is not None else "—",
            str(row["errors"]),
        )
    return table


def progress(done: int, total: int, width: int = 20) -> Text:
    filled = min(width, int(width * done / total)) if total else 0
    bar = Text("█" * filled, style=BLUE)
    bar.append("░" * (width - filled), style="#b6c3d9")
    bar.append(f"  {done:,} / {total:,}", style=MUTED)
    return bar


def status_view(stats: dict) -> Text:
    text = Text("BACKEND QUEUE  ", style=f"bold {BLUE}")
    done = stats.get("completed", 0) + stats.get("failed", 0)
    total = stats.get("comparison_jobs", 0)
    skipped = stats.get("ineligible", 0) * len(stats.get("models", []))
    done += skipped + stats.get("queue_dropped", 0)
    text.append_text(progress(done, total))
    text.append(f"\nQueued {stats.get('queued', 0)}   Active {stats.get('active', 0)}   ", MUTED)
    text.append(f"Errors {stats.get('failed', 0)}   ", "#d32746")
    losses = sum(
        d.get("export_dropped", 0) + d.get("failed", 0) + d.get("queue_dropped", 0)
        for d in stats.get("capture_diagnostics", [])
    )
    if losses:
        text.append(f"Capture losses {losses}   ", "#d32746")
    if stats.get("configuration_note"):
        text.append("\n" + stats["configuration_note"], MUTED)
    j = stats.get("judging", {})
    text.append(
        f"JEV: {j.get('active', 0)} active · {j.get('queued', 0)} queued · "
        f"{j.get('completed', 0)} decided · {j.get('uncertain', 0)} uncertain",
        MUTED,
    )
    return text
