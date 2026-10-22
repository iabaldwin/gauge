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
    if task == "instruction":
        from gauge.evaluation.instructions import grade as grade_instructions

        try:
            return grade_instructions(expected, text)
        except Exception as exc:
            return {"status": "not_scored", "reason": "checker_error:" + type(exc).__name__}
    if task in {"coqa", "quac"}:
        return conversational_qa(task, expected["answers"], text)
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
        for metric in (
            "accuracy",
            "schema_valid",
            "exact_match",
            "f1",
            "human_equivalence",
            "prompt_strict",
            "instruction_strict",
        ):
            if score["status"] == "scored" and metric in score:
                group["metrics"].setdefault(metric, []).append(float(score[metric]))
    for group in groups.values():
        group["metrics"] = {
            k: {"mean": sum(v) / len(v), "n": len(v)} for k, v in group["metrics"].items()
        }
    return groups


def token_f1(prediction: str, reference: str) -> float:
    a, b = normalize(prediction).split(), normalize(reference).split()
    if not a or not b:
        return float(a == b)
    common = sum((Counter(a) & Counter(b)).values())
    return 2 * common / (len(a) + len(b))


def leave_one_out(prediction: str, references: list[str], metric) -> float:
    groups = (
        [references[:i] + references[i + 1 :] for i in range(len(references))]
        if len(references) > 1
        else [references]
    )
    return sum(max(metric(prediction, r) for r in group) for group in groups) / len(groups)


def conversational_qa(task: str, references: list[str], text: str) -> dict:
    if not references or any(not isinstance(r, str) for r in references):
        raise ValueError("Nonempty reference answers required")
    answer = text.strip()
    metric = token_f1
    if task == "quac":
        cannot = references.count("CANNOTANSWER")
        references = (
            ["CANNOTANSWER"]
            if cannot >= len(references) - cannot
            else [r for r in references if r != "CANNOTANSWER"]
        )

        def metric(prediction, reference):
            if reference == "CANNOTANSWER":
                return float(prediction == reference)
            # QuAC gives zero for zero overlapping tokens, including empty normalized spans.
            return token_f1(prediction, reference) if normalize(reference) else 0.0

        human = (
            sum(
                max(token_f1(r, other) for j, other in enumerate(references) if j != i)
                for i, r in enumerate(references)
            )
            / len(references)
            if len(references) > 1
            else 1.0
        )
        if human < 0.4:
            return {"status": "not_scored", "reason": "low_human_agreement", "human_f1": human}
    f1 = leave_one_out(answer, references, metric)
    em = leave_one_out(answer, references, lambda a, b: float(normalize(a) == normalize(b)))
    result = {
        "status": "scored",
        "f1": f1,
        "exact_match": em,
        "passed": f1 >= (human if task == "quac" else 1.0),
        "criterion": (
            "QuAC answer F1 reaches human agreement"
            if task == "quac"
            else "CoQA answer receives full-credit F1"
        ),
        "scorer": task + " official leave-one-annotation-out answer metric",
    }
    if task == "quac":
        result.update(human_f1=human, human_equivalence=float(f1 >= human))
    return result


def expected_for(scenario, turn: int):
    return scenario.turns[turn].get("expected", scenario.expected)
