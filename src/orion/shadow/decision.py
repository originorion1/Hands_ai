"""Shadow decisions: proposed work without customer-system mutation."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from ..knowledge.promotion import KnowledgeEntry


@dataclass(frozen=True, slots=True)
class ShadowDecision:
    decision_id: UUID
    tenant_id: str
    action: str
    rationale: str
    knowledge_ids: tuple[UUID, ...]
    execution_allowed: bool = False


def propose_from_knowledge(
    knowledge: tuple[KnowledgeEntry, ...], *, tenant_id: str, action: str
) -> ShadowDecision:
    relevant = tuple(entry for entry in knowledge if entry.scope == "common" or entry.tenant_id == tenant_id)
    if not relevant:
        raise ValueError("a shadow decision requires applicable validated knowledge")
    return ShadowDecision(
        decision_id=uuid4(),
        tenant_id=tenant_id,
        action=action,
        rationale="Proposed from validated, provenance-carrying knowledge; execution is disabled.",
        knowledge_ids=tuple(entry.knowledge_id for entry in relevant),
    )
