"""Gauge artifact tools and backend operator console."""

import argparse
import json
from pathlib import Path

from gauge.cli.commands import configure
from gauge.models import Report


def main() -> None:
    parser = argparse.ArgumentParser(prog="gauge")
    commands = parser.add_subparsers(dest="command", required=True)
    render = commands.add_parser("report", help="Render a saved JSON report as standalone HTML")
    render.add_argument("input", type=Path)
    render.add_argument("--output", type=Path)
    demo = commands.add_parser("demo", help="Render clearly labeled illustrative data")
    demo.add_argument("--output", type=Path, default=Path("gauge-comparison.html"))
    configure(commands)
    args = parser.parse_args()
    if args.command not in {"report", "demo"}:
        from gauge.cli.client import ConsoleError
        from gauge.cli.commands import run

        try:
            run(args)
        except (ConsoleError, OSError) as exc:
            parser.exit(1, f"gauge: {exc}\n")
        except KeyboardInterrupt:
            parser.exit(130, "\n")
        return
    if args.command == "report":
        report = Report.from_dict(json.loads(args.input.read_text(encoding="utf-8")))
        output = args.output or args.input.with_suffix(".html")
        if output.resolve() == args.input.resolve():
            parser.error("Output must differ from the input JSON report.")
    else:
        from gauge.demo import demo_report

        report, output = demo_report(), args.output
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(report.to_html(), encoding="utf-8")
    print(output.resolve())


if __name__ == "__main__":
    main()
