"""Bridge read-only observations into ORION's canonical graph model."""

from __future__ import annotations

from collections.abc import Mapping

from ..contracts import Observation
from ..understanding.graph import GraphNode, GraphStatus, GraphStore, NodeType


def project_observations(store: GraphStore, observations: tuple[Observation, ...]) -> int:
    """Project observations as observed entities; never infer relationships."""
    added = 0
    seen_keys: set[tuple[str | None, str]] = set()
    for observation in observations:
        evidence = observation.evidence
        record = evidence.payload.get("record")
        if not isinstance(record, Mapping):
            continue
        resource = str(evidence.payload.get("resource", "unknown"))
        name = record.get("name")
        if not isinstance(name, str) or not name:
            continue
        identity = (evidence.tenant_id, f"{resource}:{name}")
        if identity in seen_keys:
            continue
        seen_keys.add(identity)
        store.add_node(GraphNode(
            node_type=NodeType.ENTITY,
            tenant_id=evidence.tenant_id,
            key=identity[1],
            attributes=dict(record),
            status=GraphStatus.OBSERVED,
            provenance_ids=(evidence.evidence_id,),
        ))
        added += 1
    return added
