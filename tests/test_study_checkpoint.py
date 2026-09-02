from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from orion.discovery.checkpoint import (
    StudyCheckpoint,
    StudyCheckpointConflictError,
    StudyCheckpointError,
    StudyCheckpointIntegrityError,
    StudyCheckpointSequenceError,
    checkpoint_from_json,
    checkpoint_to_json,
)
from orion.stores.sqlite_checkpoint import SQLiteStudyCheckpointStore
from orion.understanding.metadata import (
    MetadataUnderstanding,
    StructuralEntity,
    StructuralField,
)


def entity(name, *, evidence_id=None):
    return StructuralEntity(
        doctype=name,
        module=None,
        is_submittable=False,
        is_child_table=False,
        is_single=False,
        fields=(
            StructuralField(
                doctype=name,
                fieldname="name_field",
                fieldtype="Data",
                label=None,
                options=None,
                required=False,
                read_only=False,
                hidden=False,
                unique=False,
            ),
        ),
        provenance_ids=(evidence_id or uuid4(),),
    )


def checkpoint(
    *,
    tenant_id="customer-a",
    sequence=1,
    entities=None,
    sampled_records=frozenset(),
    metadata_targets=(),
    record_targets=(),
    created_at=None,
):
    if entities is None:
        entities=(entity("Company"),)

    return StudyCheckpoint(
        tenant_id=tenant_id,
        sequence=sequence,
        created_at=(
            created_at
            or datetime(2026, 9, 1, 20, 0, tzinfo=UTC)
        ),
        understanding=MetadataUnderstanding(
            tenant_id=tenant_id,
            entities=tuple(entities),
        ),
        sampled_records=frozenset(sampled_records),
        metadata_targets_studied=tuple(metadata_targets),
        record_targets_sampled=tuple(record_targets),
    )


def test_checkpoint_json_round_trip():
    original = checkpoint(
        entities=(entity("Company"), entity("Customer")),
        sampled_records=frozenset({"Company"}),
        metadata_targets=("Customer",),
        record_targets=("Company",),
    )

    restored = checkpoint_from_json(
        checkpoint_to_json(original)
    )

    assert restored == original


def test_checkpoint_round_trip_preserves_relationship_and_other_options():
    fields = (
        StructuralField(
            "Company",
            "status",
            "Select",
            None,
            "Open\nClosed",
            False,
            False,
            False,
            False,
        ),
        StructuralField(
            "Company",
            "contacts",
            "Table MultiSelect",
            None,
            "Contact",
            False,
            False,
            False,
            False,
        ),
    )
    company = StructuralEntity(
        "Company",
        None,
        False,
        False,
        False,
        fields,
        (uuid4(),),
    )
    original = checkpoint(entities=(company,))

    restored = checkpoint_from_json(checkpoint_to_json(original))

    assert restored == original
    assert restored.understanding.entities[0].fields == fields


def test_checkpoint_json_is_deterministic():
    current = checkpoint()

    assert checkpoint_to_json(current) == checkpoint_to_json(current)


def test_checkpoint_does_not_persist_authorization():
    payload = json.loads(
        checkpoint_to_json(checkpoint())
    )

    assert "authorization" not in payload
    assert "credentials" not in payload
    assert "execution_allowed" not in payload


def test_checkpoint_rejects_cross_tenant_understanding():
    with pytest.raises(
        StudyCheckpointError,
        match="tenant boundary",
    ):
        StudyCheckpoint(
            tenant_id="customer-a",
            sequence=1,
            created_at=datetime.now(UTC),
            understanding=MetadataUnderstanding(
                tenant_id="customer-b",
                entities=(),
            ),
            sampled_records=frozenset(),
            metadata_targets_studied=(),
            record_targets_sampled=(),
        )


def test_checkpoint_rejects_naive_timestamp():
    with pytest.raises(
        StudyCheckpointError,
        match="timezone-aware",
    ):
        checkpoint(
            created_at=datetime(
                2026,
                9,
                1,
                20,
                0,
                tzinfo=UTC,
            ).replace(tzinfo=None),
        )


def test_checkpoint_rejects_unknown_sampled_resource():
    with pytest.raises(
        StudyCheckpointError,
        match="must already be understood",
    ):
        checkpoint(
            sampled_records=frozenset({"Customer"}),
        )


def test_sqlite_store_round_trip(tmp_path):
    store = SQLiteStudyCheckpointStore(
        tmp_path / "orion-checkpoints.sqlite3"
    )

    original = checkpoint()

    store.append(original)

    assert store.load_latest(
        tenant_id="customer-a"
    ) == original


def test_sqlite_store_survives_reopen(tmp_path):
    path = tmp_path / "orion-checkpoints.sqlite3"
    original = checkpoint()

    first_store = SQLiteStudyCheckpointStore(path)
    first_store.append(original)

    second_store = SQLiteStudyCheckpointStore(path)

    assert second_store.load_latest(
        tenant_id="customer-a"
    ) == original


def test_sqlite_store_is_tenant_isolated(tmp_path):
    store = SQLiteStudyCheckpointStore(
        tmp_path / "orion-checkpoints.sqlite3"
    )

    tenant_a = checkpoint(tenant_id="customer-a")
    tenant_b = checkpoint(tenant_id="customer-b")

    store.append(tenant_a)
    store.append(tenant_b)

    assert store.load_latest(
        tenant_id="customer-a"
    ) == tenant_a

    assert store.load_latest(
        tenant_id="customer-b"
    ) == tenant_b


def test_sqlite_store_returns_latest_sequence(tmp_path):
    store = SQLiteStudyCheckpointStore(
        tmp_path / "orion-checkpoints.sqlite3"
    )

    first = checkpoint(sequence=1)
    second = checkpoint(
        sequence=2,
        created_at=datetime(
            2026,
            9,
            1,
            21,
            0,
            tzinfo=UTC,
        ),
    )

    store.append(first)
    store.append(second)

    assert store.load_latest(
        tenant_id="customer-a"
    ) == second


def test_sqlite_store_idempotently_accepts_same_checkpoint(tmp_path):
    store = SQLiteStudyCheckpointStore(
        tmp_path / "orion-checkpoints.sqlite3"
    )

    current = checkpoint()

    store.append(current)
    store.append(current)

    assert store.load_latest(
        tenant_id="customer-a"
    ) == current


def test_sqlite_store_rejects_conflicting_same_sequence(tmp_path):
    store = SQLiteStudyCheckpointStore(
        tmp_path / "orion-checkpoints.sqlite3"
    )

    store.append(checkpoint())

    conflicting = checkpoint(
        created_at=datetime(
            2026,
            9,
            1,
            21,
            0,
            tzinfo=UTC,
        ),
    )

    with pytest.raises(
        StudyCheckpointConflictError,
        match="different state",
    ):
        store.append(conflicting)


def test_sqlite_store_rejects_sequence_gap(tmp_path):
    store = SQLiteStudyCheckpointStore(
        tmp_path / "orion-checkpoints.sqlite3"
    )

    store.append(checkpoint(sequence=1))

    with pytest.raises(
        StudyCheckpointSequenceError,
        match="strictly consecutive",
    ):
        store.append(checkpoint(sequence=3))


def test_sqlite_store_rejects_first_sequence_other_than_one(tmp_path):
    store = SQLiteStudyCheckpointStore(
        tmp_path / "orion-checkpoints.sqlite3"
    )

    with pytest.raises(
        StudyCheckpointSequenceError,
        match="strictly consecutive",
    ):
        store.append(checkpoint(sequence=2))


def test_sqlite_store_detects_payload_corruption(tmp_path):
    path = tmp_path / "orion-checkpoints.sqlite3"

    store = SQLiteStudyCheckpointStore(path)
    store.append(checkpoint())

    connection = sqlite3.connect(path)

    try:
        connection.execute(
            """
            UPDATE orion_study_checkpoints
            SET payload_json = payload_json || ' '
            WHERE tenant_id = ? AND sequence = ?
            """,
            ("customer-a", 1),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        StudyCheckpointIntegrityError,
        match="checksum",
    ):
        store.load_latest(
            tenant_id="customer-a"
        )


def test_sqlite_store_detects_envelope_tampering(tmp_path):
    path = tmp_path / "orion-checkpoints.sqlite3"

    store = SQLiteStudyCheckpointStore(path)
    store.append(checkpoint())

    connection = sqlite3.connect(path)

    try:
        connection.execute(
            """
            UPDATE orion_study_checkpoints
            SET created_at = ?
            WHERE tenant_id = ? AND sequence = ?
            """,
            (
                "2030-01-01T00:00:00+00:00",
                "customer-a",
                1,
            ),
        )
        connection.commit()
    finally:
        connection.close()

    with pytest.raises(
        StudyCheckpointIntegrityError,
        match="envelope",
    ):
        store.load_latest(
            tenant_id="customer-a"
        )
