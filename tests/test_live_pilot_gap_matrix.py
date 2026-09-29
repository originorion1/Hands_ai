import json
from pathlib import Path

MATRIX = Path(__file__).parents[1] / "docs/00-architecture/LIVE_PILOT_GAP_MATRIX.json"


def test_gap_matrix_retains_historical_audit_scope():
    matrix = json.loads(MATRIX.read_text(encoding="utf-8"))

    assert matrix["dependency_head"] == "2375ad61af37f8e13a6c59935051d614e2ead8cb"
    assert "historical" in matrix["scope"].lower()
    assert "not current-tree review coverage" in matrix["scope"]
    assert "Current stacked laboratory tree" not in matrix["scope"]
    assert matrix["verdict"] == "NOT_LIVE_PILOT_READY"
