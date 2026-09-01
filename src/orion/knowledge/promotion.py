"""Governed promotion boundary between validated hypotheses and knowledge."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping
from uuid import UUID, uuid4

from ..understanding.hypotheses import Hypothesis


@dataclass(frozen=True, slots=True)
class KnowledgeEntry:
    knowledge_id: UUID
    tenant_id: str
    statement: str
    evidence_ids: tuple[UUID, ...]
    scope: str
    status: str = "trusted"


class KnowledgeStore:
    """In-memory prototype store with explicit customer/common isolation."""

    def __init__(self) -> None:
        self._entries: list[KnowledgeEntry] = []

    def promote(self, hypothesis: Hypothesis, *, scope: str) -> KnowledgeEntry:
        if hypothesis.status != "validated":
            raise ValueError("only validated hypotheses may enter knowledge")
        if scope not in {"customer", "common"}:
            raise ValueError("scope must be customer or common")
        if scope == "common" and not hypothesis.supporting_evidence:
            raise ValueError("common knowledge requires provenance")
        entry = KnowledgeEntry(
            knowledge_id=uuid4(),
            tenant_id=hypothesis.tenant_id,
            statement=hypothesis.statement,
            evidence_ids=hypothesis.supporting_evidence,
            scope=scope,
        )
        self._entries.append(entry)
        return entry

    def list(self, *, tenant_id: str, scope: str) -> tuple[KnowledgeEntry, ...]:
        if scope == "common":
            return tuple(e for e in self._entries if e.scope == "common")
        return tuple(
            e for e in self._entries if e.scope == "customer" and e.tenant_id == tenant_id
        )
