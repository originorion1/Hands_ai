from uuid import uuid4

import pytest

from orion.discovery.planner import (
    DiscoveryAuthorization,
    DiscoveryPlanError,
    DiscoveryTargetKind,
    plan_authorized_discovery,
)
from orion.understanding.metadata import (
    MetadataUnderstanding,
    StructuralEntity,
    StructuralField,
)


def field(
    name,
    *,
    fieldtype="Link",
    options=None,
):
    return StructuralField(
        doctype="Company",
        fieldname=name,
        fieldtype=fieldtype,
        label=None,
        options=options,
        required=False,
        read_only=False,
        hidden=False,
        unique=False,
    )


def entity(
    name,
    *,
    fields=(),
    provenance_ids=None,
):
    return StructuralEntity(
        doctype=name,
        module=None,
        is_submittable=False,
        is_child_table=False,
        is_single=False,
        fields=tuple(fields),
        provenance_ids=(
            tuple(provenance_ids)
            if provenance_ids is not None
            else (uuid4(),)
        ),
    )


def company_understanding(*, extra_entities=()):
    company = entity(
        "Company",
        fields=(
            field("customer", options="Customer"),
            field("currency", options="Currency"),
            field(
                "accounts",
                fieldtype="Table",
                options="Company Account",
            ),
        ),
    )

    return MetadataUnderstanding(
        tenant_id="customer-a",
        entities=(company,) + tuple(extra_entities),
    )


def test_plans_only_explicitly_allowlisted_metadata():
    understanding = company_understanding()

    plan = plan_authorized_discovery(
        understanding,
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset({"Customer"}),
        ),
    )

    assert len(plan.items) == 1
    assert plan.items[0].kind is DiscoveryTargetKind.METADATA
    assert plan.items[0].target == "Customer"


def test_does_not_plan_unallowlisted_reference():
    understanding = company_understanding()

    plan = plan_authorized_discovery(
        understanding,
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
        ),
    )

    assert plan.is_empty


def test_does_not_replan_metadata_already_understood():
    understanding = company_understanding(
        extra_entities=(entity("Customer"),),
    )

    plan = plan_authorized_discovery(
        understanding,
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset({"Customer"}),
        ),
    )

    assert plan.is_empty


def test_non_relationship_options_do_not_propose_metadata_targets():
    understanding = MetadataUnderstanding(
        tenant_id="customer-a",
        entities=(
            entity(
                "Company",
                fields=(
                    field(
                        "status",
                        fieldtype="Select",
                        options="Open\nClosed",
                    ),
                    field(
                        "reference_note",
                        fieldtype="Data",
                        options="Customer",
                    ),
                ),
            ),
        ),
    )

    plan = plan_authorized_discovery(
        understanding,
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset({"Customer"}),
        ),
    )

    assert plan.is_empty


def test_plans_records_only_after_structure_is_understood():
    understanding = company_understanding()

    plan = plan_authorized_discovery(
        understanding,
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            record_targets=frozenset({"Company"}),
        ),
    )

    assert len(plan.items) == 1
    assert plan.items[0].kind is DiscoveryTargetKind.RECORDS
    assert plan.items[0].target == "Company"


def test_does_not_plan_records_for_unknown_structure():
    understanding = company_understanding()

    plan = plan_authorized_discovery(
        understanding,
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            record_targets=frozenset({"Customer"}),
        ),
    )

    assert plan.is_empty


def test_metadata_expansion_precedes_record_sampling():
    understanding = company_understanding()

    plan = plan_authorized_discovery(
        understanding,
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset({"Customer"}),
            record_targets=frozenset({"Company"}),
        ),
    )

    assert [item.kind for item in plan.items] == [
        DiscoveryTargetKind.METADATA,
        DiscoveryTargetKind.RECORDS,
    ]


def test_respects_max_target_bound():
    understanding = company_understanding()

    plan = plan_authorized_discovery(
        understanding,
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset(
                {
                    "Customer",
                    "Currency",
                    "Company Account",
                }
            ),
            record_targets=frozenset({"Company"}),
            max_targets=2,
        ),
    )

    assert len(plan.items) == 2
    assert all(
        item.kind is DiscoveryTargetKind.METADATA
        for item in plan.items
    )


def test_plan_is_deterministic():
    understanding = company_understanding()

    authorization = DiscoveryAuthorization(
        tenant_id="customer-a",
        metadata_targets=frozenset(
            {"Customer", "Currency", "Company Account"}
        ),
        record_targets=frozenset({"Company"}),
    )

    first = plan_authorized_discovery(
        understanding,
        authorization=authorization,
    )

    second = plan_authorized_discovery(
        understanding,
        authorization=authorization,
    )

    assert first == second


def test_preserves_structural_provenance():
    evidence_id = uuid4()

    understanding = MetadataUnderstanding(
        tenant_id="customer-a",
        entities=(
            entity(
                "Company",
                fields=(field("customer", options="Customer"),),
                provenance_ids=(evidence_id,),
            ),
        ),
    )

    plan = plan_authorized_discovery(
        understanding,
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset({"Customer"}),
        ),
    )

    assert plan.items[0].provenance_ids == (evidence_id,)


def test_skips_already_sampled_record_resource():
    understanding = company_understanding()

    plan = plan_authorized_discovery(
        understanding,
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            record_targets=frozenset({"Company"}),
        ),
        already_sampled_records=frozenset({"Company"}),
    )

    assert plan.is_empty


def test_rejects_cross_tenant_authorization():
    understanding = company_understanding()

    with pytest.raises(
        DiscoveryPlanError,
        match="tenant boundary",
    ):
        plan_authorized_discovery(
            understanding,
            authorization=DiscoveryAuthorization(
                tenant_id="customer-b",
                metadata_targets=frozenset({"Customer"}),
            ),
        )


def test_rejects_wildcard_authorization():
    with pytest.raises(
        DiscoveryPlanError,
        match="wildcard",
    ):
        DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset({"*"}),
        )


def test_rejects_control_character_authorization_target():
    with pytest.raises(
        DiscoveryPlanError,
        match="control characters",
    ):
        DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset({"Open\nClosed"}),
        )
