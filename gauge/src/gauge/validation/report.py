"""Portable results: reuse Pixel Lab for inspection and keep quality separate from agreement."""

from __future__ import annotations

import json
from html import escape

from gauge.assets import asset


def render(report: dict) -> str:
    payload = json.dumps(report, ensure_ascii=False).replace("<", "\\u003c").replace(">", "\\u003e")
    payload = (
        payload.replace("&", "\\u0026").replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")
    )
    rows = []
    for group, summary in report["quality"].items():
        metrics = (
            " · ".join(f"{k}: {v['mean']:.1%} (n={v['n']})" for k, v in summary["metrics"].items())
            or "Not scored"
        )
        rows.append(
            f"<tr><td>{escape(group)}</td><td>{summary['calls']}</td>"
            f"<td>{summary['scored']}</td><td>{escape(metrics)}</td></tr>"
        )
    css = asset("pixel-lab.css").read_text()
    script = asset("pixel-lab.js").read_text()
    adapter = asset("gauge-adapter.js").read_text()
    checks = " · ".join(f"{k}: {'PASS' if v else 'FAIL'}" for k, v in report["checks"].items())
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Public dataset validation · Gauge</title>
<style>{css}
body{{margin:20px;background:#dce3ef}}
.validation{{background:white;border:2px solid #142449;padding:24px;
margin-bottom:20px;box-shadow:4px 4px 0 #b6c3d9}}
.validation h1{{margin:8px 0 16px}}
.validation p{{font-size:11px;line-height:1.7;color:#667390}}
.validation table{{width:100%;font-size:11px;border-collapse:collapse}}
.validation td,.validation th{{padding:12px;border-bottom:1px solid #b6c3d9;
text-align:left}}
.quality-scroll{{overflow:auto}}
#app{{height:auto;min-height:700px}}
.pl-comparison-content{{overflow:visible}}</style></head><body>
<header class="pl validation"><span class="pl-eyebrow">GAUGE / PUBLIC DATASET VALIDATION</span>
<h1>Different tasks. Observable connections.</h1>
<p>{escape(report["run_id"])} · {report["source_calls"]} source calls
· fixture {escape(report["manifest_sha256"][:16])}</p>
<p>{escape(checks)}</p><p>Quality below uses public dataset labels, not OpenAI answers. 
Dialogue replay has no live answer oracle and stays unscored. 
Exact agreement in the comparison table is a separate metric.</p>
<div class="quality-scroll"><table>
<thead><tr><th>Dataset / model</th><th>Calls</th><th>Scored</th><th>Quality</th></tr></thead>
<tbody>{"".join(rows)}</tbody></table></div></header>
<div id="app"></div><script type="application/json" id="validation-data">{payload}</script>
<script>{script}</script><script>{adapter}</script><script>
const data=JSON.parse(document.querySelector('#validation-data').textContent);
const records=data.records.map(row=>{{
const r=GaugeDashboard.toRequest(row);
r.metadata.correlation=data.correlation.graph[r.id];return r;}});
new PixelLab.Dashboard('#app',{{title:'Gauge',subtitle:'PUBLIC DATA / '+data.profile,
layout:'comparisons',maxRequests:1000,requests:records}});
</script></body></html>"""
