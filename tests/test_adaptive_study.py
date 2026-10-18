"""Decision-risk ranking stays inside existing authorization and evidence gates."""

from uuid import uuid4

import pytest

from orion.discovery.planner import DiscoveryAuthorization
from orion.learning.adaptive_study import (
    decision_loss,
    expected_decision_gain,
    rank_adaptive_questions,
)
from orion.learning.epistemic_selection import QuestionModel
from orion.understanding.metadata import MetadataUnderstanding, StructuralEntity


def _question(tenant, target, structural, calibration, likelihoods):
    return QuestionModel(tenant, target, target, (structural,),
                         ("flag", "clear"), likelihoods, (calibration,))


def _understanding(tenant, target, structural):
    return MetadataUnderstanding(tenant, (
        StructuralEntity(target, None, False, False, False, (), (structural,)),
    ))


def _rank(questions, *, authorized=True, verifier=None):
    structural = questions[0].provenance_ids[0]
    return rank_adaptive_questions(
        (0.5, 0.5), questions,
        understanding=_understanding("tenant-a", questions[0].target, structural),
        authorization=DiscoveryAuthorization(
            "tenant-a", record_targets=frozenset({questions[0].target}) if authorized
            else frozenset(), max_targets=1,
        ),
        calibration_verifier=verifier or (lambda _: True),
        max_request_cost=1, max_privacy_risk=0.2, max_operational_risk=0.2,
    )


def test_decision_gain_uses_frozen_abstention_thresholds():
    structural, calibration = uuid4(), uuid4()
    weak = _question("tenant-a", "Weak", structural, calibration,
                     ((0.6, 0.4), (0.4, 0.6)))
    strong = _question("tenant-a", "Strong", structural, calibration,
                       ((0.9, 0.1), (0.1, 0.9)))
    assert decision_loss((0.5, 0.5)) == 0.2
    assert expected_decision_gain((0.5, 0.5), weak) == pytest.approx(0)
    assert expected_decision_gain((0.5, 0.5), strong) == pytest.approx(0.1)


def test_only_governed_authorized_question_can_be_ranked():
    structural, calibration = uuid4(), uuid4()
    q = _question("tenant-a", "Allowed", structural, calibration,
                  ((0.9, 0.1), (0.1, 0.9)))
    assert [row.target for row in _rank((q,))] == ["Allowed"]
    assert _rank((q,), authorized=False) == ()
    with pytest.raises(ValueError, match="unverified"):
        _rank((q,), verifier=lambda _: False)
    foreign = _question("tenant-b", "Allowed", structural, calibration,
                        ((0.9, 0.1), (0.1, 0.9)))
    with pytest.raises(ValueError, match="tenant"):
        _rank((foreign,))


@pytest.mark.parametrize("prior", [(float("nan"), 0.5), (0.5, 0.4), (1.0,)])
def test_invalid_belief_fails_closed(prior):
    with pytest.raises(ValueError, match="binary belief"):
        decision_loss(prior)
