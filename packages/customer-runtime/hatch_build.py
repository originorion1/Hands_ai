"""Package the exact reviewed customer runtime and its lineage dependency."""

from pathlib import Path

from hatchling.builders.hooks.plugin.interface import BuildHookInterface


class CustomBuildHook(BuildHookInterface):
    def initialize(self, version: str, build_data: dict) -> None:
        del version
        root = Path(self.root)
        source_root = root / "src" if (root / "src/deployment/customer").is_dir() else root.parent.parent
        customer = source_root / "deployment/customer"
        expected = {
            "__init__.py", "backup.py", "bounded_metadata.py", "catalog.py",
            "enrollment.py", "journey.py", "login.py", "metadata_approval.py",
            "metadata_connection.py",
            "oidc.py", "oidc_client.py", "portal.py",
            "portal_http.py", "service.py", "tenants.py",
        }
        if {path.name for path in customer.glob("*.py")} != expected:
            raise RuntimeError("customer runtime source set changed without review")
        paths = [source_root / "deployment/__init__.py"]
        paths.extend(customer / name for name in sorted(expected))
        paths.extend(customer / name for name in ("portal.html", "portal.css", "portal.js"))
        paths.extend((
            source_root / "deployment/release_candidate/__init__.py",
            source_root / "deployment/release_candidate/continuous_lineage.py",
        ))
        if any(not path.is_file() or path.is_symlink() for path in paths):
            raise RuntimeError("customer runtime source is unavailable")
        for path in paths:
            relative = path.relative_to(source_root)
            destination = Path("src") / relative if self.target_name == "sdist" else relative
            build_data["force_include"][str(path)] = str(destination)
