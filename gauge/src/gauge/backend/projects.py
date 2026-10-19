"""Internal project namespaces. Segmentation only; no user authentication."""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from uuid import uuid4

from gauge.local.state import write_json


class ProjectRegistry:
    def __init__(self, directory: Path | None, default, factory):
        self.directory, self.default, self.factory = directory, default, factory
        self.lock = threading.Lock()
        self.backends: dict = {}
        if directory:
            directory.mkdir(parents=True, exist_ok=True)

    def list(self) -> list[dict]:
        if self.directory is None:
            return (
                [{"id": self.default.project_id, "name": Path(self.default.db).parent.parent.name}]
                if self.default.project_id
                else []
            )
        return [
            json.loads(p.read_text()) for p in sorted(self.directory.glob("project_*/project.json"))
        ]

    def create(self, data: dict) -> dict:
        if self.directory is None:
            raise ValueError("This service serves one local project")
        project_id = data.get("id") or "project_" + uuid4().hex
        if not isinstance(project_id, str) or not re.fullmatch(r"project_[0-9a-f]{32}", project_id):
            raise ValueError("Invalid project ID")
        name = data.get("name", "Project")
        if not isinstance(name, str) or not 1 <= len(name) <= 200:
            raise ValueError("Invalid project name")
        with self.lock:
            path = self.directory / project_id / "project.json"
            if path.exists():
                return json.loads(path.read_text())
            path.parent.mkdir(exist_ok=True)
            result = {"id": project_id, "name": name}
            write_json(path, result)
            return result

    def select(self, project_id: str | None):
        if not project_id:
            if self.directory is None:
                return self.default
            projects = self.list()
            if not projects:
                return self.default
            project_id = projects[0]["id"]
        if self.directory is None:
            if project_id != self.default.project_id:
                raise ValueError("Project not served by this backend")
            return self.default
        if not re.fullmatch(r"project_[0-9a-f]{32}", project_id):
            raise ValueError("Invalid project ID")
        with self.lock:
            path = self.directory / project_id
            if not (path / "project.json").exists():
                raise ValueError("Project not registered on this backend")
            if project_id not in self.backends:
                self.backends[project_id] = self.factory(str(path / "captures.sqlite"), project_id)
            return self.backends[project_id]
