from dataclasses import replace
from uuid import uuid4

import pytest

from orion.knowledge.promotion import KnowledgeStore
from orion.understanding.hypotheses import Hypothesis
from orion.validation.claims import Assurance, ValidationDecision


def hypothesis() -> Hypothesis:
    return Hypothesis(
        hypothesis_id=uuid4(),
        tenant_id="customer-a",
        statement="Observed structural fact",
        supporting_evidence=(uuid4(),),
    )


def decision(
    item: Hypothesis,
    *,
    assurance: Assurance = Assurance.LOW,
    status: str = "validated",
) -> ValidationDecision:
    return ValidationDecision(
        hypothesis_id=item.hypothesis_id,
        assurance=assurance,
        status=status,
        reason="test decision",
    )


def test_promotion_preserves_assurance() -> None:
    item = hypothesis()
    validated = replace(item, status="validated")

    entry = KnowledgeStore().promote(
        validated,
        validation=decision(item, assurance=Assurance.LOW),
        scope="customer",
    )

    assert entry.assurance is Assurance.LOW
    assert entry.status == "validated"


def test_promotion_rejects_validation_for_different_hypothesis() -> None:
    item = hypothesis()
    other = hypothesis()

    with pytest.raises(ValueError, match="does not belong"):
        KnowledgeStore().promote(
            replace(item, status="validated"),
            validation=decision(other),
            scope="customer",
        )


def test_promotion_rejects_unvalidated_decision() -> None:
    item = hypothesis()

    with pytest.raises(ValueError, match="only validated"):
        KnowledgeStore().promote(
            replace(item, status="validated"),
            validation=decision(item, status="unvalidated"),
            scope="customer",
        )


def test_promotion_requires_provenance() -> None:
    item = replace(
        hypothesis(),
        supporting_evidence=(),
        status="validated",
    )

    with pytest.raises(ValueError, match="requires provenance"):
        KnowledgeStore().promote(
            item,
            validation=decision(item),
            scope="customer",
        )
