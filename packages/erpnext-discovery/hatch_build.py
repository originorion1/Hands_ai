"""Give the connector sole artifact ownership of its vendor-specific modules."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        del version
        root = Path(self.root)
        source_root = root / "src"
        sources = sorted((source_root / "orion/discovery").glob("erpnext_*.py"))
        sources.extend(source_root / name for name in (
            "orion/discovery/retained_investigation_session.py",
            "orion/pilot/erpnext_candidate.py",
            "orion/presentation/report_interface.py",
            "orion_http/report_server.py",
        ))
        if len(sources) != 25 or any(not file.is_file() or file.is_symlink() for file in sources):
            raise RuntimeError("ERPNext connector sources are unavailable")
        for source in sources:
            relative = source.relative_to(source_root)
            destination = Path("src") / relative if self.target_name == "sdist" else relative
            build_data["force_include"][str(source)] = str(destination)
