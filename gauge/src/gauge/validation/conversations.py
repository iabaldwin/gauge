"""Grounded conversation adapters. Current gold answers stay out of request context."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from gauge.evaluation.instructions import validate


def coqa(path: Path) -> list[dict]:
    rows = []
    for item in json.loads(path.read_text())["data"]:
        # These source domains have explicit CC-BY-SA / Apache redistribution terms.
        if item["source"] not in {"wikipedia", "gutenberg", "cnn"}:
            continue
        answers = {a["turn_id"]: a for a in item["answers"]}
        additional = [
            {a["turn_id"]: a for a in group} for group in item["additional_answers"].values()
        ]
        turns = []
        for question in item["questions"][:4]:
            number = question["turn_id"]
            a = answers[number]
            targets = [a["input_text"], *(g[number]["input_text"] for g in additional)]
            # Retain all annotations, including repeated answers: they affect LOO scoring.
            turns.append(
                {
                    "user": question["input_text"],
                    "assistant": a["input_text"],
                    "expected": {"answers": targets},
                    "evidence": a["span_text"],
                    "source_turn": number,
                }
            )
        if len(turns) == 4 and len(item["story"]) <= 14000:
            rows.append(
                {
                    "id": item["id"],
                    "passage": item["story"],
                    "turns": turns,
                    "domain": item["source"],
                }
            )
    return rows


def quac(path: Path) -> list[dict]:
    rows = []
    for article in json.loads(path.read_text())["data"]:
        for item in article["paragraphs"]:
            turns = []
            for question in item["qas"][:4]:
                original = question["orig_answer"]["text"]
                turns.append(
                    {
                        "user": question["question"],
                        "assistant": original,
                        "expected": {"answers": [a["text"] for a in question["answers"]]},
                        "evidence": original,
                        "source_turn": question["id"],
                    }
                )
            if len(turns) == 4 and len(item["context"]) <= 14000:
                rows.append(
                    {
                        "id": item["id"],
                        "passage": item["context"],
                        "turns": turns,
                        "domain": article["title"],
                        "title": article["title"],
                        "section": article["section_title"],
                        "background": article["background"],
                    }
                )
    return rows


def multiif(path: Path) -> list[dict]:
    rows = []
    for item in csv.DictReader(path.open()):
        if item["language"] != "English":
            continue
        turns = []
        try:
            for turn in (1, 2, 3):
                prompt = json.loads(item[f"turn_{turn}_prompt"])
                spec = {
                    "ids": json.loads(item[f"turn_{turn}_instruction_id_list"]),
                    "kwargs": [
                        json.loads(k) if isinstance(k, str) else k
                        for k in json.loads(item[f"turn_{turn}_kwargs"])
                    ],
                }
                validate(spec)
                if prompt["role"] != "user" or len(prompt["content"]) > 4000:
                    raise ValueError("Unsupported prompt")
                turns.append(
                    {
                        "user": prompt["content"],
                        "assistant": "",
                        "expected": spec,
                        "source_turn": turn,
                    }
                )
        except (ValueError, TypeError, KeyError):
            continue
        rows.append({"id": item["key"], "turns": turns, "domain": "English"})
    return rows
