"""In-memory stores for laboratory use and tests only."""

from __future__ import annotations

from collections.abc import Sequence

from ..contracts import Evidence


class InMemoryEvidenceStore:
    def __init__(self) -> None:
        self._items: list[Evidence] = []

    def append(self, evidence: Evidence) -> None:
        self._items.append(evidence)

    def query(self, *, tenant_id: str | None = None) -> Sequence[Evidence]:
        if tenant_id is None:
            return tuple(self._items)
        return tuple(item for item in self._items if item.tenant_id == tenant_id)
