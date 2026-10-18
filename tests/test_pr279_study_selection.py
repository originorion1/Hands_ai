"""Domain-neutral, equal-access study selection and evidence contracts."""

import json
from pathlib import Path

import pytest

from evaluation.pr279_study_selection.benchmark import (
    _conclude,
    _observation,
    run_benchmark,
)
from evaluation.pr279_study_selection.score import score

ROOT = Path(__file__).resolve().parents[1] / "evaluation/pr279_study_selection"


def test_three_domain_study_comparison_is_blind_and_reproducible(tmp_path):
    first = run_benchmark(ROOT / "cases.json")
    second = run_benchmark(ROOT / "cases.json")
    assert first == second
    assert first["labels_available_to_selector"] is False
    assert {row["domain"] for row in first["predictions"]} == {
        "procurement", "maintenance", "inventory",
    }
    assert len(first["predictions"]) == 12 * 4
    assert all(row["requests"] == [
        ("metadata", row["selected_target"]),
        ("record", row["selected_target"]),
    ] for row in first["predictions"])
    assert all(row["read_only"] and row["execution_allowed"] is False
               for row in first["predictions"])
    assert any(row["method"] == "orion" and row["conclusion"]["status"] == "abstained"
               and "reference unit cost" in row["conclusion"]["missing_evidence"]
               for row in first["predictions"])

    path = tmp_path / "sealed.json"
    path.write_text(json.dumps(first, sort_keys=True))
    result = score(path, ROOT / "labels.json", ROOT / "cases.json")
    assert result["case_count"] == 12 and result["domain_count"] == 3
    assert result["designed_positive_count"] == 9
    assert result["scores"]["fixed"]["tp"] == 6
    assert result["scores"]["orion"]["tp"] == 3
    assert result["scores"]["orion"]["fp"] == 0
    assert result["scores"]["orion"]["evidence_requests"] == 24
    assert result["incremental_value_verified"] is False


def test_study_conclusion_abstains_on_tampered_record_digest():
    case = json.loads((ROOT / "cases.json").read_text())["cases"][1]
    target = "Purchase Bill"
    observation = _observation(case, target, metadata=False)
    observation.evidence.payload["record"]["observed_unit_cost"] = "900.00"
    conclusion = _conclude(case, observation)
    assert conclusion["status"] == "abstained"
    assert conclusion["estimated_exposure_usd"] is None
    assert conclusion["missing_evidence"] == ["valid source record"]


def test_score_rejects_predictions_with_missing_case(tmp_path):
    predictions = run_benchmark(ROOT / "cases.json")
    predictions["predictions"].pop()
    path = tmp_path / "partial.json"
    path.write_text(json.dumps(predictions))
    with pytest.raises(ValueError, match="missing or duplicate"):
        score(path, ROOT / "labels.json", ROOT / "cases.json")
