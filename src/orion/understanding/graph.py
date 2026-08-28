"""Vendor-neutral system graph domain model.

The graph represents ORION's current understanding of an environment. It is not
an execution engine and does not grant authority.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Mapping
from uuid import UUID, uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class NodeType(StrEnum):
    ENTITY = "entity"
    ATTRIBUTE = "attribute"
    ACTOR = "actor"
    PROCESS = "process"
    ACTION = "action"
    STATE = "state"
    EVENT = "event"
    SYSTEM = "system"
    COMPONENT = "component"
    CAPABILITY = "capability"
    CONDITION = "condition"
    OUTCOME = "outcome"
    EVIDENCE = "evidence"
    KNOWLEDGE = "knowledge"


class RelationshipType(StrEnum):
    RELATES_TO = "relates_to"
    CONTAINS = "contains"
    HAS_ATTRIBUTE = "has_attribute"
    DEPENDS_ON = "depends_on"
    TRIGGERS = "triggers"
    PRECEDES = "precedes"
    TRANSITIONS_TO = "transitions_to"
    REQUIRES = "requires"
    PRODUCES = "produces"
    CONSUMES = "consumes"
    PERFORMED_BY = "performed_by"
    AUTHORIZES = "authorizes"
    SUPPORTED_BY = "supported_by"
    CONTRADICTED_BY = "contradicted_by"


class GraphStatus(StrEnum):
    OBSERVED = "observed"
    SUPPORTED = "supported"
    INFERRED = "inferred"
    VALIDATED = "validated"
    CONTRADICTED = "contradicted"
    STALE = "stale"
    RETIRED = "retired"
    UNKNOWN = "unknown"


@dataclass(frozen=True, slots=True)
class GraphNode:
    node_type: NodeType
    tenant_id: str | None
    key: str
    attributes: Mapping[str, object] = field(default_factory=dict)
    status: GraphStatus = GraphStatus.UNKNOWN
    confidence: float | None = None
    provenance_ids: tuple[UUID, ...] = ()
    node_id: UUID = field(default_factory=uuid4)
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class GraphRelationship:
    relationship_type: RelationshipType
    source_id: UUID
    target_id: UUID
    tenant_id: str | None
    status: GraphStatus = GraphStatus.UNKNOWN
    confidence: float | None = None
    provenance_ids: tuple[UUID, ...] = ()
    valid_from: datetime | None = None
    valid_until: datetime | None = None
    relationship_id: UUID = field(default_factory=uuid4)
    schema_version: int = 1


@dataclass(frozen=True, slots=True)
class GraphQuery:
    tenant_id: str | None
    source_id: UUID | None = None
    relationship_type: RelationshipType | None = None
    max_depth: int = 1
    limit: int = 100

    def __post_init__(self) -> None:
        if self.max_depth < 0:
            raise ValueError("max_depth must be non-negative")
        if self.limit < 1:
            raise ValueError("limit must be positive")


@dataclass(frozen=True, slots=True)
class GraphPath:
    node_ids: tuple[UUID, ...]
    relationship_ids: tuple[UUID, ...]
    generated_at: datetime = field(default_factory=utc_now)


class GraphInvariantError(ValueError):
    """Raised when a graph mutation would violate a domain invariant."""


class GraphStore:
    """Small persistence boundary for graph implementations.

    This in-memory implementation is intentionally replaceable. Production
    persistence belongs behind this contract and must preserve the same domain
    invariants and tenant boundaries.
    """

    def __init__(self) -> None:
        self._nodes: dict[UUID, GraphNode] = {}
        self._relationships: dict[UUID, GraphRelationship] = {}

    def add_node(self, node: GraphNode) -> None:
        if node.node_id in self._nodes:
            raise GraphInvariantError(f"node already exists: {node.node_id}")
        self._nodes[node.node_id] = node

    def add_relationship(self, relationship: GraphRelationship) -> None:
        if relationship.relationship_id in self._relationships:
            raise GraphInvariantError(
                f"relationship already exists: {relationship.relationship_id}"
            )
        source = self._nodes.get(relationship.source_id)
        target = self._nodes.get(relationship.target_id)
        if source is None or target is None:
            raise GraphInvariantError("relationship endpoints must already exist")
        if source.tenant_id != relationship.tenant_id or target.tenant_id != relationship.tenant_id:
            raise GraphInvariantError("relationship crosses tenant boundary")
        self._relationships[relationship.relationship_id] = relationship

    def get_node(self, node_id: UUID, *, tenant_id: str | None) -> GraphNode | None:
        node = self._nodes.get(node_id)
        if node is None or node.tenant_id != tenant_id:
            return None
        return node

    def relationships_from(
        self,
        node_id: UUID,
        *,
        tenant_id: str | None,
        relationship_type: RelationshipType | None = None,
        limit: int = 100,
    ) -> tuple[GraphRelationship, ...]:
        if limit < 1:
            raise ValueError("limit must be positive")
        result = [
            item
            for item in self._relationships.values()
            if item.source_id == node_id
            and item.tenant_id == tenant_id
            and (relationship_type is None or item.relationship_type == relationship_type)
        ]
        return tuple(result[:limit])

    def path(self, query: GraphQuery) -> tuple[GraphPath, ...]:
        if query.source_id is None:
            return ()
        if self.get_node(query.source_id, tenant_id=query.tenant_id) is None:
            return ()

        paths: list[GraphPath] = []
        frontier: list[GraphPath] = [GraphPath((query.source_id,), ())]
        while frontier and len(paths) < query.limit:
            current = frontier.pop(0)
            if len(current.relationship_ids) >= query.max_depth:
                paths.append(current)
                continue
            edges = self.relationships_from(
                current.node_ids[-1],
                tenant_id=query.tenant_id,
                relationship_type=query.relationship_type,
                limit=query.limit,
            )
            if not edges:
                paths.append(current)
                continue
            for edge in edges:
                if edge.target_id in current.node_ids:
                    continue
                frontier.append(
                    GraphPath(
                        current.node_ids + (edge.target_id,),
                        current.relationship_ids + (edge.relationship_id,),
                    )
                )
                if len(frontier) + len(paths) >= query.limit:
                    break
        return tuple(paths[: query.limit])
