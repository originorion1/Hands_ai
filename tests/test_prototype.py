from orion.contracts import Evidence, EvidenceKind, Observation
from orion.prototype import PrototypeRunner, StaticObservationAdapter
from orion.stores.memory import InMemoryEvidenceStore
from orion.understanding.graph import GraphStore, NodeType


def test_vertical_slice_discovers_persists_and_projects() -> None:
    evidence = Evidence(
        kind=EvidenceKind.METADATA,
        source="authorized-fixture",
        payload={"key": "Invoice", "node_type": "entity"},
        tenant_id="customer-a",
    )
    adapter = StaticObservationAdapter([Observation(evidence=evidence)])
    evidence_store = InMemoryEvidenceStore()
    graph_store = GraphStore()

    report = PrototypeRunner(
        evidence_store=evidence_store,
        graph_store=graph_store,
    ).run(adapter, tenant_id="customer-a")

    assert report.observations == 1
    assert report.evidence == 1
    assert report.graph_nodes == 1
    assert evidence_store.query(tenant_id="customer-a") == (evidence,)

    # The node is reachable only through its tenant scope.
    node = next(iter(graph_store._nodes.values()))
    assert node.node_type is NodeType.ENTITY
    assert node.tenant_id == "customer-a"
    assert node.provenance_ids == (evidence.evidence_id,)


def test_vertical_slice_rejects_cross_tenant_evidence() -> None:
    evidence = Evidence(
        kind=EvidenceKind.METADATA,
        source="fixture",
        payload={"key": "Invoice"},
        tenant_id="customer-b",
    )
    adapter = StaticObservationAdapter([Observation(evidence=evidence)])
    runner = PrototypeRunner(
        evidence_store=InMemoryEvidenceStore(),
        graph_store=GraphStore(),
    )

    import pytest

    with pytest.raises(ValueError, match="tenant"):
        runner.run(adapter, tenant_id="customer-a")
