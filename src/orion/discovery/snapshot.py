"""Normalized read-only discovery snapshots.

Adapters translate vendor/system-specific observations into this small
contract. The rest of ORION must not depend on the source system's schema.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Mapping
from uuid import UUID, uuid4

from ..contracts import Evidence, EvidenceKind, Observation


@dataclass(frozen=True, slots=True)
class DiscoveredObject:
    object_id: UUID
    object_type: str
    name: str
    attributes: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class DiscoverySnapshot:
    tenant_id: str
    source_system: str
    observed_at: datetime
    objects: tuple[DiscoveredObject, ...]

    def to_observations(self) -> tuple[Observation, ...]:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")

        return tuple(
            Observation(
                evidence=Evidence(
                    evidence_id=uuid4(),
                    tenant_id=self.tenant_id,
                    kind=EvidenceKind.METADATA,
                    source=self.source_system,
                    observed_at=self.observed_at.astimezone(timezone.utc),
                    payload={
                        "key": str(item.object_id),
                        "node_type": item.object_type,
                        "name": item.name,
                        "attributes": dict(item.attributes),
                    },
                )
            )
            for item in self.objects
        )
