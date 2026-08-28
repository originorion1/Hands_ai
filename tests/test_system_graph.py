from uuid import uuid4

import pytest

from orion.understanding.graph import (
    GraphInvariantError,
    GraphNode,
    GraphQuery,
    GraphRelationship,
    GraphStatus,
    GraphStore,
    NodeType,
    RelationshipType,
)


def test_graph_requires_existing_endpoints_and_preserves_tenant_boundary() -> None:
    store = GraphStore()
    source = GraphNode(NodeType.ENTITY, "tenant-a", "invoice")
    target = GraphNode(NodeType.PROCESS, "tenant-a", "approval")
    store.add_node(source)
    store.add_node(target)

    store.add_relationship(
        GraphRelationship(
            RelationshipType.REQUIRES,
            source.node_id,
            target.node_id,
            "tenant-a",
            status=GraphStatus.INFERRED,
        )
    )

    with pytest.raises(GraphInvariantError):
        store.add_relationship(
            GraphRelationship(
                RelationshipType.REQUIRES,
                source.node_id,
                target.node_id,
                "tenant-b",
            )
        )


def test_graph_path_is_bounded_and_tenant_scoped() -> None:
    store = GraphStore()
    a = GraphNode(NodeType.ENTITY, "tenant-a", "a")
    b = GraphNode(NodeType.PROCESS, "tenant-a", "b")
    c = GraphNode(NodeType.ACTION, "tenant-a", "c")
    other = GraphNode(NodeType.ACTION, "tenant-b", "other")
    for node in (a, b, c, other):
        store.add_node(node)

    store.add_relationship(GraphRelationship(RelationshipType.TRIGGERS, a.node_id, b.node_id, "tenant-a"))
    store.add_relationship(GraphRelationship(RelationshipType.TRIGGERS, b.node_id, c.node_id, "tenant-a"))

    paths = store.path(GraphQuery("tenant-a", source_id=a.node_id, max_depth=2, limit=10))
    assert any(path.node_ids == (a.node_id, b.node_id, c.node_id) for path in paths)
    assert all(other.node_id not in path.node_ids for path in paths)


def test_invalid_query_limits_fail_fast() -> None:
    with pytest.raises(ValueError):
        GraphQuery("tenant-a", source_id=uuid4(), limit=0)

    with pytest.raises(ValueError):
        GraphQuery("tenant-a", source_id=uuid4(), max_depth=-1)
