"""Offline comparator keeps controls, evidence and authority explicit."""

import hashlib
import json
from pathlib import Path

import pytest

from evaluation.hybrid_237 import evaluate, generate_active


def test_frozen_reference_and_three_way_comparison():
    result = evaluate.three_way()
    scores = result["results"]
    assert scores["conventional_statistical"] == scores["hybrid_candidate"]
    assert scores["conventional_statistical"]["tp"] == 8
    assert scores["conventional_statistical"]["fp"] == 1
    assert scores["existing_orion"]["tp"] == 1
    assert result["incremental_hybrid_true_positives"] == 0
    assert result["incremental_hybrid_false_positives"] == 0
    assert result["verified_avoidable_cost_usd"] == 0
    assert result["realized_savings_usd"] == 0
    assert result["execution_allowed"] is False
    assert result["orion_provenance"]


def test_active_fixture_rebuild_is_byte_identical():
    cases, outcomes = generate_active.generate()
    for filename, document in (("active_cases.json", cases),
                               ("active_outcomes.json", outcomes)):
        expected = json.dumps(document, indent=2, sort_keys=True) + "\n"
        assert (evaluate.ROOT / filename).read_text() == expected


def test_active_plan_is_bounded_and_denies_revoked_authority():
    report = evaluate.active_investigation()
    assert report["execution_allowed"] is False
    assert len(report["plans_frozen_before_outcomes_sha256"]) == 64
    assert report["results"]["fixed"]["resolved"] == 4
    assert report["results"]["symptom_first"]["resolved"] == 6
    assert report["results"]["value_ranked"]["resolved"] == 6
    cases = json.loads((evaluate.ROOT / "active_cases.json").read_text())["cases"]
    revoked = next(case for case in cases if case["id"] == "revoked-a")
    assert evaluate._order(revoked, "value_ranked") == ()
    for case in cases:
        for strategy in ("fixed", "symptom_first", "value_ranked"):
            plan = evaluate._order(case, strategy)
            assert len(plan) == len(set(plan))
            assert set(plan) <= set(case["allowed_requests"])
            assert len(plan[:case["max_requests"]]) <= case["max_requests"]


def test_unreviewed_capability_and_bad_budget_denied():
    cases = json.loads((evaluate.ROOT / "active_cases.json").read_text())["cases"]
    case = cases[0]
    with pytest.raises(ValueError, match="unreviewed"):
        evaluate._order({**case, "allowed_requests": ["write_invoice"]}, "value_ranked")
    with pytest.raises(ValueError, match="budget"):
        evaluate._order({**case, "max_requests": 999}, "fixed")


def test_outcome_must_follow_evidence_period(tmp_path, monkeypatch):
    original = evaluate.ROOT
    cases = json.loads((original / "active_cases.json").read_text())
    outcomes = json.loads((original / "active_outcomes.json").read_text())
    identifier = cases["cases"][0]["id"]
    outcomes["outcomes"][identifier]["period"] = cases["cases"][0]["period"]
    (tmp_path / "active_cases.json").write_text(json.dumps(cases))
    (tmp_path / "active_outcomes.json").write_text(json.dumps(outcomes))
    monkeypatch.setattr(evaluate, "ROOT", tmp_path)
    with pytest.raises(ValueError, match="follow"):
        evaluate.active_investigation()


def test_reference_inputs_unchanged():
    reference = Path(evaluate.REFERENCE)
    assert hashlib.sha256((reference / "events.json").read_bytes()).hexdigest() == (
        evaluate.EVENTS_SHA256
    )
    assert hashlib.sha256((reference / "ground_truth.json").read_bytes()).hexdigest() == (
        evaluate.LABELS_SHA256
    )
