"""Read-only GitHub audit. Run with PYTHONPATH="$AGENTSKILLS_BIN" python3 ... ."""

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from _cli import cli

ROOT = Path(__file__).resolve().parents[2] / "data/doordash-llm-audit"


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def search(term):
    key = term.replace('"', '').replace(' ', '_').replace('/', '_').replace(':', '_')
    target = ROOT / "search" / f"{key}.json"
    existing = json.loads(target.read_text()) if target.exists() else {}
    if existing.get("complete_within_cap"):
        print(f"cached {term}", flush=True)
        return
    rows, pages = existing.get("items", []), existing.get("pages", [])
    for page in range(len(pages) + 1, 11):
        try:
            data = cli("gh", "api", "-X", "GET", "search/code", "-f", f"q=org:doordash {term}",
                       "-f", "per_page=100", "-f", f"page={page}")
        except (Exception, SystemExit):
            save(target, {**existing, "query": f"org:doordash {term}", "pages": pages,
                          "items": rows, "complete_within_cap": False,
                          "interrupted": True})
            raise
        rows.extend(data.get("items", []))
        pages.append({"page": page, "total_count": data.get("total_count"),
                      "incomplete_results": data.get("incomplete_results"),
                      "received": len(data.get("items", []))})
        done = len(rows) >= min(data.get("total_count", 0), 1000) or not data.get("items")
        save(target, {"query": f"org:doordash {term}", "retrieved_at": datetime.now(timezone.utc).isoformat(),
                      "pages": pages, "complete_within_cap": done,
                      "capped": data.get("total_count", 0) > 1000, "items": rows})
        print(f"{term}: page {page}, {len(rows)}/{data.get('total_count')} hits", flush=True)
        time.sleep(6.2)
        if done:
            break


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("terms", nargs="+")
    for term in parser.parse_args().terms:
        search(term)
