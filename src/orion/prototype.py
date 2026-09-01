"""Runnable, vendor-neutral ORION v0.1 vertical slice.

The slice demonstrates the minimum learning loop: discover authorized evidence,
persist evidence, project a bounded system understanding, and report what was
learned. It deliberately does not execute external writes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .contracts import Observation
from .ports import DiscoveryAdapter, EvidenceStore
from .understanding.graph import GraphNode, GraphStatus, GraphStore, NodeType


@dataclass(frozen=True, slots=True)
class PrototypeReport:
    observations: int
    evidence: int
    graph_nodes: int
    tenant_id: str


class PrototypeRunner:
    """Composes the first ORION vertical slice without vendor semantics."""

    def __init__(self, *, evidence_store: EvidenceStore, graph_store: GraphStore) -> None:
        self._evidence_store = evidence_store
        self._graph_store = graph_store

    def run(self, adapter: DiscoveryAdapter, *, tenant_id: str) -> PrototypeReport:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must be non-empty")

        observations = tuple(adapter.discover())

        # Validate the complete batch before mutating either store.
        for observation in observations:
            if observation.evidence.tenant_id != tenant_id:
                raise ValueError("discovery evidence tenant does not match run tenant")

        # Build all graph projections before persistence so normalization failures
        # cannot leave evidence partially stored.
        nodes = tuple(
            _node_from_observation(observation, tenant_id=tenant_id)
            for observation in observations
        )

        for observation in observations:
            self._evidence_store.append(observation.evidence)

        for node in nodes:
            self._graph_store.add_node(node)

        return PrototypeReport(
            observations=len(observations),
            evidence=len(observations),
            graph_nodes=len(nodes),
            tenant_id=tenant_id,
        )


def _node_from_observation(observation: Observation, *, tenant_id: str) -> GraphNode:
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
