"""Reference agreement read model and bounded backend-only judging.

Conversation membership comes from an explicit dataset manifest, never content similarity.
"""

from __future__ import annotations

import json
import queue
import sqlite3
import threading
from collections import Counter

from gauge.backend.judge import JevJudge
from gauge.backend.replay import ReplayUnavailable, materialize, response_text
from gauge.backend.reporting import TerminalWrites
from gauge.evaluation.scoring import grade


def summarize(rows: list[dict]) -> dict:
    conversations: dict = {}
    for row in rows:
        conversation = conversations.setdefault(
            row["conversation"],
            {
                "id": row["conversation"],
                "dataset": row["dataset"],
                "turns": [],
            },
        )
        conversation["turns"].append(row)
    models = sorted({m for row in rows for m in row["outputs"]})
    totals: dict = {
        m: {
            "first_divergence": {},
            "first_departure": {},
            "all_agree": 0,
            "unresolved": 0,
            "agreement_through": {},
        }
        for m in models
    }
    for conversation in conversations.values():
        turns = sorted(conversation["turns"], key=lambda r: r["turn"])
        conversation["turns"] = turns
        conversation["models"] = {}
        for model in models:
            first = None
            departure = None
            semantic_prefix = True
            known_prefix = True
            for expected, turn in enumerate(turns):
                judgment = turn["outputs"].get(model, {}).get("judgment", {})
                verdict = judgment.get("verdict")
                semantic = judgment.get("answers", {}).get("semantic", {}).get("verdict", verdict)
                semantic_prefix = semantic_prefix and turn["turn"] == expected
                if semantic == "different" and departure is None:
                    departure = {
                        "turn": turn["turn"] + 1,
                        "exact": semantic_prefix,
                        "reference": judgment.get("answers", {})
                        .get("reference_validity", {})
                        .get("verdict", "unknown"),
                    }
                semantic_prefix = semantic_prefix and semantic == "equivalent"
                known_prefix = known_prefix and turn["turn"] == expected
                if verdict == "different" and first is None:
                    first = {"turn": turn["turn"] + 1, "exact": known_prefix}
                known_prefix = known_prefix and verdict == "equivalent"
                metric = totals[model]["agreement_through"].setdefault(
                    str(turn["turn"] + 1), {"agreed": 0, "total": 0}
                )
                metric["total"] += 1
                metric["agreed"] += int(known_prefix)
            conversation["models"][model] = {
                "first_divergence": first,
                "first_departure": departure,
                "all_agree": known_prefix,
            }
            if departure:
                key = str(departure["turn"]) if departure["exact"] else "earlier_unresolved"
                departures = totals[model]["first_departure"]
                departures[key] = departures.get(key, 0) + 1
            if first:
                key = str(first["turn"]) if first["exact"] else "earlier_unresolved"
                counts = totals[model]["first_divergence"]
                counts[key] = counts.get(key, 0) + 1
            elif known_prefix:
                totals[model]["all_agree"] += 1
            else:
                totals[model]["unresolved"] += 1
    statuses = Counter(
        o.get("judgment", {}).get("status", "unjudged") for r in rows for o in r["outputs"].values()
    )
    return {
        "conversations": list(conversations.values()),
        "models": totals,
        "statuses": dict(statuses),
        "mode": "reference-agreement",
    }


class AgreementEvaluator:
    def __init__(self, store, *, enabled: bool, budget: int = 2000):
        self.store, self.enabled, self.budget = store, enabled, budget
        self.lock = threading.RLock()
        self.jobs: queue.Queue = queue.Queue(2000)
        self.inflight: set[tuple] = set()
        self.reporting = TerminalWrites()
        self.started = 0
        self.workers_started = False
        with self.db() as db:
            for run_id, conversation, turn, payload in db.execute(
                "SELECT run_id, conversation, turn, payload FROM gauge_agreement"
            ).fetchall():
                row = json.loads(payload)
                for output in row["outputs"].values():
                    if output.get("judgment", {}).get("status") in {"pending", "running"}:
                        output["judgment"] = {
                            "status": "unavailable",
                            "verdict": "uncertain",
                            "reason": "Backend restarted; judge interrupted",
                        }
                self.save(db, run_id, conversation, turn, row)

    def db(self):
        return sqlite3.connect(self.store.path, timeout=5)

    @staticmethod
    def save(db, run_id, conversation, turn, row):
        db.execute(
            "INSERT OR REPLACE INTO gauge_agreement VALUES (?, ?, ?, ?)",
            (run_id, conversation, turn, json.dumps(row)),
        )

    def register(self, config: dict) -> dict:
        run_id, conversation, turn = config["run_id"], config["conversation"], config["turn"]
        reference, call_id = config["reference"], config.get("call_id")
        if (
            not all(
                isinstance(v, str) and 0 < len(v) <= 256
                for v in (run_id, conversation, config["dataset"])
            )
            or type(turn) is not int
            or not 0 <= turn < 100
            or not isinstance(reference, str)
            or not 0 < len(reference) <= 16000
        ):
            raise ValueError("Invalid reference case")
        assessment = config.get("assessment")
        if assessment is not None:
            if not isinstance(assessment, dict) or assessment.get("task") not in {
                "coqa",
                "quac",
                "instruction",
            }:
                raise ValueError("Unsupported native assessment")
            if len(json.dumps(assessment)) > 32000:
                raise ValueError("Assessment exceeds size limit")
            if assessment["task"] == "instruction":
                from gauge.evaluation.instructions import validate

                validate(assessment["expected"])
            else:
                grade(assessment["task"], assessment["expected"], "")
        with self.lock, self.db() as db:
            run = db.execute("SELECT payload FROM gauge_runs WHERE id=?", (run_id,)).fetchone()
            if not run:
                raise ValueError("Unknown run")
            prior = db.execute(
                "SELECT payload FROM gauge_agreement WHERE run_id=? AND conversation=? AND turn=?",
                (run_id, conversation, turn),
            ).fetchone()
            if prior:
                row = json.loads(prior[0])
                if (
                    row["reference"] != reference
                    or row["call_id"] != call_id
                    or row["dataset"] != config["dataset"]
                    or row.get("assessment") != assessment
                ):
                    raise ValueError("Reference case conflicts with saved case")
                return {"saved": True}
            if (
                db.execute(
                    "SELECT count(*) FROM gauge_agreement WHERE run_id=?", (run_id,)
                ).fetchone()[0]
                >= 1000
            ):
                raise ValueError("Reference case limit reached")
            captured = db.execute(
                "SELECT c.payload FROM gauge_captures c JOIN gauge_run_captures r "
                "ON c.call_id=r.call_id WHERE r.run_id=? AND c.call_id=?",
                (run_id, call_id),
            ).fetchone()
            if call_id and not captured:
                raise ValueError("Capture does not belong to run")
            row = {
                "conversation": conversation,
                "dataset": config["dataset"],
                "turn": turn,
                "reference": reference,
                "call_id": call_id,
                "assessment": assessment,
                "history_mode": config.get("history_mode", "recorded"),
                "evidence": config.get("evidence"),
                "context": [],
                "outputs": {},
            }
            event = json.loads(captured[0]) if captured else None
            if event:
                try:
                    row["context"] = materialize(event, self.store.response_capture)
                except ReplayUnavailable:
                    pass
                response = event.get("response") or {}
                text = response_text(response)
                complete = response.get("status") == "completed" or any(
                    c.get("finish_reason") == "stop" for c in response.get("choices", [])
                )
                row["outputs"]["OpenAI source"] = self.output(text, complete)
                jobs = db.execute(
                    "SELECT payload FROM gauge_comparisons WHERE call_id=?", (call_id,)
                ).fetchall()
                for job in jobs:
                    j = json.loads(job[0])
                    row["outputs"][j["candidate"]] = self.output(
                        j.get("candidate_output"),
                        j["status"] == "completed" and j.get("finish_reason") == "stop",
                    )
            row["outputs"].setdefault("OpenAI source", self.output(None, False))
            for model in json.loads(run[0]).get("models", []):
                row["outputs"].setdefault(model, self.output(None, False))
            if not row["context"]:
                for output in row["outputs"].values():
                    output["judgment"] = {
                        "status": "unavailable",
                        "verdict": "uncertain",
                        "reason": "Complete replay context unavailable",
                    }
            if assessment:
                for output in row["outputs"].values():
                    score = grade(
                        assessment["task"],
                        assessment["expected"],
                        output["text"],
                        output["judgment"]["status"] == "unjudged",
                    )
                    output["score"] = score
                    # QA lexical scores never determine semantic agreement. Complete QA
                    # outputs stay unjudged until the independent JEV reference decision.
                    if assessment["task"] == "instruction":
                        output["judgment"] = {
                            "status": "scored" if score["status"] == "scored" else "not_scored",
                            "verdict": ("equivalent" if score["passed"] else "different")
                            if score["status"] == "scored"
                            else "uncertain",
                            "reason": score.get("criterion", score.get("reason")),
                        }
            self.save(db, run_id, conversation, turn, row)
        return {"saved": True}

    @staticmethod
    def output(text, complete):
        return {
            "text": text,
            "judgment": {
                "status": "unjudged" if complete and text else "unavailable",
                "verdict": "uncertain",
            },
        }

    def rows(self, run_id):
        with self.db() as db:
            return [
                json.loads(r[0])
                for r in db.execute(
                    "SELECT payload FROM gauge_agreement WHERE run_id=? "
                    "ORDER BY conversation, turn",
                    (run_id,),
                )
            ]

    def stats(self):
        with self.lock:
            return {
                "pending": len(self.inflight),
                "calls_finished": self.started,
                "budget": self.budget,
                "enabled": self.enabled,
            }

    def snapshot(self, run_id):
        return summarize(self.rows(run_id))

    def submit(self, run_id, limit):
        if not self.enabled:
            raise ValueError("JEV disabled or PORTKEY_API_KEY missing")
        if type(limit) is not int or not 1 <= limit <= 2000:
            raise ValueError("Judge limit must be 1–2000")
        accepted = 0
        with self.lock, self.db() as db:
            if not self.workers_started:
                for i in range(2):
                    threading.Thread(
                        target=self.consume, daemon=True, name=f"reference-jev-{i}"
                    ).start()
                self.workers_started = True
            for row in self.rows(run_id):
                for model, output in row["outputs"].items():
                    key = (run_id, row["conversation"], row["turn"], model)
                    if (
                        output["judgment"]["status"] not in {"unjudged", "error"}
                        or key in self.inflight
                        or accepted >= limit
                        or self.started + len(self.inflight) >= self.budget
                    ):
                        continue
                    output["judgment"] = {"status": "pending", "verdict": "uncertain"}
                    self.inflight.add(key)
                    assessment = row.get("assessment") or {}
                    answers = (
                        assessment["expected"]["answers"]
                        if assessment.get("task") in {"coqa", "quac"}
                        else None
                    )
                    self.jobs.put_nowait(
                        (key, row["context"], row["reference"], output["text"], answers)
                    )
                    accepted += 1
                self.save(db, run_id, row["conversation"], row["turn"], row)
        return {
            "accepted": accepted,
            "remaining_budget": self.budget - self.started - len(self.inflight),
        }

    def consume(self):
        judge = None
        while True:
            key, context, reference, output, answers = self.jobs.get()
            try:
                judge = judge or JevJudge()
                judgment = (
                    judge.grade_answers(context, answers, output)
                    if answers is not None
                    else judge.grade_reference(context, reference, output)
                )
            except Exception as exc:
                judgment = {"status": "error", "verdict": "uncertain", "reason": type(exc).__name__}

            def write(key=key, judgment=judgment):
                with self.lock, self.db() as db:
                    found = db.execute(
                        "SELECT payload FROM gauge_agreement WHERE run_id=? AND conversation=? "
                        "AND turn=?",
                        key[:3],
                    ).fetchone()
                    if not found:
                        raise LookupError("Reference case missing")
                    row = json.loads(found[0])
                    row["outputs"][key[3]]["judgment"] = judgment
                    self.save(db, *key[:3], row)

            self.reporting.finish((key[0], key[1], str(key[2]) + "/" + key[3]), judgment, write)
            with self.lock:
                self.started += 1
                self.inflight.discard(key)
            self.jobs.task_done()
