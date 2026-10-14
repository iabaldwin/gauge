#!/usr/bin/env python3
"""Score merged verticals for consensus, surge, and thin-neighbor white space.

Fall 2026 is included in counts and excluded from slopes. The size distribution
after a fine merge is singleton-heavy (median 1). A pile starts at 4 companies,
which is above a pair and below the handful of verticals that reach 6.
"""

from __future__ import annotations

import json
import re
from collections import Counter, defaultdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "data"
BATCH_ORDER = ("W26", "X26", "S26", "F26")
COMPLETED = ("W26", "X26", "S26")
DENSE_CUTOFF = 4


def slugify(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def load_groups() -> list[dict]:
    groups: list[dict] = []
    for path in sorted((DATA / "merge_out").glob("*.json")):
        groups.extend(json.loads(path.read_text())["groups"])
    return groups


def build() -> dict:
    companies = {
        row["slug"]: row
        for row in (
            json.loads(line)
            for line in (DATA / "companies.jsonl").read_text().splitlines()
            if line.strip()
        )
    }
    classified = {
        row["slug"]: row
        for row in (
            json.loads(line)
            for line in (DATA / "classified.jsonl").read_text().splitlines()
            if line.strip()
        )
    }
    verticals: list[dict] = []
    used: set[str] = set()
    for group in load_groups():
        base = f"{group['domain']}--{slugify(group['vertical'])}"
        vertical_id = base
        suffix = 2
        while vertical_id in used:
            vertical_id = f"{base}-{suffix}"
            suffix += 1
        used.add(vertical_id)
        counts: Counter[str] = Counter()
        modalities: Counter[str] = Counter()
        members = []
        for slug in group["slugs"]:
            company = companies[slug]
            label = classified[slug]
            counts[company["batch"]] += 1
            modalities[label["modality"]] += 1
            members.append(
                {
                    "slug": slug,
                    "name": company["name"],
                    "batch": company["batch"],
                    "one_liner": company.get("one_liner") or "",
                    "website": company.get("website"),
                }
            )
        members.sort(key=lambda member: (BATCH_ORDER.index(member["batch"]), member["name"].lower()))
        batch_counts = {batch: counts[batch] for batch in BATCH_ORDER}
        completed = sum(batch_counts[batch] for batch in COMPLETED)
        summer = batch_counts["S26"]
        verticals.append(
            {
                "id": vertical_id,
                "label": group["vertical"],
                "domain": group["domain"],
                "buyer": group["buyer"],
                "job": group["job"],
                "modality": modalities.most_common(1)[0][0],
                "count": len(members),
                "counts": batch_counts,
                "completed_count": completed,
                "consensus": all(batch_counts[batch] > 0 for batch in COMPLETED),
                "surge": summer >= 2 and summer > completed - summer,
                "dense": len(members) >= DENSE_CUTOFF,
                "companies": members,
            }
        )

    by_domain: dict[str, list[dict]] = defaultdict(list)
    for vertical in verticals:
        by_domain[vertical["domain"]].append(vertical)

    for vertical in verticals:
        vertical["white_space"] = None

    for dense in verticals:
        if not dense["dense"]:
            continue
        dense_buyer = dense["buyer"].strip().lower()
        dense_job = dense["job"].strip().lower()
        for thin in by_domain[dense["domain"]]:
            if thin["id"] == dense["id"] or thin["count"] > 2:
                continue
            share_buyer = thin["buyer"].strip().lower() == dense_buyer
            share_job = thin["job"].strip().lower() == dense_job
            if not (share_buyer or share_job):
                continue
            batches = {member["batch"] for member in thin["companies"]}
            sprout = bool(batches) and batches <= {"S26", "F26"}
            link = {
                "id": dense["id"],
                "label": dense["label"],
                "share": "buyer" if share_buyer else "job",
                "sprout": sprout,
            }
            if share_buyer and share_job:
                link["share"] = "buyer+job"
            slot = thin["white_space"]
            if slot is None:
                thin["white_space"] = {"sprout": sprout, "neighbors": [link]}
            else:
                slot["neighbors"].append(link)
                slot["sprout"] = slot["sprout"] or sprout

    verticals.sort(key=lambda vertical: (-vertical["count"], vertical["id"]))
    return {
        "dense_cutoff": DENSE_CUTOFF,
        "dense_cutoff_note": (
            "After the merge the median vertical has 1 company and the 75th "
            "percentile is still 1. Six verticals have 4 or more, two have 6 "
            "or more. Dense means 4 or more: a pile, rather than a pair."
        ),
        "window": list(BATCH_ORDER),
        "completed_batches": list(COMPLETED),
        "consensus_rule": "At least one company in each of W26, X26, and S26.",
        "surge_rule": "S26 holds more companies than W26 and X26 combined, and at least 2.",
        "white_space_rule": (
            "1–2 companies, same domain as a dense vertical, same buyer or "
            "same job, different vertical. A sprout appears only in S26 or F26."
        ),
        "vertical_count": len(verticals),
        "verticals": verticals,
    }


def main() -> None:
    payload = build()
    (DATA / "verticals.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    dense = [row for row in payload["verticals"] if row["dense"]]
    space = [row for row in payload["verticals"] if row["white_space"]]
    print(f"verticals {payload['vertical_count']}")
    print(f"dense {len(dense)}")
    print(f"white space {len(space)}")


if __name__ == "__main__":
    main()
