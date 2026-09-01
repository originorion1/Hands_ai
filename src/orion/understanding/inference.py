"""Relationship inference primitives.

Inference produces hypotheses only. It never promotes a hypothesis to a fact,
trusted knowledge, authorization, or executable action.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from uuid import UUID, uuid4


class HypothesisStatus(str, Enum):
    CANDIDATE = "CANDIDATE"
    SUPPORTED = "SUPPORTED"
    CONTRADICTED = "CONTRADICTED"
    VALIDATED = "VALIDATED"
    REJECTED = "REJECTED"
    RETIRED = "RETIRED"


@dataclass(frozen=True, slots=True)
class InferenceEvidence:
    """Reference to evidence used by an inference; content is stored elsewhere."""

    evidence_id: UUID
    role: str


@dataclass(frozen=True, slots=True)
class RelationshipHypothesis:
    """A traceable, non-authoritative relationship proposal."""

    hypothesis_id: UUID
    source_node: UUID
    relationship_type: str
    target_node: UUID
    evidence: frozenset[InferenceEvidence]
    status: HypothesisStatus = HypothesisStatus.CANDIDATE
    rationale: str = ""
    metadata: Mapping[str, str] = field(default_factory=dict)

    @classmethod
    def propose(
        cls,
        *,
        source_node: UUID,
        relationship_type: str,
        target_node: UUID,
        evidence: Sequence[InferenceEvidence],
        rationale: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> RelationshipHypothesis:
        if not evidence:
            raise ValueError("An inference hypothesis requires evidence references")
        if source_node == target_node:
            raise ValueError("Self-referential relationship hypotheses are not allowed")
        return cls(
            hypothesis_id=uuid4(),
            source_node=source_node,
            relationship_type=relationship_type,
            target_node=target_node,
            evidence=frozenset(evidence),
            rationale=rationale,
            metadata=dict(metadata or {}),
        )


class RelationshipInferenceEngine:
    """Produces relationship hypotheses from supplied observations.

    Validation/promotion is intentionally outside this component. This keeps
    probabilistic inference separate from facts, governance, and execution.
    """

    def propose(
        self,
        *,
        source_node: UUID,
        relationship_type: str,
        target_node: UUID,
        evidence: Sequence[InferenceEvidence],
        rationale: str = "",
        metadata: Mapping[str, str] | None = None,
    ) -> RelationshipHypothesis:
        return RelationshipHypothesis.propose(
            source_node=source_node,
            relationship_type=relationship_type,
            target_node=target_node,
            evidence=evidence,
            rationale=rationale,
            metadata=metadata,
        )
