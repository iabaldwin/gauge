"""Shared UI assets, bundled in distributions and read from source in development."""

from importlib.resources import files
from pathlib import Path

_SOURCE_ROOT = Path(__file__).resolve().parents[3]
_SOURCE_FILES = {
    "pixel-lab.js": "pixel-lab/pixel-lab.js",
    "pixel-lab.css": "pixel-lab/pixel-lab.css",
    "index.html": "gauge/dashboard/index.html",
    "app.js": "gauge/dashboard/app.js",
    "gauge-adapter.js": "gauge/dashboard/gauge-adapter.js",
    "dashboard.css": "gauge/dashboard/dashboard.css",
    "validation-summary.js": "gauge/dashboard/validation-summary.js",
    "performance-summary.js": "gauge/dashboard/performance-summary.js",
}


def asset(name: str):
    """Only known names can resolve; caller data never supplies arbitrary paths."""
    source = _SOURCE_FILES[name]
    bundled = files("gauge").joinpath("_assets", name)
    return bundled if bundled.is_file() else _SOURCE_ROOT / source
