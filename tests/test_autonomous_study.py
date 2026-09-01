from uuid import uuid4

import pytest

from orion.contracts import (
    Evidence,
    EvidenceKind,
    Observation,
    ObservationMode,
)
from orion.discovery.autonomous_study import (
    AutonomousStudyError,
    AutonomousStudyLimits,
    AutonomousStudyStopReason,
    run_autonomous_study,
)
from orion.discovery.planner import DiscoveryAuthorization
from orion.understanding.metadata import (
    MetadataUnderstanding,
    StructuralEntity,
    StructuralField,
)


def field(
    doctype,
    name,
    *,
    fieldtype="Data",
    options=None,
):
    return StructuralField(
        doctype=doctype,
        fieldname=name,
        fieldtype=fieldtype,
        label=None,
        options=options,
        required=False,
        read_only=False,
        hidden=False,
        unique=False,
    )


def entity(name, *, fields=()):
    return StructuralEntity(
        doctype=name,
        module=None,
        is_submittable=False,
        is_child_table=False,
        is_single=False,
        fields=tuple(fields),
        provenance_ids=(uuid4(),),
    )


def starting_understanding():
    return MetadataUnderstanding(
        tenant_id="customer-a",
        entities=(
            entity(
                "Company",
                fields=(
                    field(
                        "Company",
                        "customer",
                        fieldtype="Link",
                        options="Customer",
                    ),
                ),
            ),
        ),
    )


def metadata_observation(target, *, linked_target=None):
    fields = []

    if linked_target is not None:
        fields.append(
            {
                "fieldname": "next_link",
                "fieldtype": "Link",
                "options": linked_target,
            }
        )

    return Observation(
        mode=ObservationMode.READ_ONLY,
        evidence=Evidence(
            kind=EvidenceKind.METADATA,
            source="test-metadata",
            tenant_id="customer-a",
            payload={
                "doctype": target,
                "metadata": {
                    "docs": [
                        {
                            "name": target,
                            "fields": fields,
                        }
                    ]
                },
            },
        ),
    )


def record_observation(target):
    return Observation(
        mode=ObservationMode.READ_ONLY,
        evidence=Evidence(
            kind=EvidenceKind.API,
            source="test-records",
            tenant_id="customer-a",
            payload={
                "resource": target,
                "record": {"name": "ROW-1"},
            },
        ),
    )


def fail_reader(_target):
    raise AssertionError("reader must not be called")


def test_autonomous_study_expands_until_exhausted():
    calls = []

    def metadata_reader(target):
        calls.append(target)

        if target == "Customer":
            return (
                metadata_observation(
                    target,
                    linked_target="Territory",
                ),
            )

        return (metadata_observation(target),)

    report = run_autonomous_study(
        starting_understanding(),
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset(
                {"Customer", "Territory"}
            ),
        ),
        metadata_reader=metadata_reader,
        record_reader=fail_reader,
    )

    assert calls == ["Customer", "Territory"]
    assert report.cycles_completed == 2
    assert report.metadata_targets_studied == (
        "Customer",
        "Territory",
    )
    assert report.stop_reason is AutonomousStudyStopReason.EXHAUSTED
    assert [
        item.doctype
        for item in report.understanding.entities
    ] == [
        "Company",
        "Customer",
        "Territory",
    ]


def test_autonomous_study_does_not_widen_authorization():
    calls = []

    def metadata_reader(target):
        calls.append(target)
        return (
            metadata_observation(
                target,
                linked_target="Territory",
            ),
        )

    report = run_autonomous_study(
        starting_understanding(),
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset({"Customer"}),
        ),
        metadata_reader=metadata_reader,
        record_reader=fail_reader,
    )

    assert calls == ["Customer"]
    assert report.stop_reason is AutonomousStudyStopReason.EXHAUSTED
    assert "Territory" not in {
        item.doctype
        for item in report.understanding.entities
    }


def test_cycle_limit_stops_further_reads():
    calls = []

    def metadata_reader(target):
        calls.append(target)
        return (
            metadata_observation(
                target,
                linked_target="Territory",
            ),
        )

    report = run_autonomous_study(
        starting_understanding(),
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset(
                {"Customer", "Territory"}
            ),
        ),
        metadata_reader=metadata_reader,
        record_reader=fail_reader,
        limits=AutonomousStudyLimits(max_cycles=1),
    )

    assert calls == ["Customer"]
    assert report.cycles_completed == 1
    assert report.stop_reason is AutonomousStudyStopReason.CYCLE_LIMIT


def test_metadata_target_budget_stops_before_next_read():
    calls = []

    def metadata_reader(target):
        calls.append(target)
        return (
            metadata_observation(
                target,
                linked_target="Territory",
            ),
        )

    report = run_autonomous_study(
        starting_understanding(),
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset(
                {"Customer", "Territory"}
            ),
        ),
        metadata_reader=metadata_reader,
        record_reader=fail_reader,
        limits=AutonomousStudyLimits(
            max_metadata_targets=1,
        ),
    )

    assert calls == ["Customer"]
    assert report.metadata_targets_studied == ("Customer",)
    assert (
        report.stop_reason
        is AutonomousStudyStopReason.METADATA_TARGET_LIMIT
    )


def test_record_target_budget_prevents_any_cycle_read():
    calls = []

    def metadata_reader(target):
        calls.append(("metadata", target))
        return (metadata_observation(target),)

    def record_reader(target):
        calls.append(("records", target))
        return (record_observation(target),)

    report = run_autonomous_study(
        starting_understanding(),
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset({"Customer"}),
            record_targets=frozenset({"Company"}),
        ),
        metadata_reader=metadata_reader,
        record_reader=record_reader,
        limits=AutonomousStudyLimits(
            max_record_targets=0,
        ),
    )

    assert calls == []
    assert report.cycles_completed == 0
    assert (
        report.stop_reason
        is AutonomousStudyStopReason.RECORD_TARGET_LIMIT
    )


def test_record_resource_is_sampled_only_once():
    calls = []

    def record_reader(target):
        calls.append(target)
        return (record_observation(target),)

    report = run_autonomous_study(
        starting_understanding(),
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            record_targets=frozenset({"Company"}),
        ),
        metadata_reader=fail_reader,
        record_reader=record_reader,
    )

    assert calls == ["Company"]
    assert report.record_targets_sampled == ("Company",)
    assert report.sampled_records == frozenset({"Company"})
    assert report.stop_reason is AutonomousStudyStopReason.EXHAUSTED


def test_existing_sampled_records_are_not_repeated():
    report = run_autonomous_study(
        starting_understanding(),
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            record_targets=frozenset({"Company"}),
        ),
        metadata_reader=fail_reader,
        record_reader=fail_reader,
        already_sampled_records=frozenset({"Company"}),
    )

    assert report.cycles_completed == 0
    assert report.sampled_records == frozenset({"Company"})
    assert report.stop_reason is AutonomousStudyStopReason.EXHAUSTED


def test_empty_authorization_performs_zero_reads():
    report = run_autonomous_study(
        starting_understanding(),
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
        ),
        metadata_reader=fail_reader,
        record_reader=fail_reader,
    )

    assert report.cycles_completed == 0
    assert report.observation_count == 0
    assert report.stop_reason is AutonomousStudyStopReason.EXHAUSTED


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"max_cycles": 0}, "max_cycles"),
        ({"max_cycles": True}, "max_cycles"),
        ({"max_metadata_targets": -1}, "max_metadata_targets"),
        ({"max_record_targets": -1}, "max_record_targets"),
    ],
)
def test_rejects_invalid_controller_limits(kwargs, message):
    with pytest.raises(AutonomousStudyError, match=message):
        AutonomousStudyLimits(**kwargs)


def test_report_counts_observations_across_cycles():
    def metadata_reader(target):
        if target == "Customer":
            return (
                metadata_observation(
                    target,
                    linked_target="Territory",
                ),
            )

        return (metadata_observation(target),)

    report = run_autonomous_study(
        starting_understanding(),
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset(
                {"Customer", "Territory"}
            ),
        ),
        metadata_reader=metadata_reader,
        record_reader=fail_reader,
    )

    assert report.observation_count == 2
