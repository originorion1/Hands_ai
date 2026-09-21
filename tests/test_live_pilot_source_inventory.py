import hashlib
import json
from copy import deepcopy
from pathlib import Path

import pytest

from scripts.verify_live_pilot_source_inventory import (
    load_inventory,
    verify_inventory_document,
    working_tree_sources,
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


def test_checked_out_inventory_matches_every_selected_tracked_source():
    inventory = load_inventory(INVENTORY_PATH)
    sources = working_tree_sources(ROOT)

    assert len(sources) == 104
    assert verify_inventory_document(inventory, sources) == []


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


@pytest.mark.parametrize("revision", [None, "short"])
def test_verifier_rejects_missing_or_non_full_inventory_revision(revision):
    source = b"def kept():\n    pass\n"
    inventory = _inventory([_entry("src/orion/kept.py", source)])
    inventory["inventory_revision"] = revision

    assert verify_inventory_document(
        inventory, {"src/orion/kept.py": source}
    )[0] == "inventory_revision must be a full 40-character commit id"
