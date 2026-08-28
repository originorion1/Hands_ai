from uuid import uuid4

import pytest

from orion.knowledge.promotion import KnowledgeEntry
from orion.shadow.decision import propose_from_knowledge


def entry(tenant_id: str, scope: str) -> KnowledgeEntry:
    return KnowledgeEntry(
        knowledge_id=uuid4(),
        tenant_id=tenant_id,
        statement="Validated rule",
        evidence_ids=(uuid4(),),
        scope=scope,
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
