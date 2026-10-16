"""Build the connector from its one authoritative source file."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        del version
        root = Path(self.root)
        relative = Path("src/orion/discovery/erpnext_adapter.py")
        in_sdist = root / relative
        in_repository = root.parents[1] / relative
        source = in_sdist if in_sdist.is_file() else in_repository
        if not source.is_file() or source.is_symlink():
            raise RuntimeError("ERPNext discovery source is unavailable")
        destination = str(relative if self.target_name == "sdist" else Path(
            "orion/discovery/erpnext_adapter.py"
        ))
        build_data["force_include"][str(source)] = destination
