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
    never writes to a knowledge store. Counts remain trusted caller assertions,
    not authenticated semantic attestation; use the semantic evidence evaluator
    for business-role validation.
    """
    if (type(hypothesis) is not Hypothesis or type(assurance) is not Assurance
            or type(independent_evidence_count) is not int or independent_evidence_count < 0
            or type(human_confirmed) is not bool):
        raise ValueError("explicit hypothesis, assurance and nonnegative integer count required")
    references = hypothesis.supporting_evidence
    if (type(references) is not tuple or any(type(ref) is not UUID for ref in references)
            or len(set(references)) != len(references)
            or independent_evidence_count > len(references)):
        raise ValueError("independent support cannot exceed unique evidence references")
    if (not references or not isinstance(hypothesis.tenant_id, str)
            or not hypothesis.tenant_id.strip()
            or hypothesis.status not in ("unvalidated", "validated", "unknown")):
        return ValidationDecision(hypothesis.hypothesis_id, assurance, "unvalidated",
                                  "missing scoped provenance or contradicted hypothesis")

    if assurance is Assurance.CRITICAL and not human_confirmed:
        return ValidationDecision(
            hypothesis.hypothesis_id,
            assurance,
            "escalate",
            "critical assurance requires human confirmation",
        )

    required = {
        Assurance.LOW: 1,
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
