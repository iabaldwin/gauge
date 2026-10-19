"""Thin command routing; preparation and offline checks never invoke providers."""

import argparse
import asyncio
import json
import os
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    prepare = commands.add_parser("prepare", help="Cache pinned datasets and build a manifest")
    prepare.add_argument("--profile", choices=["smoke", "full"], default="smoke")
    prepare.add_argument("--output", type=Path, default=Path("out/validation-smoke.manifest.json"))
    prepare.add_argument("--cache", type=Path, default=Path("data/validation-cache"))
    prepare.add_argument("--seed", type=int, default=42)
    prepare.add_argument(
        "--fixed-prefix",
        action="store_true",
        help="Evaluate each turn with recorded assistant history",
    )
    check = commands.add_parser("check", help="Offline correlation checks on real dataset fixtures")
    check.add_argument("manifest", type=Path)
    check.add_argument("--output", type=Path, default=Path("out/validation-offline.json"))
    live = commands.add_parser("run", help="Paid source calls and separate backend comparisons")
    live.add_argument("manifest", type=Path)
    live.add_argument("--output", type=Path, default=Path("out/validation-live.json"))
    live.add_argument("--connection", type=Path, default=Path(".gauge/service.json"))
    live.add_argument(
        "--source-model",
        default="gpt-4.1-nano"
        if os.environ.get("OPENAI_API_KEY")
        else "@openai-foundations/gpt-5.4-mini",
    )
    live.add_argument("--concurrency", type=int, default=4)
    live.add_argument("--wait-seconds", type=int, default=1200)
    args = parser.parse_args()
    if args.command == "prepare":
        from gauge.validation.suite import prepare

        result = prepare(args.output, args.cache, args.profile, args.seed, args.fixed_prefix)
        print(json.dumps({k: result[k] for k in ("profile", "source_calls", "sha256")}))
    elif args.command == "check":
        from gauge.validation.checks import offline
        from gauge.validation.records import read_suite

        _, scenarios = read_suite(args.manifest)
        result = offline(scenarios)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        print(json.dumps({k: v for k, v in result.items() if k != "graph"}, indent=2))
        if not result["passed"]:
            raise SystemExit(1)
    else:
        from gauge.validation.runner import run

        result = asyncio.run(
            run(
                args.manifest,
                args.output,
                args.connection,
                args.source_model,
                args.concurrency,
                args.wait_seconds,
            )
        )
        print(
            json.dumps(
                {
                    "run_id": result.get("run_id"),
                    "checks": result["checks"],
                    "report": str(args.output),
                    "dashboard": result.get("dashboard"),
                },
                indent=2,
            )
        )
        if not result["checks"] or not all(result["checks"].values()):
            raise SystemExit(1)


if __name__ == "__main__":
    main()
