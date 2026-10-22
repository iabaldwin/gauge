"""Plain, versioned fixture records. Oracle labels never enter Gauge capture metadata."""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class Scenario:
    id: str
    dataset: str
    task: str
    protocol: str
    instructions: str
    turns: list[dict[str, Any]]
    expected: Any = None
    provenance: dict[str, Any] = field(default_factory=dict)
    history_mode: str = "recorded"


def digest(value: Any) -> str:
    return hashlib.sha256(
        json.dumps(value, sort_keys=True, ensure_ascii=False).encode()
    ).hexdigest()


def write_suite(
    path: Path,
    scenarios: list[Scenario],
    sources: list[dict],
    profile: str,
    dispatch_mode: str = "protocols",
) -> dict:
    rows = [asdict(s) for s in scenarios]
    if len({s.id for s in scenarios}) != len(scenarios):
        raise ValueError("Scenario IDs must be unique")
    manifest = {
        "schema_version": 1,
        "profile": profile,
        "dispatch_mode": dispatch_mode,
        "sources": sources,
        "scenarios": rows,
        "source_calls": sum(len(s.turns) for s in scenarios),
    }
    manifest["sha256"] = digest(manifest)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
    return manifest


def read_suite(path: Path) -> tuple[dict, list[Scenario]]:
    data = json.loads(path.read_text())
    expected = data.pop("sha256")
    if data.get("schema_version") != 1 or digest(data) != expected:
        raise ValueError("Unsupported or modified fixture manifest")
    data["sha256"] = expected
    scenarios = [Scenario(**s) for s in data["scenarios"]]
    if data["source_calls"] != sum(len(s.turns) for s in scenarios):
        raise ValueError("Fixture call count mismatch")
    return data, scenarios
