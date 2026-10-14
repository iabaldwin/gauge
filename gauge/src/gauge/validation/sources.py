"""Pinned public files, byte-level provenance, bounded downloads, and offline caching."""

from __future__ import annotations

import hashlib
import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass(frozen=True)
class Source:
    name: str
    url: str
    revision: str
    split: str
    license: str
    attribution: str


BANKING = Source(
    "banking77",
    "https://raw.githubusercontent.com/PolyAI-LDN/task-specific-datasets/"
    "57ec275d8078af65b7731c2a98be812d844a6d6b/banking_data/test.csv",
    "57ec275d8078af65b7731c2a98be812d844a6d6b",
    "test",
    "CC-BY-4.0",
    "Casanueva et al., Efficient Intent Detection with Dual Sentence Encoders (2020)",
)
MULTIWOZ = Source(
    "multiwoz22",
    "https://raw.githubusercontent.com/budzianowski/multiwoz/"
    "fe0c8e65cfcd8462bd33c86e35f21addc84ca82b/data/MultiWOZ_2.2/test/dialogues_001.json",
    "fe0c8e65cfcd8462bd33c86e35f21addc84ca82b",
    "test/shard-001",
    "MIT",
    "Zang et al., MultiWOZ 2.2 (2020); Budzianowski et al. (2018)",
)
SQUAD = Source(
    "squad2",
    "https://huggingface.co/datasets/rajpurkar/squad_v2/resolve/"
    "3ffb306f725f7d2ce8394bc1873b24868140c412/squad_v2/validation-00000-of-00001.parquet",
    "3ffb306f725f7d2ce8394bc1873b24868140c412",
    "validation",
    "CC-BY-SA-4.0",
    "Rajpurkar et al., Know What You Don't Know: Unanswerable Questions for SQuAD (2018)",
)


def fetch(source: Source, cache: Path) -> tuple[Path, dict]:
    """A cache hit is checked against its receipt; corrupt bytes are never silently replaced."""
    import httpx

    cache.mkdir(parents=True, exist_ok=True)
    key = hashlib.sha256(source.url.encode()).hexdigest()[:20]
    path, receipt = cache / key, cache / (key + ".json")
    if path.exists() != receipt.exists():
        raise ValueError(f"Incomplete dataset cache entry: {path}")
    if not path.exists():
        temporary = path.with_suffix(".partial")
        try:
            with httpx.Client(
                proxy=os.environ.get("HTTPS_PROXY"),
                trust_env=False,
                timeout=60,
                follow_redirects=True,
            ) as client:
                with client.stream("GET", source.url) as response:
                    response.raise_for_status()
                    size = 0
                    with temporary.open("wb") as out:
                        for chunk in response.iter_bytes():
                            size += len(chunk)
                            if size > 64 * 1024 * 1024:
                                raise ValueError("Dataset file exceeds 64 MiB limit")
                            out.write(chunk)
            raw = temporary.read_bytes()
            info = {**asdict(source), "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw)}
            temporary.replace(path)
            receipt.write_text(json.dumps(info, indent=2) + "\n")
        finally:
            temporary.unlink(missing_ok=True)
    info = json.loads(receipt.read_text())
    if info["url"] != source.url or hashlib.sha256(path.read_bytes()).hexdigest() != info["sha256"]:
        raise ValueError(f"Dataset cache checksum mismatch: {path}")
    return path, info
