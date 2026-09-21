import hashlib
import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.verify_live_pilot_source_inventory import (
    load_inventory,
    main,
    revision_sources,
    verify_inventory_document,
)

ROOT = Path(__file__).parents[1]
INVENTORY_PATH = ROOT / "docs/00-architecture/LIVE_PILOT_SOURCE_INVENTORY.json"


def _entry(path: str, source: bytes) -> dict[str, object]:
    return {
        "file": path,
        "sha256": hashlib.sha256(source).hexdigest(),
        "symbols": ["kept"],
    }


def _inventory(entries: list[dict[str, object]]) -> dict[str, object]:
    return {
        "schema_version": 2,
        "inventory_revision": "a" * 40,
        "files": entries,
    }


def _git(repository: Path, *arguments: str) -> str:
    return subprocess.run(
        ["git", "-C", str(repository), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def _miniature_repository(tmp_path: Path) -> tuple[Path, str]:
    repository = tmp_path / "repository"
    source = b"def kept():\n    pass\n"
    (repository / "src/orion").mkdir(parents=True)
    (repository / "tools").mkdir()
    (repository / "src/orion/kept.py").write_bytes(source)
    (repository / "tools/check.py").write_bytes(source)
    _git(repository, "init", "-q")
    _git(repository, "config", "user.name", "Inventory Test")
    _git(repository, "config", "user.email", "inventory@example.test")
    _git(repository, "add", "src", "tools")
    _git(repository, "commit", "-q", "-m", "inventory snapshot")
    revision = _git(repository, "rev-parse", "HEAD")
    inventory = _inventory([
        _entry("src/orion/kept.py", source),
        _entry("tools/check.py", source),
    ])
    inventory["inventory_revision"] = revision
    inventory_path = repository / "inventory.json"
    inventory_path.write_text(json.dumps(inventory), encoding="utf-8")
    return repository, revision


def test_inventory_matches_declared_revision_when_object_is_available():
    inventory = load_inventory(INVENTORY_PATH)
    try:
        resolved, sources = revision_sources(ROOT, inventory["inventory_revision"])
    except subprocess.CalledProcessError:
        pytest.skip("declared historical object is unavailable in this shallow checkout")

    assert resolved == inventory["inventory_revision"]
    assert len(sources) == 104
    assert verify_inventory_document(inventory, sources) == []


def test_default_refuses_descendant_and_exact_revision_remains_authoritative(
    tmp_path, capsys
):
    repository, revision = _miniature_repository(tmp_path)
    arguments = ["--repository", str(repository), "--inventory", "inventory.json"]

    assert main(arguments) == 0
    assert "working-tree" in capsys.readouterr().out

    added = repository / "src/orion/later.py"
    added.write_text("class Later:\n    pass\n", encoding="utf-8")
    _git(repository, "add", "src/orion/later.py")
    _git(repository, "commit", "-q", "-m", "legitimate descendant")

    assert main(arguments) == 1
    error = capsys.readouterr().err
    assert "descendant trees are not historical inventory failures" in error
    assert f"--revision {revision}" in error

    assert main([*arguments, "--revision", revision]) == 0
    assert f"revision:{revision}" in capsys.readouterr().out


def test_verifier_reports_an_omitted_tracked_source():
    source = b"def kept():\n    pass\n"
    inventory = _inventory([_entry("src/orion/kept.py", source)])
    sources = {
        "src/orion/kept.py": source,
        "tools/omitted.py": b"class Omitted:\n    pass\n",
    }

    assert verify_inventory_document(inventory, sources) == [
        "omitted tracked source: tools/omitted.py"
    ]


def test_verifier_reports_stale_blob_digest():
    original = b"def kept():\n    pass\n"
    changed = b"def kept():\n    return 1\n"
    inventory = _inventory([_entry("src/orion/kept.py", original)])

    assert verify_inventory_document(
        inventory, {"src/orion/kept.py": changed}
    ) == ["stale sha256: src/orion/kept.py"]


def test_verifier_reports_duplicate_inventory_paths():
    source = b"def kept():\n    pass\n"
    entry = _entry("src/orion/kept.py", source)
    inventory = _inventory([entry, deepcopy(entry)])

    assert verify_inventory_document(
        inventory, {"src/orion/kept.py": source}
    ) == ["duplicate inventory path: src/orion/kept.py"]


def test_verifier_reports_top_level_symbol_drift_even_when_digest_matches():
    source = b"def actual():\n    pass\n"
    entry = _entry("tools/check.py", source)
    entry["symbols"] = ["declared"]
    inventory = _inventory([entry])

    assert verify_inventory_document(inventory, {"tools/check.py": source}) == [
        "top-level symbol drift: tools/check.py"
    ]


def test_inventory_document_has_explicit_non_review_semantics():
    inventory = json.loads(INVENTORY_PATH.read_text(encoding="utf-8"))

    assert inventory["inventory_coverage"]["attests"] == [
        "selected_tracked_path_completeness",
        "sha256_blob_identity",
        "top_level_definition_inventory",
    ]
    assert inventory["review_coverage"]["attests_file_level_review"] is False
    assert inventory["review_coverage"]["independent_exact_head_review_required"] is True
    assert "exact snapshot" in inventory["verification"]["working_tree_scope"]
    assert "fixture" in inventory["verification"]["descendant_shallow_ci"]


@pytest.mark.parametrize("revision", [None, "short"])
def test_verifier_rejects_missing_or_non_full_inventory_revision(revision):
    source = b"def kept():\n    pass\n"
    inventory = _inventory([_entry("src/orion/kept.py", source)])
    inventory["inventory_revision"] = revision

    assert verify_inventory_document(
        inventory, {"src/orion/kept.py": source}
    )[0] == "inventory_revision must be a full 40-character commit id"
