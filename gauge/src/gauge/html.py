"""Offline reports using the same standalone Pixel Lab UI as the backend."""

from __future__ import annotations

import json
from dataclasses import asdict
from html import escape
from typing import Any

from gauge.assets import asset
from gauge.models import Outcome, Report


def _result(outcome: Outcome | None, model: str) -> dict[str, Any]:
    if outcome is None:
        return {"model": model, "status": "skipped", "reason": "Not recorded"}
    output = outcome.output
    value: dict[str, Any] = {
        "model": model,
        "status": "failed" if outcome.error else "succeeded" if outcome.comparable else "skipped",
        "durationMs": outcome.latency_ms,
        "error": outcome.error,
        "reason": output.status if output and not outcome.comparable else None,
        "raw": asdict(outcome),
    }
    if output:
        usage = output.token_usage
        value["tokens"] = {
            "input": usage.input_tokens,
            "output": usage.total_output_tokens,
            "reasoning": usage.reasoning_tokens,
            "answer": usage.final_answer_tokens,
            "nonReasoning": usage.non_reasoning_output_tokens,
            "unattributed": usage.unattributed_output_tokens,
            "issues": usage.issues,
            "sources": usage.sources,
        }
        if outcome.comparable:
            value["output"] = output.text
    return value


def report_requests(report: Report) -> list[dict[str, Any]]:
    """Adapt synthetic report records to the shared visualization contract."""
    outcomes = {(o.case_id, o.target): o for o in report.outcomes}
    comparisons = {(c.case_id, c.candidate): c for c in report.comparisons}
    requests = []
    for case in report.cases:
        candidates = []
        for name, description in report.targets.items():
            if name == report.reference:
                continue
            candidate = _result(outcomes.get((case.id, name)), description.get("model", name))
            candidate.update(id=name, provider=name)
            comparison = comparisons.get((case.id, name))
            if comparison:
                candidate["evaluation"] = asdict(comparison)
                grade = comparison.assessment or comparison.agreement
                candidate["grade"] = {
                    "label": f"{grade.metric}: {grade.value}"
                    if grade.value is not None
                    else "Unscored",
                    "explanation": grade.rationale,
                }
            candidates.append(candidate)
        reference = report.targets.get(report.reference, {})
        requests.append(
            {
                "id": case.id,
                "name": case.input,
                "captured": False,
                "input": {"instructions": report.instructions, "input": case.input},
                "original": _result(
                    outcomes.get((case.id, report.reference)),
                    reference.get("model", report.reference),
                ),
                "candidates": candidates,
                "spans": [],
                "metadata": {
                    "provenance": case.provenance,
                    "report_id": report.id,
                    "timing": "Per-model latency is recorded; dispatch offsets are unavailable.",
                },
            }
        )
    return requests


def render_report(report: Report) -> str:
    """Inline assets and safely embed records; no network or inference requests."""
    payload = json.dumps(report_requests(report), ensure_ascii=False, allow_nan=False)
    for char, escaped in (
        ("<", "\\u003c"),
        (">", "\\u003e"),
        ("&", "\\u0026"),
        ("\u2028", "\\u2028"),
        ("\u2029", "\\u2029"),
    ):
        payload = payload.replace(char, escaped)
    title = "Illustrative demo" if report.metadata.get("demo") else "Model comparison"
    css = asset("pixel-lab.css").read_text(encoding="utf-8")
    script = asset("pixel-lab.js").read_text(encoding="utf-8")
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{escape(title)} · Gauge</title><style>{css}
body{{margin:16px;background:#dce3ef}}
.report-context{{margin-bottom:12px;padding:12px 16px;border:1px solid #142449;background:white}}
.report-context p{{font-size:11px;color:#667390;margin-top:4px}}
</style></head><body><header class="pl report-context"><strong>{escape(title)}</strong>
<p>{escape(report.id)} · {escape(report.created_at)}
· Reference outputs are inferred, not ground truth.</p></header>
<div id="app"></div><script id="report-data" type="application/json">{payload}</script>
<script>{script}</script><script>
new PixelLab.Dashboard('#app',{{title:'Gauge',subtitle:'Offline comparison report',
layout:'comparisons',requests:JSON.parse(document.querySelector('#report-data').textContent)}});
</script></body></html>"""
