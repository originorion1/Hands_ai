"""Explicit, provenance-carrying hypotheses derived from observations."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from ..contracts import Observation


@dataclass(frozen=True, slots=True)
class Hypothesis:
    hypothesis_id: UUID
    tenant_id: str
    statement: str
    supporting_evidence: tuple[UUID, ...]
    status: str = "unvalidated"


def generate_hypotheses(observations: tuple[Observation, ...]) -> tuple[Hypothesis, ...]:
    """Generate only explicit, low-risk structural hypotheses.

    This deliberately does not promote hypotheses to facts or common knowledge.
    """
    result: list[Hypothesis] = []
    for observation in observations:
        evidence = observation.evidence
        payload = evidence.payload
        resource = payload.get("resource")
        record = payload.get("record")
        if not isinstance(resource, str) or not isinstance(record, dict):
            continue
        name = record.get("name")
        if not isinstance(name, str) or not name:
            continue
        result.append(
            Hypothesis(
                hypothesis_id=uuid4(),
                tenant_id=evidence.tenant_id,
                statement=f"The source system exposes a {resource} object named {name}.",
                supporting_evidence=(evidence.evidence_id,),
            )
        )
    return tuple(result)
