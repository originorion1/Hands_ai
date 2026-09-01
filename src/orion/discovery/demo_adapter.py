"""Deterministic discovery adapter used to exercise the v0.1 pipeline.

This adapter intentionally has no external-system dependency. A real customer
adapter can implement the same contract without changing ORION's core.
"""

from __future__ import annotations

from datetime import datetime
from typing import Iterable
from uuid import UUID, uuid5

from .snapshot import DiscoveredObject, DiscoverySnapshot


class DemoDiscoveryAdapter:
    """Read-only adapter over a supplied set of source records."""

    NAMESPACE = UUID("4a6a0e4b-5d4f-4b91-9a9c-0a8e9d7f4a11")

    def __init__(self, records: Iterable[dict[str, object]]) -> None:
        self._records = tuple(records)

    def discover(self, *, tenant_id: str, observed_at: datetime) -> DiscoverySnapshot:
        objects: list[DiscoveredObject] = []
        for index, record in enumerate(self._records):
            object_type = str(record.get("type", "unknown"))
            name = str(record.get("name", f"object-{index}"))
            object_id = uuid5(self.NAMESPACE, f"{tenant_id}:{object_type}:{name}:{index}")
            attributes = {
                str(key): str(value)
                for key, value in record.items()
                if key not in {"type", "name"}
            }
            objects.append(
                DiscoveredObject(
                    object_id=object_id,
                    object_type=object_type,
                    name=name,
                    attributes=attributes,
                )
            )
        return DiscoverySnapshot(
            tenant_id=tenant_id,
            source_system="demo-read-only",
            observed_at=observed_at,
            objects=tuple(objects),
        )
