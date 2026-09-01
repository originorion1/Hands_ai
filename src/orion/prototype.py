"""Runnable, vendor-neutral ORION v0.1 vertical slice.

The slice demonstrates the minimum learning loop: discover authorized evidence,
persist evidence, project a bounded system understanding, and report what was
learned. It deliberately does not execute external writes.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable
from uuid import UUID, uuid4

from .contracts import Observation
from .ports import DiscoveryAdapter, EvidenceStore
from .understanding.graph import GraphNode, GraphStatus, GraphStore, NodeType


@dataclass(frozen=True, slots=True)
class PrototypeReport:
    observations: int
    evidence: int
    graph_nodes: int
    tenant_id: str | None


class PrototypeRunner:
    """Composes the first ORION vertical slice without vendor semantics."""

    def __init__(self, *, evidence_store: EvidenceStore, graph_store: GraphStore) -> None:
        self._evidence_store = evidence_store
        self._graph_store = graph_store

    def run(self, adapter: DiscoveryAdapter, *, tenant_id: str | None) -> PrototypeReport:
        observations = tuple(adapter.discover())
        evidence_count = 0
        graph_count = 0

        for observation in observations:
            evidence = observation.evidence
            if evidence.tenant_id != tenant_id:
                raise ValueError("discovery evidence tenant does not match run tenant")
            self._evidence_store.append(evidence)
            evidence_count += 1

            node = _node_from_observation(observation, tenant_id=tenant_id)
            self._graph_store.add_node(node)
            graph_count += 1

        return PrototypeReport(
            observations=len(observations),
            evidence=evidence_count,
            graph_nodes=graph_count,
            tenant_id=tenant_id,
        )


def _node_from_observation(observation: Observation, *, tenant_id: str | None) -> GraphNode:
    evidence = observation.evidence
    payload = dict(evidence.payload)
    key = str(payload.get("key") or evidence.source or evidence.evidence_id)
    raw_type = str(payload.get("node_type", NodeType.ENTITY.value)).lower()
    try:
        node_type = NodeType(raw_type)
    except ValueError:
        node_type = NodeType.ENTITY

    return GraphNode(
        node_type=node_type,
        tenant_id=tenant_id,
        key=key,
        attributes={"evidence_kind": evidence.kind.value, "source": evidence.source},
        status=GraphStatus.OBSERVED,
        provenance_ids=(evidence.evidence_id,),
    )


class StaticObservationAdapter:
    """Tiny deterministic adapter for local smoke tests and demonstrations."""

    def __init__(self, observations: Iterable[Observation]) -> None:
        self._observations = tuple(observations)

    def discover(self) -> tuple[Observation, ...]:
        return self._observations
