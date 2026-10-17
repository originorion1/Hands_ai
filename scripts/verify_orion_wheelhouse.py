"""Verify a reviewed wheel manifest before an offline ORION installation.

The manifest is a release artifact. Its hash must be reviewed and carried by the
release record; generating a new manifest is not a substitute for that review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import zipfile
from pathlib import Path

ORION_DISTRIBUTIONS = frozenset({
    "originorion-core",
    "originorion-connector-sdk",
    "originorion-connector-erpnext-discovery",
    "originorion-connector-synthetic",
    "originorion-erpnext-pilot-compat",
})
SHA256 = re.compile(r"[0-9a-f]{64}\Z")


def normalized(name: str) -> str:
    return re.sub(r"[-_.]+", "-", name).lower()


def wheel_identity(path: Path) -> tuple[str, str]:
    with zipfile.ZipFile(path) as archive:
        metadata_files = [name for name in archive.namelist() if name.endswith(".dist-info/METADATA")]
        if len(metadata_files) != 1:
            raise ValueError(f"wheel metadata count invalid: {path.name}")
        lines = archive.read(metadata_files[0]).decode("utf-8").splitlines()
    names = [line[6:].strip() for line in lines if line.startswith("Name: ")]
    versions = [line[9:].strip() for line in lines if line.startswith("Version: ")]
    if len(names) != 1 or len(versions) != 1:
        raise ValueError(f"wheel identity invalid: {path.name}")
    name, version = normalized(names[0]), versions[0]
    if not version or any(char in version for char in " /\\\n\r"):
        raise ValueError(f"wheel version invalid: {path.name}")
    return name, version


def verify(wheelhouse: Path, manifest: Path) -> list[str]:
    document = json.loads(manifest.read_text())
    if document.get("schema") != "orion.offline-wheelhouse.v1":
        raise ValueError("unsupported manifest schema")
    records = document.get("wheels")
    if not isinstance(records, list) or not records:
        raise ValueError("empty wheel manifest")
    actual = {path.name for path in wheelhouse.iterdir()}
    expected = {record["file"] for record in records}
    if actual != expected or len(expected) != len(records):
        raise ValueError("wheelhouse differs from reviewed manifest")
    requirements = []
    seen = set()
    orion = set()
    for record in records:
        filename, digest = record["file"], record["sha256"]
        if Path(filename).name != filename or not filename.endswith(".whl"):
            raise ValueError("invalid wheel filename")
        if not SHA256.fullmatch(digest):
            raise ValueError("invalid SHA-256 digest")
        wheel = wheelhouse / filename
        if wheel.is_symlink() or not wheel.is_file():
            raise ValueError(f"wheel file custody invalid: {filename}")
        if hashlib.sha256(wheel.read_bytes()).hexdigest() != digest:
            raise ValueError(f"wheel SHA-256 mismatch: {filename}")
        name, version = wheel_identity(wheel)
        if not filename.startswith(f"{name.replace('-', '_')}-{version}-"):
            raise ValueError(f"wheel filename identity mismatch: {filename}")
        if name != normalized(record["name"]) or version != record["version"]:
            raise ValueError(f"wheel identity mismatch: {filename}")
        if name in seen:
            raise ValueError(f"duplicate distribution: {name}")
        seen.add(name)
        if name.startswith("originorion-"):
            if name not in ORION_DISTRIBUTIONS:
                raise ValueError(f"unexpected ORION distribution: {name}")
            orion.add(name)
        requirements.append(f"{name}=={version} --hash=sha256:{digest}")
    if "originorion-core" not in orion or "originorion-connector-sdk" not in orion:
        raise ValueError("Kernel and SDK wheels required")
    return requirements


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("wheelhouse", type=Path)
    parser.add_argument("manifest", type=Path)
    parser.add_argument("--install", action="store_true")
    args = parser.parse_args()
    requirements = verify(args.wheelhouse, args.manifest)
    if args.install:
        with tempfile.TemporaryDirectory() as directory:
            requirements_file = Path(directory) / "requirements.txt"
            requirements_file.write_text("\n".join(requirements) + "\n")
            subprocess.run([
                sys.executable, "-m", "pip", "install", "--no-index", "--only-binary=:all:",
                "--require-hashes", "--find-links", str(args.wheelhouse), "-r",
                str(requirements_file),
            ], check=True)
            subprocess.run([sys.executable, "-m", "pip", "check"], check=True)
    print(json.dumps({"verified_wheels": len(requirements), "offline_only": True}))


if __name__ == "__main__":
    main()
