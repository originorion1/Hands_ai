"""Risk-aware validation and knowledge-promotion boundary."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from ..understanding.hypotheses import Hypothesis


class Assurance(StrEnum):
    LOW = "low"
    MEDIUM = "medium"
    HIGH = "high"
    CRITICAL = "critical"


@dataclass(frozen=True, slots=True)
class ValidationDecision:
    hypothesis_id: UUID
    assurance: Assurance
    status: str
    reason: str


def validate_hypothesis(
    hypothesis: Hypothesis,
    *,
    assurance: Assurance = Assurance.LOW,
    independent_evidence_count: int = 0,
    human_confirmed: bool = False,
) -> ValidationDecision:
    """Classify support without pretending evidence count is universal.

    The rule is risk-aware: high/critical assurance requires stronger support
    and critical cases may require explicit human confirmation. This function
    never writes to a knowledge store.
    """
    if independent_evidence_count < 0:
        raise ValueError("independent_evidence_count cannot be negative")

    if assurance is Assurance.CRITICAL and not human_confirmed:
        return ValidationDecision(
            hypothesis.hypothesis_id,
            assurance,
            "escalate",
            "critical assurance requires human confirmation",
        )

    required = {
        Assurance.LOW: 0,
        Assurance.MEDIUM: 1,
        Assurance.HIGH: 2,
        Assurance.CRITICAL: 3,
    }[assurance]
    if independent_evidence_count < required:
        return ValidationDecision(
            hypothesis.hypothesis_id,
            assurance,
            "unvalidated",
            "insufficient independent supporting evidence for requested assurance",
        )

    return ValidationDecision(
        hypothesis.hypothesis_id,
        assurance,
        "validated",
        "support meets the requested assurance policy",
    )
