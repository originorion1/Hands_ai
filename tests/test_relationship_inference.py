from uuid import uuid4

import pytest

from orion.understanding.inference import (
    InferenceEvidence,
    HypothesisStatus,
    RelationshipInferenceEngine,
)


def test_inference_requires_evidence() -> None:
    with pytest.raises(ValueError, match="requires evidence"):
        RelationshipInferenceEngine().propose(
            source_node=uuid4(),
            relationship_type="DEPENDS_ON",
            target_node=uuid4(),
            evidence=[],
        )


def test_inference_produces_candidate_not_fact() -> None:
    source = uuid4()
    target = uuid4()
    evidence = InferenceEvidence(uuid4(), "observation")

    hypothesis = RelationshipInferenceEngine().propose(
        source_node=source,
        relationship_type="REQUIRES",
        target_node=target,
        evidence=[evidence],
        rationale="Repeated observed workflow sequence",
    )

    assert hypothesis.source_node == source
    assert hypothesis.target_node == target
    assert hypothesis.status is HypothesisStatus.CANDIDATE
    assert evidence in hypothesis.evidence


def test_self_relationship_is_rejected() -> None:
    node = uuid4()
    with pytest.raises(ValueError, match="Self-referential"):
        RelationshipInferenceEngine().propose(
            source_node=node,
            relationship_type="RELATES_TO",
            target_node=node,
            evidence=[InferenceEvidence(uuid4(), "observation")],
        )
