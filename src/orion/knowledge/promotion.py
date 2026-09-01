"""Governed promotion boundary between validated hypotheses and knowledge."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID, uuid4

from ..understanding.hypotheses import Hypothesis
from ..validation.claims import Assurance, ValidationDecision


@dataclass(frozen=True, slots=True)
class KnowledgeEntry:
    knowledge_id: UUID
    tenant_id: str
    statement: str
    evidence_ids: tuple[UUID, ...]
    scope: str
    assurance: Assurance
    status: str = "validated"


class KnowledgeStore:
    """In-memory prototype store with explicit customer/common isolation."""

    def __init__(self) -> None:
        self._entries: list[KnowledgeEntry] = []

    def promote(
        self,
        hypothesis: Hypothesis,
        *,
        validation: ValidationDecision,
        scope: str,
    ) -> KnowledgeEntry:
        if validation.hypothesis_id != hypothesis.hypothesis_id:
            raise ValueError("validation decision does not belong to hypothesis")

        if validation.status != "validated":
            raise ValueError("only validated hypotheses may enter knowledge")

        if hypothesis.status != "validated":
            raise ValueError("hypothesis must be explicitly marked validated")

        if not hypothesis.supporting_evidence:
            raise ValueError("knowledge promotion requires provenance")

        if scope == "common":
            raise ValueError(
                "common knowledge requires an explicit generalization workflow"
            )

        if scope != "customer":
            raise ValueError("scope must be customer")

        entry = KnowledgeEntry(
            knowledge_id=uuid4(),
            tenant_id=hypothesis.tenant_id,
            statement=hypothesis.statement,
            evidence_ids=hypothesis.supporting_evidence,
            scope=scope,
            assurance=validation.assurance,
        )
        self._entries.append(entry)
        return entry

    def list(self, *, tenant_id: str, scope: str) -> tuple[KnowledgeEntry, ...]:
        if scope == "common":
            raise ValueError(
                "common knowledge retrieval is unavailable until generalization is implemented"
            )

        if scope != "customer":
            raise ValueError("scope must be customer")

        return tuple(
            e for e in self._entries
            if e.scope == "customer" and e.tenant_id == tenant_id
        )
