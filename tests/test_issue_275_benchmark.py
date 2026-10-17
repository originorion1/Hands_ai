"""Blind-label separation and equal-access three-way benchmark contracts."""

from __future__ import annotations

import json

from evaluation.issue_275 import benchmark
from evaluation.issue_275.blind_packet import packet


def test_three_way_holdout_has_equal_rows_and_no_incremental_claim():
    result = benchmark.evaluate_three_way()
    assert result["case_count"] == 12
    assert result["equal_access"]["rows"] == 48
    assert len(result["source_sha256_by_case"]) == 12
    conventional = result["scores"]["conventional_union"]
    existing = result["scores"]["existing_orion"]
    hybrid = result["scores"]["hybrid_investigation"]
    assert (conventional["tp"], conventional["fp"], conventional["fn"]) == (3, 6, 0)
    assert (existing["tp"], existing["fp"], existing["fn"]) == (3, 0, 0)
    assert (hybrid["tp"], hybrid["fp"], hybrid["fn"]) == (3, 0, 0)
    assert conventional["estimated_exposure_usd"] is None
    assert hybrid["estimated_exposure_usd"] == "190.00"
    assert result["incremental_designed_positive_conclusions"] == 0
    assert result["independently_adjudicated_incremental_conclusions"] is None
    assert all(score["measured_analyst_review_minutes"] is None
               for score in result["scores"].values())
    assert result["execution_allowed"] is False


def test_labels_change_scores_but_never_detector_status_or_source(monkeypatch, tmp_path):
    original = benchmark.evaluate_three_way()
    replacement = tmp_path / "labels.json"
    replacement.write_text(json.dumps({
        "schema": "test-labels", "positive_cases": ["holdout_l"],
    }))
    monkeypatch.setattr(benchmark, "LABELS", replacement)
    changed = benchmark.evaluate_three_way()
    assert changed["labels_sha256"] != original["labels_sha256"]
    assert changed["case_statuses"] == original["case_statuses"]
    assert changed["source_sha256_by_case"] == original["source_sha256_by_case"]
    assert changed["scores"]["existing_orion"]["tp"] == 0


def test_blind_packet_contains_exact_source_without_labels_or_predictions():
    review = packet()
    result = benchmark.evaluate_three_way()
    assert len(review["cases"]) == result["case_count"]
    assert review["labels_or_detector_predictions_included"] is False
    assert {case["case_id"]: case["source_sha256"] for case in review["cases"]} == (
        result["source_sha256_by_case"]
    )
    assert "positive_cases" not in json.dumps(review)
    assert "candidate_review" not in json.dumps(review)
