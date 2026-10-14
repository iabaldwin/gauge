# Archived discovery research

This archive preserves the research that led to Gauge. Saved snapshots and generated
reports remain local in the repository-root `data/` and `out/` directories. They
are intentionally absent from a fresh clone. Run archive commands from the repository root.

YC company clustering followed by evidence-based investigation of potential business directions inside DoorDash.

- [GitHub audit: Python autoevals interface](../out/github-interface-audit.md)
- [DoorDash LLM workload audit](../out/doordash-llm-audit.html) — adopter evidence, evaluation readiness, and explicit search coverage; [written report](../out/doordash-llm-audit.md)
- [Gauge Python package](../gauge/README.md) — client capture, synthetic datasets, and concurrent model comparisons
- [Pixel Lab visualization library](../pixel-lab/README.md) — standalone CSS/JS trace workspace; [interactive local demo](../pixel-lab/demo/index.html)
- [Gauge backend dashboard](../gauge/dashboard/README.md) — live captures and comparisons using the standalone Pixel Lab library
- [Research narrative webpage](../out/index.html) — full findings, presentation mode, filters, and print / PDF
- [Revised findings and next tests](../out/doordash-discovery.md)
- [YC shortlist](../out/brief.md) and [original HTML view](../out/yc-landscape.html)
- [Research method](OVERVIEW.md)
- [All 20 direction assessments](../data/doordash-conclusions.json)
- [Source register](../data/research/sources.json) and [query log](../data/research/queries.json)

The research snapshots contain internal company information. Nothing was published externally.

Open the presentation locally:

```bash
open out/index.html
```

Use **Present** for nine chapters (arrow keys to move, Escape to exit), or browse the full page. Expand the evidence and direction rows for details. **Print / PDF** includes expanded evidence and all directions.

The page is self-contained and needs no server or external assets. Source links require the relevant workspace access. Rebuild it after editing the narrative or structured research data:

```bash
python3 -m research.discovery.present
```

Narrative content is in `data/presentation.json`; the page template is `research/discovery/presentation.html`. The builder joins the taxonomy, direction assessments, query log, and source register.
