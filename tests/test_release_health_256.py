import copy
import json
from pathlib import Path

import pytest

from scripts.render_release_health import render, validate

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "docs/release/0.2.0rc1/RELEASE_HEALTH.json"
MARKDOWN = SOURCE.with_suffix(".md")


def _record():
    return json.loads(SOURCE.read_text())


def test_markdown_matches_machine_readable_status():
    assert render(_record()) == MARKDOWN.read_text()


def test_open_p1_and_missing_security_review_deny_qualified_status():
    record = _record()
    record["release_decision"] = "RELEASE_CANDIDATE_QUALIFIED"
    with pytest.raises(ValueError, match="contradicts"):
        validate(record)


def test_missing_dimension_or_duplicate_blocker_denied():
    record = _record()
    del record["dimensions"]["SECURITY_HEALTH"]
    with pytest.raises(ValueError, match="dimension"):
        validate(record)
    record = _record()
    record["blockers"].append(copy.deepcopy(record["blockers"][0]))
    with pytest.raises(ValueError, match="duplicate"):
        validate(record)


def test_health_snapshot_cannot_grant_execution_or_customer_access():
    record = _record()
    record["execution_allowed"] = True
    with pytest.raises(ValueError, match="execution"):
        validate(record)
    record = _record()
    record["customer_erp_access"] = True
    with pytest.raises(ValueError, match="customer"):
        validate(record)
