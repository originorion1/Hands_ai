from dataclasses import replace
from uuid import uuid4

import pytest

from orion.knowledge.promotion import KnowledgeEntry
from orion.shadow.decision import ShadowDecision, propose_from_knowledge
from orion.validation.claims import Assurance


def entry(tenant_id: str, scope: str) -> KnowledgeEntry:
    return KnowledgeEntry(
        knowledge_id=uuid4(),
        tenant_id=tenant_id,
        statement="Validated rule",
        evidence_ids=(uuid4(),),
        scope=scope,
        assurance=Assurance.LOW,
    )


def test_shadow_decision_never_allows_execution():
    decision = propose_from_knowledge(
        (entry("customer-a", "customer"),),
        tenant_id="customer-a",
        action="prepare invoice draft",
    )
    assert decision.execution_allowed is False
    assert decision.tenant_id == "customer-a"


def test_shadow_decision_rejects_missing_applicable_knowledge():
    with pytest.raises(ValueError, match="applicable validated knowledge"):
        propose_from_knowledge(
            (entry("customer-b", "customer"),),
            tenant_id="customer-a",
            action="prepare invoice draft",
        )


def test_shadow_decision_cannot_be_constructed_with_execution_authority() -> None:
    with pytest.raises(TypeError, match="execution_allowed"):
        ShadowDecision(
            decision_id=uuid4(),
            tenant_id="customer-a",
            action="prepare invoice draft",
            rationale="test",
            knowledge_ids=(uuid4(),),
            execution_allowed=True,
        )


def test_shadow_decision_rejects_direct_common_knowledge() -> None:
    with pytest.raises(ValueError, match="applicable validated knowledge"):
        propose_from_knowledge(
            (entry("customer-a", "common"),),
            tenant_id="customer-a",
            action="prepare invoice draft",
        )


def test_shadow_decision_rejects_unvalidated_knowledge() -> None:
    item = entry("customer-a", "customer")
    item = replace(item, status="unvalidated")

    with pytest.raises(ValueError, match="applicable validated knowledge"):
        propose_from_knowledge(
            (item,),
            tenant_id="customer-a",
            action="prepare invoice draft",
        )
