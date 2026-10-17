"""Fixed synthetic benchmark, without customer data or live capabilities."""

import json
from pathlib import Path

from evaluation.intelligence_benchmark_230.evaluate import main
from evaluation.intelligence_benchmark_230.generate import generate

ROOT = Path(__file__).resolve().parents[1] / "evaluation/intelligence_benchmark_230"


def test_frozen_synthetic_fixture_matches_generator():
    events, labels = generate()
    assert json.loads((ROOT / "events.json").read_text()) == events
    assert json.loads((ROOT / "ground_truth.json").read_text()) == labels
    assert len({item["tenant"] for item in events["events"]}) == 2
    assert len({item["period"] for item in events["events"]}) == 3
    assert len({item["category"] for item in events["events"]}) == 3


def test_benchmark_reports_limits_and_denials():
    result = main()
    assert result["conventional"]["tp"] == 8
    assert result["conventional"]["fp"] == 1
    assert result["orion_actual_missing_document_only"]["tp"] == 1
    assert result["incremental_orion_validated_discoveries"] == 0
    assert result["cross_tenant_denied"]
    assert result["missing_authorization_denied"]
    assert result["heldout_learning_improvement"] is None
    assert result["independently_verified_avoidable_usd"] == 0
    assert result["execution_allowed"] is False
