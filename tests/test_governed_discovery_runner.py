from uuid import uuid4

import pytest

from orion.contracts import (
    Evidence,
    EvidenceKind,
    Observation,
    ObservationMode,
)
from orion.discovery.governed_runner import (
    GovernedDiscoveryError,
    run_governed_discovery,
)
from orion.discovery.planner import (
    DiscoveryAuthorization,
    DiscoveryPlan,
    DiscoveryPlanItem,
    DiscoveryTargetKind,
)
from orion.understanding.metadata import (
    MetadataUnderstanding,
    StructuralEntity,
    StructuralField,
)


def understanding():
    evidence_id = uuid4()

    company = StructuralEntity(
        doctype="Company",
        module=None,
        is_submittable=False,
        is_child_table=False,
        is_single=False,
        fields=(
            StructuralField(
                doctype="Company",
                fieldname="customer",
                fieldtype="Link",
                label=None,
                options="Customer",
                required=False,
                read_only=False,
                hidden=False,
                unique=False,
            ),
        ),
        provenance_ids=(evidence_id,),
    )

    return (
        MetadataUnderstanding(
            tenant_id="customer-a",
            entities=(company,),
        ),
        evidence_id,
    )


def item(kind, target, evidence_id):
    return DiscoveryPlanItem(
        kind=kind,
        target=target,
        rationale="test",
        provenance_ids=(evidence_id,),
    )


def metadata_observation(
    target="Customer",
    *,
    tenant_id="customer-a",
    mode=ObservationMode.READ_ONLY,
    kind=EvidenceKind.METADATA,
):
    return Observation(
        mode=mode,
        evidence=Evidence(
            kind=kind,
            source="test-metadata",
            tenant_id=tenant_id,
            payload={
                "doctype": target,
                "metadata": {"docs": [{"name": target}]},
            },
        ),
    )


def record_observation(
    target="Company",
    *,
    tenant_id="customer-a",
    mode=ObservationMode.READ_ONLY,
    kind=EvidenceKind.API,
):
    return Observation(
        mode=mode,
        evidence=Evidence(
            kind=kind,
            source="test-records",
            tenant_id=tenant_id,
            payload={
                "resource": target,
                "record": {"name": "ROW-1"},
            },
        ),
    )


def authorization(**kwargs):
    values = {
        "tenant_id": "customer-a",
        "metadata_targets": frozenset({"Customer"}),
        "record_targets": frozenset({"Company"}),
        "max_targets": 10,
    }
    values.update(kwargs)
    return DiscoveryAuthorization(**values)


def fail_reader(_target):
    raise AssertionError("reader must not be called")


def test_runs_authorized_metadata_read():
    current, evidence_id = understanding()
    calls = []

    plan = DiscoveryPlan(
        tenant_id="customer-a",
        items=(
            item(
                DiscoveryTargetKind.METADATA,
                "Customer",
                evidence_id,
            ),
        ),
    )

    def metadata_reader(target):
        calls.append(target)
        return (metadata_observation(target),)

    report = run_governed_discovery(
        plan,
        authorization=authorization(),
        understanding=current,
        metadata_reader=metadata_reader,
        record_reader=fail_reader,
    )

    assert calls == ["Customer"]
    assert report.observation_count == 1
    assert report.observations[0].mode is ObservationMode.READ_ONLY


def test_runs_authorized_record_read():
    current, evidence_id = understanding()
    calls = []

    plan = DiscoveryPlan(
        tenant_id="customer-a",
        items=(
            item(
                DiscoveryTargetKind.RECORDS,
                "Company",
                evidence_id,
            ),
        ),
    )

    def record_reader(target):
        calls.append(target)
        return (record_observation(target),)

    report = run_governed_discovery(
        plan,
        authorization=authorization(),
        understanding=current,
        metadata_reader=fail_reader,
        record_reader=record_reader,
    )

    assert calls == ["Company"]
    assert report.observation_count == 1


def test_preserves_plan_order():
    current, evidence_id = understanding()
    calls = []

    plan = DiscoveryPlan(
        tenant_id="customer-a",
        items=(
            item(
                DiscoveryTargetKind.METADATA,
                "Customer",
                evidence_id,
            ),
            item(
                DiscoveryTargetKind.RECORDS,
                "Company",
                evidence_id,
            ),
        ),
    )

    def metadata_reader(target):
        calls.append(("metadata", target))
        return (metadata_observation(target),)

    def record_reader(target):
        calls.append(("records", target))
        return (record_observation(target),)

    run_governed_discovery(
        plan,
        authorization=authorization(),
        understanding=current,
        metadata_reader=metadata_reader,
        record_reader=record_reader,
    )

    assert calls == [
        ("metadata", "Customer"),
        ("records", "Company"),
    ]


def test_rejects_cross_tenant_plan_before_any_read():
    current, evidence_id = understanding()

    plan = DiscoveryPlan(
        tenant_id="customer-b",
        items=(
            item(
                DiscoveryTargetKind.METADATA,
                "Customer",
                evidence_id,
            ),
        ),
    )

    with pytest.raises(
        GovernedDiscoveryError,
        match="tenant boundary",
    ):
        run_governed_discovery(
            plan,
            authorization=authorization(),
            understanding=current,
            metadata_reader=fail_reader,
            record_reader=fail_reader,
        )


def test_rejects_unauthorized_target_before_any_read():
    current, evidence_id = understanding()

    plan = DiscoveryPlan(
        tenant_id="customer-a",
        items=(
            item(
                DiscoveryTargetKind.METADATA,
                "Customer",
                evidence_id,
            ),
        ),
    )

    with pytest.raises(
        GovernedDiscoveryError,
        match="not authorized",
    ):
        run_governed_discovery(
            plan,
            authorization=authorization(
                metadata_targets=frozenset(),
            ),
            understanding=current,
            metadata_reader=fail_reader,
            record_reader=fail_reader,
        )


def test_rejects_entire_plan_before_reads_if_later_item_is_invalid():
    current, evidence_id = understanding()
    calls = []

    plan = DiscoveryPlan(
        tenant_id="customer-a",
        items=(
            item(
                DiscoveryTargetKind.RECORDS,
                "Company",
                evidence_id,
            ),
            item(
                DiscoveryTargetKind.METADATA,
                "Supplier",
                evidence_id,
            ),
        ),
    )

    def record_reader(target):
        calls.append(target)
        return (record_observation(target),)

    with pytest.raises(
        GovernedDiscoveryError,
        match="not authorized",
    ):
        run_governed_discovery(
            plan,
            authorization=authorization(),
            understanding=current,
            metadata_reader=fail_reader,
            record_reader=record_reader,
        )

    assert calls == []


def test_rejects_plan_over_target_bound_before_any_read():
    current, evidence_id = understanding()

    plan = DiscoveryPlan(
        tenant_id="customer-a",
        items=(
            item(
                DiscoveryTargetKind.METADATA,
                "Customer",
                evidence_id,
            ),
            item(
                DiscoveryTargetKind.RECORDS,
                "Company",
                evidence_id,
            ),
        ),
    )

    with pytest.raises(
        GovernedDiscoveryError,
        match="target bound",
    ):
        run_governed_discovery(
            plan,
            authorization=authorization(max_targets=1),
            understanding=current,
            metadata_reader=fail_reader,
            record_reader=fail_reader,
        )


def test_rejects_duplicate_plan_items_before_any_read():
    current, evidence_id = understanding()

    duplicate = item(
        DiscoveryTargetKind.RECORDS,
        "Company",
        evidence_id,
    )

    plan = DiscoveryPlan(
        tenant_id="customer-a",
        items=(duplicate, duplicate),
    )

    with pytest.raises(
        GovernedDiscoveryError,
        match="duplicate",
    ):
        run_governed_discovery(
            plan,
            authorization=authorization(),
            understanding=current,
            metadata_reader=fail_reader,
            record_reader=fail_reader,
        )


def test_rejects_record_read_before_structure_is_understood():
    current, evidence_id = understanding()

    plan = DiscoveryPlan(
        tenant_id="customer-a",
        items=(
            item(
                DiscoveryTargetKind.RECORDS,
                "Customer",
                evidence_id,
            ),
        ),
    )

    with pytest.raises(
        GovernedDiscoveryError,
        match="structure is not understood",
    ):
        run_governed_discovery(
            plan,
            authorization=authorization(
                record_targets=frozenset({"Customer"}),
            ),
            understanding=current,
            metadata_reader=fail_reader,
            record_reader=fail_reader,
        )


def test_rejects_metadata_not_supported_by_observed_structure():
    current, evidence_id = understanding()

    plan = DiscoveryPlan(
        tenant_id="customer-a",
        items=(
            item(
                DiscoveryTargetKind.METADATA,
                "Supplier",
                evidence_id,
            ),
        ),
    )

    with pytest.raises(
        GovernedDiscoveryError,
        match="not supported",
    ):
        run_governed_discovery(
            plan,
            authorization=authorization(
                metadata_targets=frozenset({"Supplier"}),
            ),
            understanding=current,
            metadata_reader=fail_reader,
            record_reader=fail_reader,
        )


def test_rejects_unknown_structural_provenance():
    current, _evidence_id = understanding()

    plan = DiscoveryPlan(
        tenant_id="customer-a",
        items=(
            item(
                DiscoveryTargetKind.METADATA,
                "Customer",
                uuid4(),
            ),
        ),
    )

    with pytest.raises(
        GovernedDiscoveryError,
        match="unknown structural provenance",
    ):
        run_governed_discovery(
            plan,
            authorization=authorization(),
            understanding=current,
            metadata_reader=fail_reader,
            record_reader=fail_reader,
        )


def test_rejects_shadow_observation():
    current, evidence_id = understanding()

    plan = DiscoveryPlan(
        tenant_id="customer-a",
        items=(
            item(
                DiscoveryTargetKind.METADATA,
                "Customer",
                evidence_id,
            ),
        ),
    )

    def metadata_reader(target):
        return (
            metadata_observation(
                target,
                mode=ObservationMode.SHADOW,
            ),
        )

    with pytest.raises(
        GovernedDiscoveryError,
        match="non-read-only",
    ):
        run_governed_discovery(
            plan,
            authorization=authorization(),
            understanding=current,
            metadata_reader=metadata_reader,
            record_reader=fail_reader,
        )


def test_rejects_reader_cross_tenant_observation():
    current, evidence_id = understanding()

    plan = DiscoveryPlan(
        tenant_id="customer-a",
        items=(
            item(
                DiscoveryTargetKind.RECORDS,
                "Company",
                evidence_id,
            ),
        ),
    )

    def record_reader(target):
        return (
            record_observation(
                target,
                tenant_id="customer-b",
            ),
        )

    with pytest.raises(
        GovernedDiscoveryError,
        match="tenant boundary",
    ):
        run_governed_discovery(
            plan,
            authorization=authorization(),
            understanding=current,
            metadata_reader=fail_reader,
            record_reader=record_reader,
        )


def test_rejects_wrong_evidence_kind():
    current, evidence_id = understanding()

    plan = DiscoveryPlan(
        tenant_id="customer-a",
        items=(
            item(
                DiscoveryTargetKind.METADATA,
                "Customer",
                evidence_id,
            ),
        ),
    )

    def metadata_reader(target):
        return (
            metadata_observation(
                target,
                kind=EvidenceKind.API,
            ),
        )

    with pytest.raises(
        GovernedDiscoveryError,
        match="non-metadata",
    ):
        run_governed_discovery(
            plan,
            authorization=authorization(),
            understanding=current,
            metadata_reader=metadata_reader,
            record_reader=fail_reader,
        )


def test_rejects_observation_for_wrong_target():
    current, evidence_id = understanding()

    plan = DiscoveryPlan(
        tenant_id="customer-a",
        items=(
            item(
                DiscoveryTargetKind.METADATA,
                "Customer",
                evidence_id,
            ),
        ),
    )

    def metadata_reader(_target):
        return (metadata_observation("Supplier"),)

    with pytest.raises(
        GovernedDiscoveryError,
        match="does not match plan",
    ):
        run_governed_discovery(
            plan,
            authorization=authorization(),
            understanding=current,
            metadata_reader=metadata_reader,
            record_reader=fail_reader,
        )


def test_rejects_empty_metadata_result():
    current, evidence_id = understanding()

    plan = DiscoveryPlan(
        tenant_id="customer-a",
        items=(
            item(
                DiscoveryTargetKind.METADATA,
                "Customer",
                evidence_id,
            ),
        ),
    )

    def metadata_reader(_target):
        return ()

    with pytest.raises(
        GovernedDiscoveryError,
        match="exactly one",
    ):
        run_governed_discovery(
            plan,
            authorization=authorization(),
            understanding=current,
            metadata_reader=metadata_reader,
            record_reader=fail_reader,
        )
