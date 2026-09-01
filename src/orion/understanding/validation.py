"""Evidence-backed validation boundary for inferred relationships."""

from __future__ import annotations

from collections.abc import Callable, Iterable
from dataclasses import dataclass
from enum import Enum
from uuid import UUID

from .inference import HypothesisStatus, RelationshipHypothesis


class ValidationDecision(str, Enum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    ESCALATE = "ESCALATE"


@dataclass(frozen=True, slots=True)
class ValidationResult:
    hypothesis_id: UUID
    decision: ValidationDecision
    reason: str
    supporting_evidence: int
    contradictory_evidence: int


@dataclass(frozen=True, slots=True)
class ValidationPolicy:
    """Minimal deterministic policy; richer policies can be injected later."""

    minimum_supporting_evidence: int = 2
    reject_on_contradiction: bool = True


class RelationshipValidator:
    """Validates hypotheses without granting execution authority."""

    def __init__(self, policy: ValidationPolicy | None = None) -> None:
        self.policy = policy or ValidationPolicy()

    def validate(
        self,
        hypothesis: RelationshipHypothesis,
        *,
        evidence_verifier: Callable[[UUID], bool],
        contradiction_ids: Iterable[UUID] = (),
    ) -> ValidationResult:
        if hypothesis.status in {
            HypothesisStatus.REJECTED,
            HypothesisStatus.RETIRED,
        }:
            return ValidationResult(
                hypothesis.hypothesis_id,
                ValidationDecision.REJECT,
                "Hypothesis is no longer eligible for validation",
                0,
                0,
            )

        verified = sum(
            1 for reference in hypothesis.evidence if evidence_verifier(reference.evidence_id)
        )
        contradictions = set(contradiction_ids)

        if contradictions and self.policy.reject_on_contradiction:
            return ValidationResult(
                hypothesis.hypothesis_id,
                ValidationDecision.ESCALATE,
                "Contradictory evidence requires investigation",
                verified,
                len(contradictions),
            )

        if verified < self.policy.minimum_supporting_evidence:
            return ValidationResult(
                hypothesis.hypothesis_id,
                ValidationDecision.ESCALATE,
                "Insufficient verified evidence",
                verified,
                len(contradictions),
            )

        return ValidationResult(
            hypothesis.hypothesis_id,
            ValidationDecision.ACCEPT,
            "Minimum evidence requirement satisfied",
            verified,
            len(contradictions),
        )
