"""Build the first Gauge distributions and verify standalone bundled assets."""

from __future__ import annotations

import argparse
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=root / "dist")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    subprocess.run(
        [
            sys.executable,
            "-m",
            "build",
            str(root / "gauge"),
            "--outdir",
            str(args.output.resolve()),
        ],
        check=True,
    )
    wheel = max(args.output.glob("gauge-*.whl"), key=lambda p: p.stat().st_mtime)
    with zipfile.ZipFile(wheel) as archive:
        names = set(archive.namelist())
        for asset in (
            "pixel-lab.js",
            "pixel-lab.css",
            "index.html",
            "app.js",
            "gauge-adapter.js",
        ):
            if "gauge/_assets/" + asset not in names:
                raise RuntimeError("Missing bundled UI asset: " + asset)
        entry = next(
            name for name in names if name.endswith(".dist-info/entry_points.txt")
        )
        entrypoints = archive.read(entry).decode()
        if "pytest11" not in entrypoints or "gauge.pytest_plugin" not in entrypoints:
            raise RuntimeError("Missing automatic test-runner integration")
    source = max(args.output.glob("gauge-*.tar.gz"), key=lambda p: p.stat().st_mtime)
    with tarfile.open(source) as archive:
        if not any(
            name.endswith("pixel-lab/pixel-lab.js") for name in archive.getnames()
        ):
            raise RuntimeError("Source distribution lacks Pixel Lab")
    print(f"Verified wheel: {wheel}\nVerified source distribution: {source}")


if __name__ == "__main__":
    main()
