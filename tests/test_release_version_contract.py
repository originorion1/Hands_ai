"""Keep the offline release train and its deployed read-only surface aligned."""

import tomllib
from pathlib import Path

from deployment.vercel.status import STATUS
from orion import __version__

ROOT = Path(__file__).resolve().parents[1]
VERSION = "0.2.0rc1"


def test_release_train_versions_and_internal_dependency_pins():
    projects = {
        ROOT / "pyproject.toml": ("originorion-core", VERSION),
        ROOT / "packages/connector-sdk/pyproject.toml": ("originorion-connector-sdk", VERSION),
        ROOT / "packages/erpnext-discovery/pyproject.toml": ("originorion-connector-erpnext-discovery", "0.2.0rc2"),
        ROOT / "packages/synthetic-connector/pyproject.toml": ("originorion-connector-synthetic", VERSION),
        ROOT / "packages/pilot-compat/pyproject.toml": ("originorion-erpnext-pilot-compat", "0.2.0rc2"),
    }
    versions = dict(projects.values())
    for path, (name, version) in projects.items():
        project = tomllib.loads(path.read_text())["project"]
        assert project["name"] == name
        assert project["version"] == version, path
        for dependency in project.get("dependencies", []):
            if dependency.startswith("originorion-"):
                dependency_name, dependency_version = dependency.split("==", 1)
                assert dependency_version == versions[dependency_name], (path, dependency)


def test_command_center_and_status_expose_same_release_version():
    html = (ROOT / "src/orion/presentation/static/index.html").read_text()
    assert f'<meta name="orion-release" content="{VERSION}">' in html
    assert STATUS["release_version"] == __version__ == VERSION
    assert STATUS["interface_version"] == "orion.deployment-status.v1"
    assert STATUS["execution_allowed"] is False
