from datetime import UTC, datetime

import pytest

from orion.contracts import (
    Evidence,
    EvidenceKind,
    Observation,
    ObservationMode,
)
from orion.understanding.graph import (
    GraphStatus,
    GraphStore,
    NodeType,
)
from orion.understanding.metadata import (
    MetadataUnderstandingError,
    build_metadata_understanding,
    project_metadata_understanding,
)


def metadata_observation(
    *,
    tenant_id="customer-a",
    requested="Company",
    metadata=None,
    kind=EvidenceKind.METADATA,
    mode=ObservationMode.READ_ONLY,
):
    if metadata is None:
        metadata = {
            "docs": [
                {
                    "name": "Company",
                    "module": "Setup",
                    "is_submittable": 0,
                    "istable": 0,
                    "issingle": 0,
                    "fields": [
                        {
                            "fieldname": "company_name",
                            "fieldtype": "Data",
                            "label": "Company Name",
                            "reqd": 1,
                        },
                        {
                            "fieldname": "accounts",
                            "fieldtype": "Table",
                            "options": "Company Account",
                        },
                        {
                            "fieldname": "default_currency",
                            "fieldtype": "Link",
                            "options": "Currency",
                        },
                    ],
                },
                {
                    "name": "Company Account",
                    "module": "Setup",
                    "istable": 1,
                    "fields": [
                        {
                            "fieldname": "account",
                            "fieldtype": "Link",
                            "options": "Account",
                        }
                    ],
                },
            ]
        }

    return Observation(
        mode=mode,
        evidence=Evidence(
            kind=kind,
            source="erpnext-metadata-read-only",
            tenant_id=tenant_id,
            observed_at=datetime.now(UTC),
            payload={
                "doctype": requested,
                "metadata": metadata,
            },
        ),
    )


def test_builds_tenant_scoped_structural_understanding():
    observation = metadata_observation()

    understanding = build_metadata_understanding(
        (observation,),
        tenant_id="customer-a",
    )

    assert understanding.tenant_id == "customer-a"
    assert [entity.doctype for entity in understanding.entities] == [
        "Company",
        "Company Account",
    ]

    company = understanding.entities[0]

    assert company.module == "Setup"
    assert company.is_child_table is False
    assert len(company.fields) == 3

    company_name = next(
        field
        for field in company.fields
        if field.fieldname == "company_name"
    )

    assert company_name.fieldtype == "Data"
    assert company_name.required is True


def test_supports_message_docs_compatibility_shape():
    observation = metadata_observation(
        metadata={
            "message": {
                "docs": [
                    {
                        "name": "Company",
                        "fields": [],
                    }
                ]
            }
        }
    )

    understanding = build_metadata_understanding(
        (observation,),
        tenant_id="customer-a",
    )

    assert len(understanding.entities) == 1
    assert understanding.entities[0].doctype == "Company"


def test_rejects_cross_tenant_metadata():
    observation = metadata_observation(
        tenant_id="customer-b",
    )

    with pytest.raises(
        MetadataUnderstandingError,
        match="tenant boundary",
    ):
        build_metadata_understanding(
            (observation,),
            tenant_id="customer-a",
        )


def test_rejects_non_metadata_evidence():
    observation = metadata_observation(
        kind=EvidenceKind.API,
    )

    with pytest.raises(
        MetadataUnderstandingError,
        match="METADATA",
    ):
        build_metadata_understanding(
            (observation,),
            tenant_id="customer-a",
        )


def test_rejects_non_read_only_observation():
    observation = metadata_observation(
        mode=ObservationMode.SHADOW,
    )

    with pytest.raises(
        MetadataUnderstandingError,
        match="READ_ONLY",
    ):
        build_metadata_understanding(
            (observation,),
            tenant_id="customer-a",
        )


def test_rejects_missing_docs_container():
    observation = metadata_observation(
        metadata={"unexpected": {}},
    )

    with pytest.raises(
        MetadataUnderstandingError,
        match="docs list",
    ):
        build_metadata_understanding(
            (observation,),
            tenant_id="customer-a",
        )


def test_rejects_bundle_missing_requested_doctype():
    observation = metadata_observation(
        requested="Company",
        metadata={
            "docs": [
                {
                    "name": "Something Else",
                    "fields": [],
                }
            ]
        },
    )

    with pytest.raises(
        MetadataUnderstandingError,
        match="requested DocType missing",
    ):
        build_metadata_understanding(
            (observation,),
            tenant_id="customer-a",
        )


def test_rejects_duplicate_fields():
    observation = metadata_observation(
        metadata={
            "docs": [
                {
                    "name": "Company",
                    "fields": [
                        {
                            "fieldname": "name1",
                            "fieldtype": "Data",
                        },
                        {
                            "fieldname": "name1",
                            "fieldtype": "Data",
                        },
                    ],
                }
            ]
        },
    )

    with pytest.raises(
        MetadataUnderstandingError,
        match="duplicate field",
    ):
        build_metadata_understanding(
            (observation,),
            tenant_id="customer-a",
        )


def test_projects_metadata_into_vendor_neutral_graph():
    observation = metadata_observation()

    understanding = build_metadata_understanding(
        (observation,),
        tenant_id="customer-a",
    )

    graph = GraphStore()

    report = project_metadata_understanding(
        graph,
        understanding,
    )

    assert report.added_nodes == 6
    assert report.added_relationships == 5

    nodes = [
        graph.get_node(
            node_id,
            tenant_id="customer-a",
        )
        for node_id in report.node_ids
    ]

    assert all(node is not None for node in nodes)
    assert all(node.status is GraphStatus.OBSERVED for node in nodes)

    assert sum(
        node.node_type is NodeType.COMPONENT
        for node in nodes
        if node is not None
    ) == 2

    assert sum(
        node.node_type is NodeType.ATTRIBUTE
        for node in nodes
        if node is not None
    ) == 4


def test_projection_is_idempotent():
    observation = metadata_observation()

    understanding = build_metadata_understanding(
        (observation,),
        tenant_id="customer-a",
    )

    graph = GraphStore()

    first = project_metadata_understanding(
        graph,
        understanding,
    )

    second = project_metadata_understanding(
        graph,
        understanding,
    )

    assert first.added_nodes == 6
    assert first.added_relationships == 5
    assert second.added_nodes == 0
    assert second.added_relationships == 0


def test_scoped_understanding_filters_incidental_bundle_entities():
    observation = metadata_observation()

    understanding = build_metadata_understanding(
        (observation,),
        tenant_id="customer-a",
        allowed_doctypes=frozenset({"Company"}),
    )

    assert [
        entity.doctype
        for entity in understanding.entities
    ] == ["Company"]


def test_scoped_understanding_rejects_requested_target_outside_scope():
    observation = metadata_observation()

    with pytest.raises(
        MetadataUnderstandingError,
        match="outside allowed structural scope",
    ):
        build_metadata_understanding(
            (observation,),
            tenant_id="customer-a",
            allowed_doctypes=frozenset(
                {"Company Account"}
            ),
        )
