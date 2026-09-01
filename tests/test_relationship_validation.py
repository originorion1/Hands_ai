from uuid import uuid4

from orion.understanding.inference import InferenceEvidence, RelationshipInferenceEngine
from orion.understanding.validation import (
    RelationshipValidator,
    ValidationDecision,
    ValidationPolicy,
)


def make_hypothesis(count: int = 2):
    evidence = [InferenceEvidence(uuid4(), "observation") for _ in range(count)]
    return RelationshipInferenceEngine().propose(
        source_node=uuid4(),
        relationship_type="REQUIRES",
        target_node=uuid4(),
        evidence=evidence,
    ), evidence


def test_accepts_only_when_minimum_evidence_is_verified() -> None:
    hypothesis, evidence = make_hypothesis()
    result = RelationshipValidator().validate(
        hypothesis,
        evidence_verifier=lambda evidence_id: evidence_id in {e.evidence_id for e in evidence},
    )
    assert result.decision is ValidationDecision.ACCEPT
    assert result.supporting_evidence == 2


def test_insufficient_evidence_escalates() -> None:
    hypothesis, evidence = make_hypothesis(2)
    result = RelationshipValidator(ValidationPolicy(minimum_supporting_evidence=2)).validate(
        hypothesis,
        evidence_verifier=lambda evidence_id: evidence_id == evidence[0].evidence_id,
    )
    assert result.decision is ValidationDecision.ESCALATE


def test_contradiction_escalates_by_default() -> None:
    hypothesis, evidence = make_hypothesis()
    result = RelationshipValidator().validate(
        hypothesis,
        evidence_verifier=lambda _: True,
        contradiction_ids=[uuid4()],
    )
    assert result.decision is ValidationDecision.ESCALATE
    assert result.contradictory_evidence == 1
