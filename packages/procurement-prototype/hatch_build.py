"""Package only the reviewed procurement investigation adapter."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        del version
        root = Path(self.root)
        source_root = (
            root / "src" if (root / "src/deployment/intelligence").is_dir()
            else root.parent.parent
        )
        intelligence = source_root / "deployment/intelligence"
        expected = {"__init__.py", "procurement_report.py", "review_ledger.py"}
        if {path.name for path in intelligence.glob("*.py")} != expected:
            raise RuntimeError("procurement prototype source set changed without review")
        for name in sorted(expected):
            path = intelligence / name
            if not path.is_file() or path.is_symlink():
                raise RuntimeError("procurement prototype source is unavailable")
            relative = path.relative_to(source_root)
            destination = Path("src") / relative if self.target_name == "sdist" else relative
            build_data["force_include"][str(path)] = str(destination)
        rule = (source_root / "orion/business/procurement.py"
                if source_root == root / "src"
                else source_root / "src/orion/business/procurement.py")
        if not rule.is_file() or rule.is_symlink():
            raise RuntimeError("procurement rule source is unavailable")
        destination = (
            Path("src/orion/business/procurement.py") if self.target_name == "sdist"
            else Path("orion/business/procurement.py")
        )
        build_data["force_include"][str(rule)] = str(destination)
