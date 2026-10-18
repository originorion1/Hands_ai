"""Synthetic mocks test the gate; they are not independent reviewer evidence."""

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import NAMESPACE_URL, uuid5

import pytest

from orion.learning.adjudicated_study_strategy import (
    AdjudicatedStudyCase,
    StudyObservation,
    base_strategy,
    derive_adjudicated_strategy,
)
from orion.learning.prediction_ledger import Outcome, Prediction, PredictionLedger

NOW = datetime(2026, 1, 1, tzinfo=UTC)
HORIZON = NOW + timedelta(days=1)
LATER = HORIZON + timedelta(hours=1)
DIGEST = hashlib.sha256(b"synthetic source record").hexdigest()


def _reviews(tmp_path):
    path = tmp_path / "prospective.db"
    issued = PredictionLedger(path, clock=lambda: NOW)
    pairs = []
    evidence = {}
    for index in range(20):
        prediction_id = f"p-{index}"
        source_id = uuid5(NAMESPACE_URL, f"source-{index}")
        outcome_id = uuid5(NAMESPACE_URL, f"outcome-{index}")
        prediction = Prediction(
            "tenant-a", prediction_id, "synthetic-issue-v1", "base-v1",
            NOW, NOW, HORIZON, 0.5, (source_id,),
        )
        outcome = Outcome("tenant-a", prediction_id, LATER, index % 2 == 0,
                          (outcome_id,))
        issued.record(prediction)
        pairs.append((prediction, outcome, source_id))
        evidence[source_id] = DIGEST
    resolved = PredictionLedger(path, clock=lambda: LATER)
    for _, outcome, _ in pairs:
        resolved.resolve(outcome)
    reviews = []
    for prediction, outcome, source_id in pairs:
        review = AdjudicatedStudyCase(
            prediction, outcome,
            (StudyObservation("F0", "flag" if outcome.actual else "clear",
                              source_id, DIGEST),),
            "synthetic-test-reviewer", "0" * 64,
        )
        review = replace(review, reviewer_attestation_sha256=hashlib.sha256(
            review.review_digest().encode()).hexdigest())
        reviews.append(review)
    base = base_strategy("tenant-a", (0.5, 0.5),
                         {"F0": ((0.45, 0.45, 0.10), (0.45, 0.45, 0.10))},
                         created_at=NOW)
    return resolved, tuple(reviews), evidence, base


def _derive(base, reviews, ledger, evidence, **options):
    return derive_adjudicated_strategy(
        base, reviews, producer_id="orion-policy", now=LATER, ledger=ledger,
        reviewer_verifier=options.get(
            "reviewer_verifier", lambda case, digest: case.reviewer_attestation_sha256
            == hashlib.sha256(digest.encode()).hexdigest()),
        source_verifier=options.get(
            "source_verifier", lambda tenant, identity, digest:
            tenant == "tenant-a" and evidence.get(identity) == digest),
    )


def test_versioned_update_uses_resolved_reviewed_outcomes_and_retains_rollback(tmp_path):
    ledger, reviews, evidence, base = _reviews(tmp_path)
    updated = _derive(base, reviews, ledger, evidence)
    assert updated.parent_version_id == base.version_id
    assert updated.version_id != base.version_id
    assert len(updated.adjudication_digests) == 20
    assert updated.prior == (0.5, 0.5)
    assert updated.likelihoods[0][1][0][1] > base.likelihoods[0][1][0][1]
    assert updated.likelihoods[0][1][1][0] > base.likelihoods[0][1][1][0]
    assert _derive(base, reviews, ledger, evidence).version_id == updated.version_id
    assert base.parent_version_id is None  # Rollback selects the unchanged base version.


def test_unverified_reviewer_or_source_denies_update(tmp_path):
    ledger, reviews, evidence, base = _reviews(tmp_path)
    with pytest.raises(ValueError, match="reviewer attestation"):
        _derive(base, reviews, ledger, evidence, reviewer_verifier=lambda *_: False)
    with pytest.raises(ValueError, match="custody"):
        _derive(base, reviews, ledger, evidence, source_verifier=lambda *_: False)
    with pytest.raises(ValueError, match="reviewer"):
        derive_adjudicated_strategy(
            base, reviews, producer_id="synthetic-test-reviewer", now=LATER,
            ledger=ledger, reviewer_verifier=lambda *_: True,
            source_verifier=lambda *_: True,
        )


def test_missing_or_changed_ledger_resolution_denies_update(tmp_path):
    ledger, reviews, evidence, base = _reviews(tmp_path)
    missing = PredictionLedger(tmp_path / "empty.db", clock=lambda: LATER)
    with pytest.raises(ValueError, match="ledger resolution"):
        _derive(base, reviews, missing, evidence)
    wrong = replace(reviews[0].outcome, actual=not reviews[0].outcome.actual)
    forged = (replace(reviews[0], outcome=wrong), *reviews[1:])
    with pytest.raises(ValueError, match="ledger resolution"):
        _derive(base, forged, ledger, evidence)
    with pytest.raises(ValueError, match="replay"):
        _derive(base, (reviews[0], *reviews[1:-1], reviews[0]), ledger, evidence)


def test_future_label_and_tenant_mismatch_denied(tmp_path):
    ledger, reviews, evidence, _base = _reviews(tmp_path)
    with pytest.raises(ValueError, match="horizon"):
        replace(reviews[0], outcome=replace(reviews[0].outcome, observed_at=NOW))
    foreign = base_strategy("tenant-b", (0.5, 0.5),
                            {"F0": ((0.45, 0.45, 0.10), (0.45, 0.45, 0.10))},
                            created_at=NOW)
    with pytest.raises(ValueError, match="tenant"):
        _derive(foreign, reviews, ledger, evidence)
