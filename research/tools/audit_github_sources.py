"""Fetch pinned source/tree evidence via the governed gh CLI, never execute it."""
import argparse
import base64
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from _cli import cli

ROOT = Path(__file__).resolve().parents[2] / "data/doordash-llm-audit"


def save(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2) + "\n")


def tree(repo):
    folder = ROOT / "repos" / repo
    if (folder / "tree.json").exists():
        return
    head = cli("gh", "api", f"repos/doordash/{repo}/commits/HEAD", "--jq",
               "{sha,tree:.commit.tree.sha,date:.commit.committer.date}")
    save(folder / "head.json", head)
    data = cli("gh", "api", f"repos/doordash/{repo}/git/trees/{head['tree']}?recursive=1")
    save(folder / "tree.json", data)
    print(repo, len(data.get("tree", [])), "truncated", data.get("truncated"), flush=True)


def source(repo, path):
    folder = ROOT / "repos" / repo
    filename = path.replace("/", "__")
    target = folder / "files" / filename
    if target.exists():
        return
    head = json.loads((folder / "head.json").read_text())
    data = cli("gh", "api", f"repos/doordash/{repo}/contents/{quote(path)}?ref={head['sha']}")
    if data.get("encoding") != "base64":
        raise ValueError(f"Unsupported encoding/oversized source: {repo}/{path}")
    content = base64.b64decode(data["content"])
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(content)
    save(folder / "evidence" / f"{filename}.json", {
        "repository": f"doordash/{repo}", "commit": head["sha"], "path": path,
        "url": f"https://github.com/doordash/{repo}/blob/{head['sha']}/{quote(path)}",
        "sha256": hashlib.sha256(content).hexdigest(),
        "retrieved_at": datetime.now(timezone.utc).isoformat(), "bytes": len(content),
    })
    print(repo, path, len(content), flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("mode", choices=["trees", "files"])
    parser.add_argument("items", nargs="+")
    args = parser.parse_args()
    for item in args.items:
        if args.mode == "trees":
            tree(item)
        else:
            repo, path = item.split(":", 1)
            source(repo, path)
