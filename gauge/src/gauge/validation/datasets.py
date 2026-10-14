"""Dataset-specific parsing. No provider clients, capture IDs, or execution logic."""

from __future__ import annotations

import csv
import json
from pathlib import Path


def banking(path: Path) -> list[dict]:
    with path.open(newline="") as file:
        return [
            {"id": str(i), "text": row["text"], "label": row["category"]}
            for i, row in enumerate(csv.DictReader(file))
        ]


def dialogues(path: Path, turns: int = 4) -> list[dict]:
    result = []
    for item in json.loads(path.read_text()):
        pairs = []
        for turn in item["turns"]:
            if turn["speaker"] == "USER":
                pairs.append({"user": turn["utterance"], "assistant": ""})
            elif pairs:
                pairs[-1]["assistant"] = turn["utterance"]
        if len(pairs) >= turns:
            result.append({"id": item["dialogue_id"], "turns": pairs[:turns]})
    return result


def questions(path: Path) -> list[dict]:
    import pyarrow.parquet as pq  # type: ignore[import-untyped]

    return pq.read_table(path).to_pylist()
