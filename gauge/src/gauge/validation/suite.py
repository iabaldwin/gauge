"""Seeded, bounded quality suites with explicit evidence and history policies."""

from __future__ import annotations

import random
from pathlib import Path

from gauge.validation import conversations, datasets
from gauge.validation.records import Scenario, write_suite
from gauge.validation.sources import BANKING, COQA, MULTIIF, QUAC, SQUAD, fetch

DATASETS = {s.name: s for s in (BANKING, SQUAD, COQA, QUAC, MULTIIF)}


def prepare(
    output: Path, cache: Path, profile: str = "smoke", seed: int = 42, dataset: str = "all"
) -> dict:
    if profile not in {"smoke", "full"} or dataset not in {"all", *DATASETS}:
        raise ValueError("Unknown profile or dataset")
    names = list(DATASETS) if dataset == "all" else [dataset]
    receipts, scenarios = [], []
    for name in names:
        path, receipt = fetch(DATASETS[name], cache)
        receipts.append(receipt)
        # Dataset selection is stable whether prepared alone or in the combined suite.
        rng = random.Random(f"{seed}:{name}")
        count = 5 if profile == "smoke" else 100
        if name == "banking77":
            rows = datasets.banking(path)
            labels = sorted({r["label"] for r in rows})
            groups = {label: [r for r in rows if r["label"] == label] for label in labels}
            for group in groups.values():
                rng.shuffle(group)
            rng.shuffle(labels)
            selected = [
                groups[label][i]
                for i in range(max(map(len, groups.values())))
                for label in labels
                if i < len(groups[label])
            ][:count]
            for r in selected:
                scenarios.append(
                    Scenario(
                        f"banking77/{r['id']}",
                        name,
                        "classification",
                        "chat",
                        "Classify the banking request. Return exactly one intent name from: "
                        + ", ".join(sorted(labels)),
                        [{"user": r["text"], "assistant": ""}],
                        r["label"],
                        {"source_id": r["id"], "seed": seed},
                    )
                )
        elif name == "squad2":
            rows = datasets.questions(path)
            count = 6 if profile == "smoke" else 100
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
                            f"squad2/{r['id']}",
                            name,
                            "extraction",
                            "responses",
                            'Use only the passage. Return JSON {"answer": "exact answer span"}, '
                            'or {"answer": null} when the passage does not support an answer.',
                            [
                                {
                                    "user": (
                                        f"Passage:\n{r['context']}\n\nQuestion: {r['question']}"
                                    ),
                                    "assistant": "",
                                }
                            ],
                            r["answers"]["text"],
                            {"source_id": r["id"], "seed": seed},
                        )
                    )
        else:
            rows = getattr(conversations, name)(path)
            count = 2 if profile == "smoke" else 50
            rng.shuffle(rows)
            if len(rows) < count:
                raise ValueError(f"{name} cannot fill the requested profile")
            for index, r in enumerate(rows[:count]):
                if name == "multiif":
                    instruction = "Follow the user's instructions."
                else:
                    instruction = (
                        "Answer the question using only the supplied passage and conversation. "
                        + (
                            "Give a short answer, without explanation. "
                            "Use 'unknown' if unanswerable."
                            if name == "coqa"
                            else "Return only an exact answer span copied from the passage, "
                            "or CANNOTANSWER "
                            "if the passage cannot answer the question. Do not add an explanation."
                        )
                        + "\n\nPassage:\n"
                        + r["passage"]
                    )
                if name == "quac":
                    instruction = (
                        f"Article: {r['title']}\nSection: {r['section']}\n"
                        f"Background (orientation only): {r['background']}\n\n"
                        + instruction
                    )
                scenarios.append(
                    Scenario(
                        f"{name}/{r['id']}",
                        name,
                        "instruction" if name == "multiif" else name,
                        ("chat", "responses")[index % 2],
                        instruction,
                        r["turns"],
                        provenance={
                            "source_id": r["id"],
                            "seed": seed,
                            "domain": r["domain"],
                            "available_conversations": len(rows),
                            "slice": "first 3 turns" if name == "multiif" else "first 4 turns",
                            "protocol": "shared-source-history adaptation"
                            if name == "multiif"
                            else "recorded-prefix",
                            "benchmark": "sampled development evaluation",
                        },
                        history_mode="source" if name == "multiif" else "recorded",
                    )
                )
    return write_suite(output, scenarios, receipts, profile, "quality-suite")
