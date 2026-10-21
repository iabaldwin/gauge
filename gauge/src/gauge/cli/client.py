"""Small internal backend client shared by terminal commands and the TUI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx


class ConsoleError(RuntimeError):
    pass


class ConsoleClient:
    def __init__(self, connection: Path | None = None):
        try:
            if connection is None:
                from gauge.local.runtime import ensure_service
                from gauge.local.state import project_root

                remote = project_root() / ".gauge/remote.json"
                config = json.loads(remote.read_text()) if remote.exists() else ensure_service()
            else:
                config = json.loads(connection.read_text())
            self.project_id = config.get("project_id")
            self.url = config["url"].rstrip("/")
            parsed = urlsplit(self.url)
            if (
                parsed.scheme not in {"http", "https"}
                or not parsed.hostname
                or parsed.username
                or parsed.password
            ):
                raise ValueError("Invalid internal backend URL")
            self.http = httpx.Client(
                base_url=self.url,
                trust_env=False,
                timeout=10,
            )
        except (OSError, KeyError, ValueError, RuntimeError) as exc:
            raise ConsoleError(f"Cannot load connection file: {connection}") from exc

    def close(self) -> None:
        self.http.close()

    def request(self, method: str, path: str, body: dict | None = None) -> dict:
        try:
            separator = "&" if "?" in path else "?"
            if self.project_id:
                path += separator + urlencode({"project_id": self.project_id})
            r = self.http.request(method, path, json=body)
            if not r.is_success:
                try:
                    detail = r.json().get("error", "Request failed")
                except ValueError:
                    detail = "Request failed"
                raise ConsoleError(f"HTTP {r.status_code}: {detail}")
            return r.json()
        except httpx.HTTPError as exc:
            raise ConsoleError("Backend unreachable; check that it is running") from exc

    def snapshot(self, run_id: str | None = None) -> dict:
        runs = self.request("GET", "/v1/runs")["runs"]
        stats = self.request("GET", "/stats")
        return {"runs": runs, "stats": stats}

    def records(self, run_id: str | None) -> list[dict]:
        records, cursor = [], None
        while True:
            query = {"run_id": run_id} if run_id else {}
            if cursor is not None:
                query["before"] = str(cursor)
            page = self.request("GET", "/v1/dashboard?" + urlencode(query))
            records.extend(page["records"])
            cursor = page["next_cursor"]
            if cursor is None:
                return records

    def preview(self, run_ids: list[str] | None) -> dict:
        return self.request(
            "POST",
            "/v1/runs/clear/preview",
            {"run_ids": run_ids} if run_ids is not None else {"all": True},
        )

    def clear(self, preview: dict) -> dict:
        return self.request(
            "POST",
            "/v1/runs/clear",
            {
                "run_ids": preview["run_ids"],
                "fingerprint": preview["fingerprint"],
            },
        )

    def judge(self, run_id: str, limit: int = 30) -> dict:
        return self.request("POST", "/v1/judgments", {"run_id": run_id, "limit": limit})

    def dashboard(self, run_id: str | None = None) -> str:
        if run_id is None:
            runs = self.request("GET", "/v1/runs")["runs"]
            run_id = runs[0]["id"] if runs else None
        query = {}
        if run_id:
            query["run"] = run_id
        if self.project_id:
            query["project_id"] = self.project_id
        return self.url + "/dashboard" + ("?" + urlencode(query) if query else "")


def export_run(client: ConsoleClient, run_id: str, output: Path) -> Path:
    runs = client.request("GET", "/v1/runs")["runs"]
    run = next((r for r in runs if r["id"] == run_id), None)
    if run is None:
        raise ConsoleError("Selected run no longer exists")
    payload = {"schema_version": 1, "run": run, "records": client.records(run_id)}
    output.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(output, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as file:
        json.dump(payload, file, indent=2)
        file.write("\n")
    return output.resolve()
