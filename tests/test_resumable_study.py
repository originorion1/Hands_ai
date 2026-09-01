from datetime import UTC, datetime
from uuid import uuid4

import pytest

from orion.contracts import (
    Evidence,
    EvidenceKind,
    Observation,
    ObservationMode,
)
from orion.discovery.checkpoint import (
    StudyCheckpoint,
    StudyCheckpointError,
)
from orion.discovery.governed_runner import GovernedDiscoveryError
from orion.discovery.planner import DiscoveryAuthorization
from orion.discovery.resumable_study import (
    ResumableStudySessionError,
    run_resumable_study_session,
)
from orion.stores.sqlite_checkpoint import SQLiteStudyCheckpointStore
from orion.understanding.metadata import (
    MetadataUnderstanding,
    StructuralEntity,
    StructuralField,
)

FIXED_TIME = datetime(
    2026,
    9,
    1,
    20,
    0,
    tzinfo=UTC,
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


def starting_understanding(*, tenant_id="customer-a"):
    return MetadataUnderstanding(
        tenant_id=tenant_id,
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


def metadata_observation(
    target,
    *,
    linked_target=None,
):
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


def fixed_clock():
    return FIXED_TIME


def test_session_checkpoints_every_completed_cycle(tmp_path):
    store = SQLiteStudyCheckpointStore(
        tmp_path / "checkpoints.sqlite3"
    )

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

    result = run_resumable_study_session(
        starting_understanding(),
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset(
                {"Customer", "Territory"}
            ),
        ),
        checkpoint_store=store,
        metadata_reader=metadata_reader,
        record_reader=fail_reader,
        clock=fixed_clock,
    )

    assert calls == ["Customer", "Territory"]
    assert result.study.cycles_completed == 2
    assert result.checkpoints_written == 2
    assert result.resumed_from_sequence is None

    latest = store.load_latest(
        tenant_id="customer-a"
    )

    assert latest is not None
    assert latest.sequence == 2
    assert latest.metadata_targets_studied == (
        "Customer",
        "Territory",
    )


def test_later_cycle_failure_preserves_previous_checkpoint(
    tmp_path,
):
    store = SQLiteStudyCheckpointStore(
        tmp_path / "checkpoints.sqlite3"
    )

    def metadata_reader(target):
        if target == "Customer":
            return (
                metadata_observation(
                    target,
                    linked_target="Territory",
                ),
            )

        raise RuntimeError("simulated upstream failure")

    with pytest.raises(
        GovernedDiscoveryError,
        match="read-only discovery failed",
    ):
        run_resumable_study_session(
            starting_understanding(),
            authorization=DiscoveryAuthorization(
                tenant_id="customer-a",
                metadata_targets=frozenset(
                    {"Customer", "Territory"}
                ),
            ),
            checkpoint_store=store,
            metadata_reader=metadata_reader,
            record_reader=fail_reader,
            clock=fixed_clock,
        )

    latest = store.load_latest(
        tenant_id="customer-a"
    )

    assert latest is not None
    assert latest.sequence == 1
    assert latest.metadata_targets_studied == ("Customer",)
    assert {
        item.doctype
        for item in latest.understanding.entities
    } == {
        "Company",
        "Customer",
    }


def test_restart_after_failure_continues_from_checkpoint(
    tmp_path,
):
    path = tmp_path / "checkpoints.sqlite3"
    first_store = SQLiteStudyCheckpointStore(path)

    def failing_reader(target):
        if target == "Customer":
            return (
                metadata_observation(
                    target,
                    linked_target="Territory",
                ),
            )

        raise RuntimeError("simulated crash boundary")

    with pytest.raises(GovernedDiscoveryError):
        run_resumable_study_session(
            starting_understanding(),
            authorization=DiscoveryAuthorization(
                tenant_id="customer-a",
                metadata_targets=frozenset(
                    {"Customer", "Territory"}
                ),
            ),
            checkpoint_store=first_store,
            metadata_reader=failing_reader,
            record_reader=fail_reader,
            clock=fixed_clock,
        )

    calls = []
    reopened_store = SQLiteStudyCheckpointStore(path)

    def recovered_reader(target):
        calls.append(target)
        return (metadata_observation(target),)

    result = run_resumable_study_session(
        # Deliberately fresh/stale seed. The checkpoint is canonical.
        starting_understanding(),
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset(
                {"Customer", "Territory"}
            ),
        ),
        checkpoint_store=reopened_store,
        metadata_reader=recovered_reader,
        record_reader=fail_reader,
        clock=fixed_clock,
    )

    assert calls == ["Territory"]
    assert result.resumed_from_sequence == 1
    assert result.checkpoints_written == 1
    assert result.latest_checkpoint.sequence == 2


def test_previous_authorization_is_not_restored(tmp_path):
    path = tmp_path / "checkpoints.sqlite3"
    store = SQLiteStudyCheckpointStore(path)

    def metadata_reader(target):
        return (
            metadata_observation(
                target,
                linked_target="Territory",
            ),
        )

    first = run_resumable_study_session(
        starting_understanding(),
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            metadata_targets=frozenset({"Customer"}),
        ),
        checkpoint_store=store,
        metadata_reader=metadata_reader,
        record_reader=fail_reader,
        clock=fixed_clock,
    )

    assert first.latest_checkpoint.sequence == 1

    restarted = run_resumable_study_session(
        starting_understanding(),
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
        ),
        checkpoint_store=SQLiteStudyCheckpointStore(path),
        metadata_reader=fail_reader,
        record_reader=fail_reader,
        clock=fixed_clock,
    )

    assert restarted.study.cycles_completed == 0
    assert restarted.checkpoints_written == 0
    assert restarted.latest_checkpoint.sequence == 1


def test_sampled_record_is_not_repeated_after_restart(
    tmp_path,
):
    path = tmp_path / "checkpoints.sqlite3"
    calls = []

    def record_reader(target):
        calls.append(target)
        return (record_observation(target),)

    first = run_resumable_study_session(
        starting_understanding(),
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            record_targets=frozenset({"Company"}),
        ),
        checkpoint_store=SQLiteStudyCheckpointStore(path),
        metadata_reader=fail_reader,
        record_reader=record_reader,
        clock=fixed_clock,
    )

    assert calls == ["Company"]
    assert first.latest_checkpoint.sampled_records == frozenset(
        {"Company"}
    )

    restarted = run_resumable_study_session(
        starting_understanding(),
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
            record_targets=frozenset({"Company"}),
        ),
        checkpoint_store=SQLiteStudyCheckpointStore(path),
        metadata_reader=fail_reader,
        record_reader=fail_reader,
        clock=fixed_clock,
    )

    assert restarted.study.cycles_completed == 0
    assert restarted.checkpoints_written == 0
    assert restarted.latest_checkpoint.sequence == 1


def test_empty_first_session_persists_seed_checkpoint(
    tmp_path,
):
    store = SQLiteStudyCheckpointStore(
        tmp_path / "checkpoints.sqlite3"
    )

    result = run_resumable_study_session(
        starting_understanding(),
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
        ),
        checkpoint_store=store,
        metadata_reader=fail_reader,
        record_reader=fail_reader,
        clock=fixed_clock,
    )

    assert result.study.cycles_completed == 0
    assert result.checkpoints_written == 1
    assert result.latest_checkpoint.sequence == 1


def test_exhausted_restart_does_not_append_checkpoint(
    tmp_path,
):
    path = tmp_path / "checkpoints.sqlite3"

    first = run_resumable_study_session(
        starting_understanding(),
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
        ),
        checkpoint_store=SQLiteStudyCheckpointStore(path),
        metadata_reader=fail_reader,
        record_reader=fail_reader,
        clock=fixed_clock,
    )

    second = run_resumable_study_session(
        starting_understanding(),
        authorization=DiscoveryAuthorization(
            tenant_id="customer-a",
        ),
        checkpoint_store=SQLiteStudyCheckpointStore(path),
        metadata_reader=fail_reader,
        record_reader=fail_reader,
        clock=fixed_clock,
    )

    assert first.latest_checkpoint.sequence == 1
    assert second.resumed_from_sequence == 1
    assert second.checkpoints_written == 0
    assert second.latest_checkpoint.sequence == 1


def test_seed_tenant_mismatch_fails_before_store_read():
    class UntouchedStore:
        def load_latest(self, *, tenant_id):
            raise AssertionError("store must not be read")

        def append(self, checkpoint):
            raise AssertionError("store must not be written")

    with pytest.raises(
        ResumableStudySessionError,
        match="tenant boundary",
    ):
        run_resumable_study_session(
            starting_understanding(
                tenant_id="customer-b"
            ),
            authorization=DiscoveryAuthorization(
                tenant_id="customer-a",
            ),
            checkpoint_store=UntouchedStore(),
            metadata_reader=fail_reader,
            record_reader=fail_reader,
            clock=fixed_clock,
        )


def test_rejects_cross_tenant_checkpoint_from_store():
    wrong_checkpoint = StudyCheckpoint(
        tenant_id="customer-b",
        sequence=1,
        created_at=FIXED_TIME,
        understanding=starting_understanding(
            tenant_id="customer-b"
        ),
        sampled_records=frozenset(),
        metadata_targets_studied=(),
        record_targets_sampled=(),
    )

    class WrongTenantStore:
        def load_latest(self, *, tenant_id):
            return wrong_checkpoint

        def append(self, checkpoint):
            raise AssertionError("store must not be written")

    with pytest.raises(
        ResumableStudySessionError,
        match="cross-tenant",
    ):
        run_resumable_study_session(
            starting_understanding(),
            authorization=DiscoveryAuthorization(
                tenant_id="customer-a",
            ),
            checkpoint_store=WrongTenantStore(),
            metadata_reader=fail_reader,
            record_reader=fail_reader,
            clock=fixed_clock,
        )


def test_naive_checkpoint_clock_fails_closed(tmp_path):
    store = SQLiteStudyCheckpointStore(
        tmp_path / "checkpoints.sqlite3"
    )

    def naive_clock():
        return FIXED_TIME.replace(tzinfo=None)

    with pytest.raises(
        StudyCheckpointError,
        match="timezone-aware",
    ):
        run_resumable_study_session(
            starting_understanding(),
            authorization=DiscoveryAuthorization(
                tenant_id="customer-a",
            ),
            checkpoint_store=store,
            metadata_reader=fail_reader,
            record_reader=fail_reader,
            clock=naive_clock,
        )

    assert store.load_latest(
        tenant_id="customer-a"
    ) is None
