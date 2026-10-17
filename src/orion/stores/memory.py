"""In-memory stores for laboratory use and tests only."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal
from types import MappingProxyType
from uuid import UUID

from ..contracts import Evidence


def _copy_payload(value: object, active: set[int]) -> object:
    if value is None or type(value) in (bool, int, float, str, bytes, date, datetime, Decimal, UUID):
        return value
    if type(value) not in (dict, MappingProxyType, list, tuple):
        raise ValueError("unsupported evidence payload value")
    identity = id(value)
    if identity in active:
        raise ValueError("cyclic evidence payload")
    active.add(identity)
    try:
        if type(value) in (dict, MappingProxyType):
            if not all(type(key) is str for key in value):
                raise ValueError("evidence payload keys must be strings")
            return {key: _copy_payload(item, active) for key, item in value.items()}
        if type(value) is list:
            return [_copy_payload(item, active) for item in value]
        return tuple(_copy_payload(item, active) for item in value)
    finally:
        active.remove(identity)


def _snapshot(evidence: Evidence) -> Evidence:
    if not isinstance(evidence, Evidence):
        raise TypeError("evidence snapshot requires Evidence")
    try:
        return replace(evidence, payload=_copy_payload(evidence.payload, set()))
    except Exception:  # noqa: BLE001 - fail closed without exposing payload contents
        raise ValueError("evidence snapshot unavailable") from None


class InMemoryEvidenceStore:
    def __init__(self) -> None:
        self._items: list[Evidence] = []

    def append(self, evidence: Evidence) -> None:
        self._items.append(_snapshot(evidence))

    def append_batch_atomic(self, evidence: Sequence[Evidence]) -> None:
        # Build the replacement before publishing it. Construction failure leaves
        # the previously visible evidence untouched.
        snapshots = tuple(_snapshot(item) for item in evidence)
        self._items = [*self._items, *snapshots]

    def query(self, *, tenant_id: str | None = None) -> Sequence[Evidence]:
        if tenant_id is None:
            return tuple(_snapshot(item) for item in self._items)
        return tuple(_snapshot(item) for item in self._items if item.tenant_id == tenant_id)
