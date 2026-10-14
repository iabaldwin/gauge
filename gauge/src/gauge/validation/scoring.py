"""Independent label/span scoring, never treating the original model as ground truth."""

from __future__ import annotations

import json
import re
import string
from collections import Counter
from typing import Any


def normalize(text: str) -> str:
    text = text.lower().translate(str.maketrans("", "", string.punctuation))
    return " ".join(re.sub(r"\b(a|an|the)\b", " ", text).split())


def grade(task: str, expected, text: str | None, completed: bool = True) -> dict:
    if not completed or text is None:
        return {"status": "not_scored", "reason": "incomplete_or_failed_output"}
    if task == "classification":
        return {"status": "scored", "accuracy": float(text.strip() == expected)}
    if task == "extraction":
        try:
            payload = json.loads(text)
            valid = (
                isinstance(payload, dict)
                and set(payload) == {"answer"}
                and (isinstance(payload["answer"], str) or payload["answer"] is None)
            )
        except (ValueError, TypeError):
            valid = False
        if not valid:
            return {"status": "scored", "schema_valid": False, "exact_match": 0.0, "f1": 0.0}
        answer = normalize(payload["answer"] or "")
        targets = [normalize(x) for x in expected] if expected else [""]

        def f1(target):
            a, b = answer.split(), target.split()
            if not a or not b:
                return float(a == b)
            common = sum((Counter(a) & Counter(b)).values())
            return 2 * common / (len(a) + len(b))

        return {
            "status": "scored",
            "schema_valid": True,
            "exact_match": float(answer in targets),
            "f1": max(map(f1, targets)),
        }
    return {"status": "not_scored", "reason": "scripted_dialogue_has_no_live_answer_oracle"}


def aggregate(rows: list[dict]) -> dict:
    groups: dict[str, Any] = {}
    for row in rows:
        key = row["dataset"] + "/" + row["target"]
        group = groups.setdefault(key, {"calls": 0, "scored": 0, "not_scored": 0, "metrics": {}})
        group["calls"] += 1
        score = row["score"]
        group["scored" if score["status"] == "scored" else "not_scored"] += 1
        for metric in ("accuracy", "schema_valid", "exact_match", "f1"):
            if metric in score:
                group["metrics"].setdefault(metric, []).append(float(score[metric]))
    for group in groups.values():
        group["metrics"] = {
            k: {"mean": sum(v) / len(v), "n": len(v)} for k, v in group["metrics"].items()
        }
    return groups
