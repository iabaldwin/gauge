"""Bundle the shared Pixel Lab assets without maintaining source copies."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version, build_data):
        if self.target_name != "wheel":
            return
        root = Path(self.root)
        library = root / "pixel-lab"  # Source distributions include the library here.
        if not library.is_dir():
            library = root.parent / "pixel-lab"
        for name in ("pixel-lab.js", "pixel-lab.css"):
            build_data["force_include"][str(library / name)] = "gauge/_assets/" + name
        for name in (
            "index.html",
            "app.js",
            "gauge-adapter.js",
            "dashboard.css",
            "validation-summary.js",
            "performance-summary.js",
        ):
            build_data["force_include"][str(root / "dashboard" / name)] = "gauge/_assets/" + name
