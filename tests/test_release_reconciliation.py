"""Synthetic deployment evidence updates reasons, never critical release status."""

import json
from pathlib import Path

from orion.pilot.readiness import release_report


def test_reconciliation_preserves_all_independent_critical_release_requirements():
    report = release_report()
    failures = {"SECURITY", "SECRETS"}
    blocked = {
        "EPISTEMIC_SAFETY",
        "AUTHORIZATION",
        "DISCOVERY",
        "SEMANTIC_UNDERSTANDING",
        "WORLD_MODEL",
        "PROVENANCE",
        "RESTART",
        "TRANSPORT",
        "TENANT_ISOLATION",
        "AUDIT",
        "OBSERVABILITY",
        "FAILURE_SAFETY",
        "DATA_MINIMIZATION",
        "COST_CONTROL",
        "ERP_GATEWAY",
        "TEST_COVERAGE",
        "DEPLOYMENT_CONFIGURATION",
    }
    expected = {**dict.fromkeys(failures, "FAIL"), **dict.fromkeys(blocked, "BLOCKED")}
    assert len(report["gates"]) == 19
    assert {g["category"]: g["status"] for g in report["gates"]} == expected
    assert all(g["critical"] is True and g["reason"] for g in report["gates"])
    assert report["live_ready"] is False
    assert report["execution_allowed"] is False
    assert report["customer_connections"] == 0


def test_documented_gate_result_matches_current_nonactivating_release_contract():
    root = Path(__file__).resolve().parents[1]
    documented = json.loads((root / "docs/00-architecture/LIVE_PILOT_GATE_RESULT.json").read_text())
    assert documented == release_report()
