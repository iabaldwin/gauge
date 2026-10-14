#!/usr/bin/env python3
"""Snapshot the last four YC batches from the public company directory."""

from __future__ import annotations

import json
import re
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
COMPANIES_URL = "https://www.ycombinator.com/companies"
ALGOLIA_APP = "45BWZJ1SGC"
ALGOLIA_INDEX = "YCCompany_production"
ALGOLIA_HOST = "https://45bwzj1sgc-dsn.algolia.net/1/indexes/*/queries"

# Directory facet value -> short code used in the rest of the pipeline.
BATCHES = {
    "Winter 2026": "W26",
    "Spring 2026": "X26",
    "Summer 2026": "S26",
    "Fall 2026": "F26",
}

KEEP = (
    "name",
    "slug",
    "one_liner",
    "long_description",
    "website",
    "industry",
    "subindustry",
    "tags",
    "team_size",
)

UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)


def public_search_key() -> str:
    """Read the search-only config the companies page embeds for every visitor."""
    req = urllib.request.Request(COMPANIES_URL, headers={"User-Agent": UA})
    html = urllib.request.urlopen(req, timeout=30).read().decode("utf-8", "ignore")
    match = re.search(r"window\.AlgoliaOpts\s*=\s*(\{.*?\})", html)
    if not match:
        raise RuntimeError("AlgoliaOpts was not on the companies page")
    opts = json.loads(match.group(1))
    key = opts.get("key")
    if not key:
        raise RuntimeError("AlgoliaOpts had no search key")
    return key


def query(key: str, params: str) -> dict:
    url = (
        f"{ALGOLIA_HOST}?x-algolia-application-id={ALGOLIA_APP}"
        f"&x-algolia-api-key={urllib.parse.quote(key)}"
    )
    body = json.dumps(
        {"requests": [{"indexName": ALGOLIA_INDEX, "params": params}]}
    ).encode()
    req = urllib.request.Request(
        url,
        data=body,
        headers={
            "Content-Type": "application/json",
            "User-Agent": UA,
            "Origin": "https://www.ycombinator.com",
            "Referer": "https://www.ycombinator.com/",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as response:
        payload = json.load(response)
    return payload["results"][0]


def pull_batch(key: str, batch_name: str) -> list[dict]:
    facet = urllib.parse.quote(json.dumps([[f"batch:{batch_name}"]]))
    result = query(key, f"query=&hitsPerPage=1000&facetFilters={facet}")
    hits = result.get("hits", [])
    if result.get("nbHits", 0) > len(hits):
        raise RuntimeError(
            f"{batch_name} returned {result['nbHits']} hits, page size was {len(hits)}"
        )
    return hits


def trim(hit: dict, batch_code: str) -> dict:
    row = {field: hit.get(field) for field in KEEP}
    row["batch"] = batch_code
    row["tags"] = row["tags"] or []
    return row


def main() -> None:
    fetched_at = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    key = public_search_key()
    rows: list[dict] = []
    counts: dict[str, int] = {}
    seen: set[str] = set()
    for batch_name, batch_code in BATCHES.items():
        hits = pull_batch(key, batch_name)
        added = 0
        for hit in hits:
            slug = hit.get("slug")
            if not slug or slug in seen:
                continue
            seen.add(slug)
            rows.append(trim(hit, batch_code))
            added += 1
        counts[batch_code] = added
        print(f"{batch_code}: {added}")

    DATA.mkdir(parents=True, exist_ok=True)
    companies_path = DATA / "companies.jsonl"
    with companies_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")

    meta = {
        "fetched_at": fetched_at,
        "source": "YCCompany_production via ycombinator.com/companies",
        "batches": counts,
        "count": len(rows),
        "note": (
            "Fall 2026 is mid-batch. Membership includes F26; "
            "trend slopes use W26, X26, and S26 only."
        ),
    }
    (DATA / "snapshot.json").write_text(
        json.dumps(meta, indent=2) + "\n", encoding="utf-8"
    )
    print(f"wrote {len(rows)} companies")


if __name__ == "__main__":
    main()
