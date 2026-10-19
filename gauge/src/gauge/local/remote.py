"""Connect a local project to an internal shared service without creating an account."""

from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import httpx

from gauge.backend.store import DashboardStore
from gauge.local.state import bootstrap, write_json


def connect(url: str, project_id: str | None = None) -> dict:
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Provide an internal backend HTTP(S) URL without credentials or query")
    project = bootstrap()
    folder = Path(project["root"]) / ".gauge"
    from gauge.ingestion import CaptureInbox

    CaptureInbox(project["database"]).close()
    store = DashboardStore(project["database"], recover=False)
    if project_id and project_id != project["project_id"]:
        if store.page()["total"]:
            raise ValueError(
                "This local project has captures; connect it with its original project ID"
            )
        state = json.loads((folder / "project.json").read_text())
        state["project_id"] = project_id
        # Write the selected ID only after the remote service accepts it.
    else:
        state = None
        project_id = project["project_id"]
    url = url.rstrip("/")
    with httpx.Client(base_url=url, timeout=10, trust_env=False) as client:
        response = client.post("/v1/projects", json={"id": project_id, "name": project["name"]})
        response.raise_for_status()
        query = "?" + urlencode({"project_id": project_id})
        transferred, cursor = 0, None
        while True:
            page = store.page(before=cursor)
            for row in page["records"]:
                capture = row["capture"]
                sent = client.post(
                    "/v1/import" + query,
                    json=capture,
                    headers={"Idempotency-Key": capture["call_id"]},
                )
                sent.raise_for_status()
                transferred += 1
            cursor = page["next_cursor"]
            if cursor is None:
                break
        for run in store.runs():
            sent = client.post("/v1/runs/import" + query, json=run)
            sent.raise_for_status()
    if state:
        write_json(folder / "project.json", state)
    result = {"url": url, "project_id": project_id}
    write_json(folder / "remote.json", result)
    return {**result, "captures_uploaded": transferred}
