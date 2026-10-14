"""Authenticated loopback control/feed client for benchmark processes, not applications."""

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx


class BackendClient:
    def __init__(self, connection: Path):
        config = json.loads(connection.read_text())
        self.url = config["url"].rstrip("/")
        if urlsplit(self.url).hostname not in {"127.0.0.1", "localhost"}:
            raise ValueError("Validation control requires a dedicated loopback backend")
        self.http = httpx.Client(
            base_url=self.url,
            trust_env=False,
            timeout=15,
            headers={"Authorization": "Bearer " + config["token"]},
        )

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.http.close()

    def get(self, path: str) -> dict:
        r = self.http.get(path)
        r.raise_for_status()
        return r.json()

    def post(self, path: str, body: dict) -> dict:
        r = self.http.post(path, json=body)
        r.raise_for_status()
        return r.json()

    def records(self, run_id: str) -> list[dict]:
        records, cursor = [], None
        while True:
            page = self.get(
                "/v1/dashboard?run_id="
                + run_id
                + ("&before=" + str(cursor) if cursor is not None else "")
            )
            records.extend(page["records"])
            cursor = page["next_cursor"]
            if cursor is None:
                return records


def source_options() -> dict:
    key = os.environ.get("OPENAI_API_KEY")
    if key:
        return {
            "api_key": key,
            **(
                {"base_url": os.environ["OPENAI_BASE_URL"]}
                if os.environ.get("OPENAI_BASE_URL")
                else {}
            ),
        }
    if os.environ.get("PORTKEY_API_KEY") and os.environ.get("PORTKEY_BASE_URL"):
        return {
            "api_key": os.environ["PORTKEY_API_KEY"],
            "base_url": os.environ["PORTKEY_BASE_URL"],
            "default_headers": {"x-portkey-api-key": os.environ["PORTKEY_API_KEY"]},
        }
    raise ValueError("Configure OPENAI_API_KEY or PORTKEY_API_KEY + PORTKEY_BASE_URL")
