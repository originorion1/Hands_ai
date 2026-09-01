"""Vendor-neutral system graph projection from ORION observations."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from uuid import UUID

from ..contracts import Observation


@dataclass(frozen=True, slots=True)
class GraphNode:
    node_id: UUID
    node_type: str
    name: str
    tenant_id: str
    attributes: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class SystemGraph:
    tenant_id: str
    nodes: tuple[GraphNode, ...]

    @classmethod
    def from_observations(cls, observations: tuple[Observation, ...]) -> SystemGraph:
        nodes: list[GraphNode] = []
        tenant_ids: set[str] = set()
        for observation in observations:
            evidence = observation.evidence
            tenant_ids.add(evidence.tenant_id)
            payload = evidence.payload
            raw_id = payload.get("key")
            name = payload.get("name")
            node_type = payload.get("node_type")
            if not all(isinstance(value, str) and value for value in (raw_id, name, node_type)):
                continue
            nodes.append(
                GraphNode(
                    node_id=UUID(raw_id),
                    node_type=node_type,
                    name=name,
                    tenant_id=evidence.tenant_id,
                    attributes={str(k): str(v) for k, v in payload.items()},
                )
            )
        if len(tenant_ids) > 1:
            raise ValueError("A system graph cannot combine tenants")
        tenant_id = next(iter(tenant_ids), "")
        return cls(tenant_id=tenant_id, nodes=tuple(nodes))
