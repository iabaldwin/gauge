"""Matched A/B scheduling and explicit performance denominators."""

from __future__ import annotations

import random
import statistics


def schedule(calls: int, batch_size: int, seed: int) -> list[dict]:
    rng = random.Random(seed)
    batches = []
    for start in range(0, calls, batch_size):
        order = ["native", "gauge"]
        rng.shuffle(order)
        batches.append(
            {"indices": list(range(start, min(calls, start + batch_size))), "order": order}
        )
    return batches


def quantile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low = int(position)
    high = min(low + 1, len(ordered) - 1)
    return ordered[low] + (ordered[high] - ordered[low]) * (position - low)


def summarize(samples: list[dict], batches: list[dict], planned: int) -> dict:
    arms = {}
    for arm in ("native", "gauge"):
        rows = [r for r in samples if r["arm"] == arm]
        completed = [r for r in rows if r["status"] == "completed"]
        successful = [r for r in completed if r["finish_reason"] == "stop"]
        values = [r["latency_ms"] for r in successful]
        elapsed = sum(b["wall_seconds"] for b in batches if b["arm"] == arm)
        arms[arm] = {
            "calls": len(rows),
            "planned": planned,
            "successful": len(successful),
            "errors": len(rows) - len(completed),
            "incomplete": len(completed) - len(successful),
            "latencies_ms": values,
            "attempt_latencies_ms": [r["latency_ms"] for r in rows],
            "p50_ms": quantile(values, 0.5),
            "p95_ms": quantile(values, 0.95),
            "p99_ms": quantile(values, 0.99),
            "active_seconds": elapsed,
            "throughput": len(successful) / elapsed if elapsed else None,
        }
    delta = {}
    for metric in ("p50_ms", "p95_ms", "p99_ms", "throughput"):
        a, b = arms["native"][metric], arms["gauge"][metric]
        delta[metric] = {
            "absolute": b - a if a is not None and b is not None else None,
            "percent": (b / a - 1) * 100 if a and b is not None else None,
        }
    pairs: dict[int, dict[str, float]] = {}
    for row in samples:
        if row["status"] == "completed" and row["finish_reason"] == "stop":
            pairs.setdefault(row["pair"], {})[row["arm"]] = row["latency_ms"]
    differences = [p["gauge"] - p["native"] for p in pairs.values() if len(p) == 2]
    return {
        "arms": arms,
        "delta": delta,
        "matched_pairs": len(differences),
        "paired_mean_delta_ms": statistics.mean(differences) if differences else None,
    }
