from uuid import uuid4

import pytest

from orion.contracts import (
    Evidence,
    EvidenceKind,
    Observation,
    ObservationMode,
)
from orion.discovery.planner import DiscoveryAuthorization
from orion.discovery.study_cycle import run_study_cycle
from orion.understanding.metadata import (
    MetadataUnderstanding,
    MetadataUnderstandingError,
    StructuralEntity,
    StructuralField,
    merge_metadata_understandings,
)


def structural_field(
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


def structural_entity(
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


def starting_understanding():
    company = structural_entity(
        "Company",
        fields=(
            structural_field(
                "Company",
                "customer",
                fieldtype="Link",
                options="Customer",
            ),
        ),
    )

    return MetadataUnderstanding(
        tenant_id="customer-a",
        entities=(company,),
    )


def metadata_observation(target, *, docs=None):
    if docs is None:
        docs = [
            {
                "name": target,
                "fields": [
                    {
                        "fieldname": "name_field",
                        "fieldtype": "Data",
                    }
                ],
            }
        ]

    return Observation(
        mode=ObservationMode.READ_ONLY,
        evidence=Evidence(
            kind=EvidenceKind.METADATA,
            source="test-metadata",
            tenant_id="customer-a",
            payload={
                "doctype": target,
                "metadata": {"docs": docs},
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


def test_merge_adds_new_structural_entity():
    current = starting_understanding()
    customer = structural_entity("Customer")

    merged = merge_metadata_understandings(
        current,
        MetadataUnderstanding(
            tenant_id="customer-a",
            entities=(customer,),
        ),
    )

    assert [entity.doctype for entity in merged.entities] == [
        "Company",
        "Customer",
    ]


def test_merge_preserves_provenance_for_same_structure():
    first_id = uuid4()
    second_id = uuid4()

    first = structural_entity(
        "Company",
        provenance_ids=(first_id,),
    )
    second = structural_entity(
        "Company",
        provenance_ids=(second_id,),
    )

    merged = merge_metadata_understandings(
        MetadataUnderstanding(
            tenant_id="customer-a",
            entities=(first,),
        ),
        MetadataUnderstanding(
            tenant_id="customer-a",
            entities=(second,),
        ),
    )

    assert merged.entities[0].provenance_ids == (
        first_id,
        second_id,
    )


def test_merge_rejects_cross_tenant_understanding():
    with pytest.raises(
        MetadataUnderstandingError,
        match="tenant boundary",
    ):
        merge_metadata_understandings(
            MetadataUnderstanding(
                tenant_id="customer-a",
                entities=(),
            ),
            MetadataUnderstanding(
                tenant_id="customer-b",
                entities=(),
            ),
        )


def test_merge_rejects_conflicting_structure():
    first = structural_entity(
        "Company",
        fields=(
            structural_field(
                "Company",
                "company_name",
            ),
        ),
    )

    conflicting = structural_entity(
        "Company",
        fields=(
            structural_field(
                "Company",
                "different_field",
            ),
        ),
    )

    with pytest.raises(
        MetadataUnderstandingError,
        match="conflicting metadata",
    ):
        merge_metadata_understandings(
            MetadataUnderstanding(
                tenant_id="customer-a",
                entities=(first,),
            ),
            MetadataUnderstanding(
                tenant_id="customer-a",
                entities=(conflicting,),
            ),
        )


def test_cycle_expands_authorized_metadata_once():
    current = starting_understanding()
    calls = []

    def metadata_reader(target):
        calls.append(("metadata", target))
        return (metadata_observation(target),)

    result = run_study_cycle(
        current,
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset({"Customer"}),
        ),
        metadata_reader=metadata_reader,
        record_reader=fail_reader,
    )

    assert calls == [("metadata", "Customer")]
    assert [entity.doctype for entity in result.understanding.entities] == [
        "Company",
        "Customer",
    ]
    assert len(result.metadata_observations) == 1
    assert result.record_observations == ()
    assert result.sampled_records == frozenset()


def test_cycle_samples_records_only_for_understood_resource():
    current = starting_understanding()
    calls = []

    def record_reader(target):
        calls.append(("records", target))
        return (record_observation(target),)

    result = run_study_cycle(
        current,
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            record_targets=frozenset({"Company"}),
        ),
        metadata_reader=fail_reader,
        record_reader=record_reader,
    )

    assert calls == [("records", "Company")]
    assert result.understanding == current
    assert len(result.record_observations) == 1
    assert result.sampled_records == frozenset({"Company"})


def test_cycle_metadata_precedes_record_sampling():
    current = starting_understanding()
    calls = []

    def metadata_reader(target):
        calls.append(("metadata", target))
        return (metadata_observation(target),)

    def record_reader(target):
        calls.append(("records", target))
        return (record_observation(target),)

    result = run_study_cycle(
        current,
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset({"Customer"}),
            record_targets=frozenset({"Company"}),
        ),
        metadata_reader=metadata_reader,
        record_reader=record_reader,
    )

    assert calls == [
        ("metadata", "Customer"),
        ("records", "Company"),
    ]
    assert result.sampled_records == frozenset({"Company"})


def test_cycle_empty_authorization_performs_zero_reads():
    current = starting_understanding()

    result = run_study_cycle(
        current,
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
        ),
        metadata_reader=fail_reader,
        record_reader=fail_reader,
    )

    assert result.plan.is_empty
    assert result.discovery.observation_count == 0
    assert result.understanding == current


def test_cycle_does_not_repeat_already_sampled_record():
    current = starting_understanding()

    result = run_study_cycle(
        current,
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            record_targets=frozenset({"Company"}),
        ),
        metadata_reader=fail_reader,
        record_reader=fail_reader,
        already_sampled_records=frozenset({"Company"}),
    )

    assert result.plan.is_empty
    assert result.sampled_records == frozenset({"Company"})


def test_record_observations_do_not_become_structural_understanding():
    current = starting_understanding()

    def record_reader(target):
        return (record_observation(target),)

    result = run_study_cycle(
        current,
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            record_targets=frozenset({"Company"}),
        ),
        metadata_reader=fail_reader,
        record_reader=record_reader,
    )

    assert result.understanding == current


def test_metadata_bundle_conflict_fails_closed():
    current = starting_understanding()

    conflicting_docs = [
        {
            "name": "Customer",
            "fields": [],
        },
        {
            "name": "Company",
            "fields": [
                {
                    "fieldname": "unexpected",
                    "fieldtype": "Data",
                }
            ],
        },
    ]

    def metadata_reader(target):
        return (
            metadata_observation(
                target,
                docs=conflicting_docs,
            ),
        )

    with pytest.raises(
        MetadataUnderstandingError,
        match="conflicting metadata",
    ):
        run_study_cycle(
            current,
            authorization=DiscoveryAuthorization(
                tenant_id="customer-a",
                metadata_targets=frozenset({"Customer"}),
            ),
            metadata_reader=metadata_reader,
            record_reader=fail_reader,
        )


def test_metadata_conflict_prevents_later_record_read():
    current = starting_understanding()
    calls = []

    conflicting_docs = [
        {
            "name": "Customer",
            "fields": [],
        },
        {
            "name": "Company",
            "fields": [
                {
                    "fieldname": "conflicting_field",
                    "fieldtype": "Data",
                }
            ],
        },
    ]

    def metadata_reader(target):
        calls.append(("metadata", target))
        return (
            metadata_observation(
                target,
                docs=conflicting_docs,
            ),
        )

    def record_reader(target):
        calls.append(("records", target))
        return (record_observation(target),)

    with pytest.raises(
        MetadataUnderstandingError,
        match="conflicting metadata",
    ):
        run_study_cycle(
            current,
            authorization=DiscoveryAuthorization(
                tenant_id="customer-a",
                metadata_targets=frozenset({"Customer"}),
                record_targets=frozenset({"Company"}),
            ),
            metadata_reader=metadata_reader,
            record_reader=record_reader,
        )

    assert calls == [
        ("metadata", "Customer"),
    ]
