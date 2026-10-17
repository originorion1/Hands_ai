"""Keep vendor implementation and the historical pilot outside Kernel source."""

import ast
import hashlib
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_kernel_source_and_metadata_have_no_erpnext_dependency():
    for base in (ROOT / "src/orion", ROOT / "src/orion_http"):
        for source in base.rglob("*.py"):
            if source.name == "erpnext_pilot_metadata.py":
                connector_source = (
                    ROOT / "packages/erpnext-discovery/src/orion/discovery/erpnext_pilot_metadata.py"
                )
                assert source.is_symlink()
                assert source.resolve() == connector_source.resolve()
                assert hashlib.sha256(source.read_bytes()).hexdigest() == (
                    "febe5045ec990999260751c9cd1b7438d72c499325678c80ccf2a1f05519eb5c"
                )
                continue
            assert "erpnext" not in source.name.lower(), source
            for node in ast.walk(ast.parse(source.read_text())):
                if isinstance(node, ast.Import):
                    assert all("erpnext" not in item.name.lower() for item in node.names), source
                elif isinstance(node, ast.ImportFrom):
                    assert "erpnext" not in (node.module or "").lower(), source
                    assert all("erpnext" not in item.name.lower() for item in node.names), source
    assert not (ROOT / "src/orion/pilot").exists()
    assert not (ROOT / "src/orion/demo.py").exists()
    project = tomllib.loads((ROOT / "pyproject.toml").read_text())["project"]
    assert all("erpnext" not in value.lower() for value in project["dependencies"])
    assert "orion-runtime" not in project.get("scripts", {})


def test_historical_pilot_has_an_independent_distribution():
    project = tomllib.loads((ROOT / "packages/pilot-compat/pyproject.toml").read_text())[
        "project"
    ]
    assert project["name"] == "originorion-erpnext-pilot-compat"
    assert project["scripts"]["orion-runtime"] == "orion.pilot.deployment:main"
    assert "originorion-connector-erpnext-discovery==0.1.0" in project["dependencies"]
