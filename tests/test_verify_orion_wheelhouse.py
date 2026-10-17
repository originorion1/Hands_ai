"""A reviewed offline wheelhouse must not substitute or mutate distributions."""

import hashlib
import json
import zipfile

import pytest

from scripts.verify_orion_wheelhouse import verify


def wheel(directory, name):
    filename = f"{name.replace('-', '_')}-0.1.0-py3-none-any.whl"
    path = directory / filename
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr(
            f"{name.replace('-', '_')}-0.1.0.dist-info/METADATA",
            f"Metadata-Version: 2.3\nName: {name}\nVersion: 0.1.0\n",
        )
    return {"file": filename, "name": name, "version": "0.1.0",
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def manifest(directory, records):
    path = directory.parent / "manifest.json"
    path.write_text(json.dumps({"schema": "orion.offline-wheelhouse.v1", "wheels": records}))
    return path


def test_exact_wheelhouse_accepted(tmp_path):
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    records = [wheel(wheels, "originorion-core"), wheel(wheels, "originorion-connector-sdk")]
    requirements = verify(wheels, manifest(wheels, records))
    assert len(requirements) == 2
    assert all("--hash=sha256:" in item for item in requirements)


@pytest.mark.parametrize("failure", [
    "tamper", "extra", "substitute", "symlink", "manifest_lists_public_collision",
])
def test_wheelhouse_rejects_substitution(tmp_path, failure):
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    records = [wheel(wheels, "originorion-core"), wheel(wheels, "originorion-connector-sdk")]
    reviewed = manifest(wheels, records)
    if failure == "tamper":
        (wheels / records[0]["file"]).write_bytes(b"unrelated public package")
    elif failure == "extra":
        wheel(wheels, "orion-core")
    elif failure == "substitute":
        replacement = wheel(wheels, "orion-core")
        original = wheels / records[0]["file"]
        original.unlink()
        (wheels / replacement["file"]).rename(original)
        records[0]["sha256"] = hashlib.sha256(original.read_bytes()).hexdigest()
        reviewed = manifest(wheels, records)
    elif failure == "symlink":
        original = wheels / records[0]["file"]
        target = tmp_path / "outside.whl"
        target.write_bytes(original.read_bytes())
        original.unlink()
        original.symlink_to(target)
    elif failure == "manifest_lists_public_collision":
        records.append(wheel(wheels, "orion-core"))
        reviewed = manifest(wheels, records)
    with pytest.raises(ValueError):
        verify(wheels, reviewed)
