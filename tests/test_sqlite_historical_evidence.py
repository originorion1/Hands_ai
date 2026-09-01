import sqlite3
from datetime import UTC, datetime

import pytest

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.history.evidence import HistoricalEvidenceBatch
from orion.stores.sqlite_historical_evidence import (
    HistoricalEvidenceConflictError,
    HistoricalEvidenceIntegrityError,
    HistoricalEvidenceSequenceError,
    SQLiteHistoricalEvidenceStore,
)


def batch(*, tenant_id="customer-a", resource="Purchase Invoice", sequence=1, name="PINV-001"):
    return HistoricalEvidenceBatch(
        tenant_id=tenant_id,
        resource=resource,
        sequence=sequence,
        created_at=datetime(2026, 9, 2, 12, sequence, tzinfo=UTC),
        observations=(
            Observation(
                evidence=Evidence(
                    kind=EvidenceKind.API,
                    source="historical-test",
                    tenant_id=tenant_id,
                    observed_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
                    payload={"resource": resource, "record": {"name": name}},
                )
            ),
        ),
    )


def test_sqlite_round_trip_and_reopen(tmp_path):
    path = tmp_path / "historical.sqlite3"
    original = batch()
    SQLiteHistoricalEvidenceStore(path).append(original)

    assert SQLiteHistoricalEvidenceStore(path).load_all(
        tenant_id="customer-a", resource="Purchase Invoice"
    ) == (original,)


def test_sqlite_tenant_and_resource_isolation(tmp_path):
    store = SQLiteHistoricalEvidenceStore(tmp_path / "historical.sqlite3")
    tenant_a = batch()
    tenant_b = batch(tenant_id="customer-b")
    other_resource = batch(resource="Sales Invoice")
    store.append(tenant_a)
    store.append(tenant_b)
    store.append(other_resource)

    assert store.load_all(tenant_id="customer-a", resource="Purchase Invoice") == (tenant_a,)
    assert store.load_all(tenant_id="customer-b", resource="Purchase Invoice") == (tenant_b,)
    assert store.load_all(tenant_id="customer-a", resource="Sales Invoice") == (other_resource,)


def test_sqlite_exact_replay_is_idempotent_and_conflict_is_rejected(tmp_path):
    store = SQLiteHistoricalEvidenceStore(tmp_path / "historical.sqlite3")
    original = batch()
    store.append(original)
    store.append(original)
    assert store.load_all(tenant_id="customer-a", resource="Purchase Invoice") == (original,)

    with pytest.raises(HistoricalEvidenceConflictError, match="different state"):
        store.append(batch(name="PINV-CHANGED"))


def test_sqlite_rejects_sequence_gap(tmp_path):
    store = SQLiteHistoricalEvidenceStore(tmp_path / "historical.sqlite3")
    with pytest.raises(HistoricalEvidenceSequenceError, match="strictly consecutive"):
        store.append(batch(sequence=2))


def test_sqlite_detects_checksum_and_envelope_tampering(tmp_path):
    path = tmp_path / "historical.sqlite3"
    store = SQLiteHistoricalEvidenceStore(path)
    store.append(batch())
    connection = sqlite3.connect(path)
    try:
        connection.execute("UPDATE orion_historical_evidence SET payload_json = payload_json || ' '")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(HistoricalEvidenceIntegrityError, match="checksum"):
        store.load_all(tenant_id="customer-a", resource="Purchase Invoice")

    path = tmp_path / "envelope.sqlite3"
    store = SQLiteHistoricalEvidenceStore(path)
    store.append(batch())
    connection = sqlite3.connect(path)
    try:
        connection.execute("UPDATE orion_historical_evidence SET created_at = '2030-01-01T00:00:00+00:00'")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(HistoricalEvidenceIntegrityError, match="envelope"):
        store.load_all(tenant_id="customer-a", resource="Purchase Invoice")


def test_sqlite_detects_deleted_middle_sequence(tmp_path):
    path = tmp_path / "historical.sqlite3"
    store = SQLiteHistoricalEvidenceStore(path)
    store.append(batch(sequence=1, name="PINV-001"))
    store.append(batch(sequence=2, name="PINV-002"))
    store.append(batch(sequence=3, name="PINV-003"))
    connection = sqlite3.connect(path)
    try:
        connection.execute("DELETE FROM orion_historical_evidence WHERE sequence = 2")
        connection.commit()
    finally:
        connection.close()
    with pytest.raises(HistoricalEvidenceIntegrityError, match="sequence gap"):
        store.load_all(tenant_id="customer-a", resource="Purchase Invoice")
