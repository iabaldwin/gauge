"""Project discovery and atomic first-use identity, used off the inference path."""

from __future__ import annotations

import json
import os
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import uuid4


def project_root(start: Path | None = None) -> Path:
    if start is None and os.environ.get("GAUGE_PROJECT_DIR"):
        return Path(os.environ["GAUGE_PROJECT_DIR"]).resolve()
    start = (start or Path.cwd()).resolve()
    for path in (start, *start.parents):
        if (path / ".gauge/project.json").is_file():
            return path
    for path in (start, *start.parents):
        if (path / "pyproject.toml").is_file() or (path / ".git").exists():
            return path
    return start


@contextmanager
def locked(path: Path):
    """Serialize CLI/worker bootstrap without publishing partially written files."""
    import fcntl

    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, "a") as file:
        deadline = time.monotonic() + 5
        while True:
            try:
                fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    raise TimeoutError("Gauge project initialization is busy") from None
                time.sleep(0.01)
        try:
            yield
        finally:
            fcntl.flock(file, fcntl.LOCK_UN)


def write_json(path: Path, payload: dict) -> None:
    temporary = path.with_name(path.name + "." + uuid4().hex + ".tmp")
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, "w") as file:
            json.dump(payload, file, indent=2)
            file.write("\n")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def bootstrap(root: Path | None = None) -> dict:
    root = project_root(root)
    folder = root / ".gauge"
    folder.mkdir(parents=True, exist_ok=True, mode=0o700)
    with locked(folder / "project.lock"):
        path = folder / "project.json"
        if path.exists():
            config = json.loads(path.read_text())
            if config.get("version") != 1 or not str(config.get("project_id", "")).startswith(
                "project_"
            ):
                raise ValueError("Invalid Gauge project state")
        else:
            config = {
                "version": 1,
                "project_id": "project_" + uuid4().hex,
                "name": root.name,
                "created_at": time.time(),
            }
            write_json(path, config)
        # Relative storage paths survive moving/renaming the containing project.
        return {**config, "root": str(root), "database": str(folder / "captures.sqlite")}


def installation_id() -> str:
    home = Path(os.environ.get("GAUGE_HOME", str(Path.home() / ".local/share/gauge")))
    home.mkdir(parents=True, exist_ok=True, mode=0o700)
    with locked(home / "installation.lock"):
        path = home / "installation.json"
        if path.exists():
            value = json.loads(path.read_text())["installation_id"]
            if not isinstance(value, str) or not value.startswith("installation_"):
                raise ValueError("Invalid Gauge installation state")
            return value
        value = "installation_" + uuid4().hex
        write_json(path, {"installation_id": value})
        return value
