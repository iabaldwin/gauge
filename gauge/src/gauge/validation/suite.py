"""Deterministic sampling and prompt contracts, independent of dataset transport."""

from __future__ import annotations

import random
from pathlib import Path

from gauge.validation import datasets
from gauge.validation.records import Scenario, write_suite
from gauge.validation.sources import BANKING, MULTIWOZ, SQUAD, fetch

PROTOCOLS = ("chat", "responses", "chain", "conversation")


def prepare(
    output: Path,
    cache: Path,
    profile: str = "smoke",
    seed: int = 42,
    fixed_prefix: bool = False,
) -> dict:
    if profile not in {"smoke", "full"}:
        raise ValueError("Profile must be smoke or full")
    files, receipts = {}, []
    for source in (BANKING, MULTIWOZ, SQUAD):
        path, receipt = fetch(source, cache)
        files[source.name] = path
        receipts.append(receipt)
    rng = random.Random(seed)
    rows = datasets.banking(files["banking77"])
    labels = sorted({r["label"] for r in rows})
    instruction = "Classify the banking request. Return exactly one intent name from: " + ", ".join(
        labels
    )
    # Round-robin across shuffled intent groups rather than sampling the dominant intents.
    groups = {label: [r for r in rows if r["label"] == label] for label in labels}
    for group in groups.values():
        rng.shuffle(group)
    rng.shuffle(labels)
    selected = [
        groups[label][i]
        for i in range(max(map(len, groups.values())))
        for label in labels
        if i < len(groups[label])
    ]
    count = 10 if profile == "smoke" else 400
    scenarios = [
        Scenario(
            "banking77/" + r["id"],
            "banking77",
            "classification",
            "chat",
            instruction,
            [{"user": r["text"], "assistant": ""}],
            r["label"],
            {"source_id": r["id"], "seed": seed},
        )
        for r in selected[:count]
    ]
    rows = datasets.questions(files["squad2"])
    count = 8 if profile == "smoke" else 200
    for answerable in (True, False):
        pool = [
            r
            for r in rows
            if bool(r["answers"]["text"]) == answerable and len(r["context"]) <= 8000
        ]
        rng.shuffle(pool)
        for r in pool[: count // 2]:
            scenarios.append(
                Scenario(
                    "squad2/" + r["id"],
                    "squad2",
                    "extraction",
                    "responses",
                    'Use only the passage. Return JSON {"answer": "exact answer span"}, '
                    'or {"answer": null} when the passage does not support an answer.',
                    [
                        {
                            "user": f"Passage:\n{r['context']}\n\nQuestion: {r['question']}",
                            "assistant": "",
                        }
                    ],
                    r["answers"]["text"],
                    {"source_id": r["id"], "seed": seed},
                )
            )
    rows = datasets.dialogues(files["multiwoz22"])
    rng.shuffle(rows)
    count = (
        (8 if profile == "smoke" else 100) if fixed_prefix else (2 if profile == "smoke" else 25)
    )
    for index, r in enumerate(rows[:count]):
        protocols = (("chat", "responses")[index % 2],) if fixed_prefix else PROTOCOLS
        for protocol in protocols:
            scenarios.append(
                Scenario(
                    f"multiwoz22/{r['id']}/{protocol}",
                    "multiwoz22",
                    "dialogue",
                    protocol,
                    "You are a concise travel assistant. Remember the user's requirements. "
                    "Do not claim to have made a booking or accessed tools. "
                    "Ask for missing details.",
                    r["turns"],
                    None,
                    {"source_id": r["id"], "seed": seed},
                )
            )
    expected = 50 if profile == "smoke" else 1000
    if sum(len(s.turns) for s in scenarios) != expected:
        raise ValueError("Dataset slice cannot fill the requested profile")
    return write_suite(
        output, scenarios, receipts, profile, "fixed-prefix" if fixed_prefix else "protocols"
    )
