"""Finite information-gain selection cannot become acquisition authority."""

from uuid import uuid4

import pytest

from orion.discovery.planner import DiscoveryAuthorization
from orion.learning.epistemic_selection import (
    QuestionModel,
    expected_information_gain,
    posterior,
    rank_authorized_questions,
)
from orion.understanding.metadata import MetadataUnderstanding, StructuralEntity


def _entity(name, evidence_id):
    return StructuralEntity(name, None, False, False, False, (), (evidence_id,))


def _question(target, structural, calibration, likelihoods, **options):
    return QuestionModel(
        "tenant-a", target, target, (structural,), ("flag", "clear"),
        likelihoods, (calibration,), **options,
    )


def _rank(questions, structural, calibration, **options):
    understanding = MetadataUnderstanding(
        "tenant-a", tuple(_entity(q.target, structural) for q in questions),
    )
    authorization = DiscoveryAuthorization(
        "tenant-a", record_targets=frozenset(q.target for q in questions),
        max_targets=1,
    )
    return rank_authorized_questions(
        (0.5, 0.5), questions, understanding=understanding,
        authorization=authorization, calibration_verifier=lambda value: value == calibration,
        max_request_cost=2, max_privacy_risk=0.2, max_operational_risk=0.2,
        **options,
    )


def test_information_gain_prefers_verified_discriminating_evidence():
    structural, calibration = uuid4(), uuid4()
    weak = _question("Alpha", structural, calibration, ((0.6, 0.4), (0.4, 0.6)))
    strong = _question("Zulu", structural, calibration, ((0.9, 0.1), (0.1, 0.9)))
    ranked = _rank((weak, strong), structural, calibration)
    assert [row.target for row in ranked] == ["Zulu", "Alpha"]
    assert ranked[0].expected_information_gain_bits == pytest.approx(0.531004, abs=1e-6)
    assert posterior((0.5, 0.5), strong, "flag") == pytest.approx((0.9, 0.1))
    assert ranked[0].plan.tenant_id == "tenant-a"
    assert "expected bits" in ranked[0].rationale


def test_missing_decision_signal_has_zero_gain_without_claiming_value():
    q = _question("Neutral", uuid4(), uuid4(), ((0.5, 0.5), (0.5, 0.5)))
    assert expected_information_gain((0.5, 0.5), q) == 0
    assert posterior((0.5, 0.5), q, "clear") == (0.5, 0.5)


def test_unauthorized_target_never_queries_calibration_evidence():
    structural, calibration = uuid4(), uuid4()
    q = _question("Denied", structural, calibration, ((0.9, 0.1), (0.1, 0.9)))
    calls = []
    ranked = rank_authorized_questions(
        (0.5, 0.5), (q,),
        understanding=MetadataUnderstanding("tenant-a", (_entity("Denied", structural),)),
        authorization=DiscoveryAuthorization("tenant-a", record_targets=frozenset()),
        calibration_verifier=lambda value: calls.append(value) or True,
        max_request_cost=1, max_privacy_risk=0, max_operational_risk=0,
    )
    assert ranked == () and calls == []


def test_risk_and_cost_are_hard_filters_before_calibration():
    structural, calibration = uuid4(), uuid4()
    q = _question("Costly", structural, calibration, ((0.9, 0.1), (0.1, 0.9)),
                  request_cost=3, privacy_risk=0.3)
    calls = []
    ranked = rank_authorized_questions(
        (0.5, 0.5), (q,),
        understanding=MetadataUnderstanding("tenant-a", (_entity("Costly", structural),)),
        authorization=DiscoveryAuthorization("tenant-a", record_targets=frozenset({"Costly"})),
        calibration_verifier=lambda value: calls.append(value) or True,
        max_request_cost=2, max_privacy_risk=0.2, max_operational_risk=0.2,
    )
    assert ranked == () and calls == []


def test_wrong_tenant_and_unverified_calibration_fail_closed():
    structural, calibration = uuid4(), uuid4()
    q = _question("Target", structural, calibration, ((0.9, 0.1), (0.1, 0.9)))
    with pytest.raises(ValueError, match="tenant boundary"):
        rank_authorized_questions(
            (0.5, 0.5), (q,),
            understanding=MetadataUnderstanding("tenant-b", (_entity("Target", structural),)),
            authorization=DiscoveryAuthorization("tenant-a", record_targets=frozenset({"Target"})),
            calibration_verifier=lambda _: True, max_request_cost=1,
            max_privacy_risk=0, max_operational_risk=0,
        )
    with pytest.raises(ValueError, match="unverified"):
        _rank((q,), structural, uuid4())


@pytest.mark.parametrize("prior", [(1.0,), (0.4, 0.4), (float("nan"), 0.5)])
def test_bad_prior_denied(prior):
    q = _question("Target", uuid4(), uuid4(), ((0.9, 0.1), (0.1, 0.9)))
    with pytest.raises(ValueError):
        expected_information_gain(prior, q)
