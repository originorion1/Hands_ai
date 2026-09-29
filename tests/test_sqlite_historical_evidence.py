import os
import sqlite3
from datetime import UTC, datetime

import pytest

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.history.evidence import (
    HistoricalEvidenceBatch,
    HistoricalEvidenceConflictError,
    HistoricalEvidenceIntegrityError,
    HistoricalEvidenceSequenceError,
)
from orion.stores import sqlite_historical_evidence
from orion.stores.sqlite_historical_evidence import (
    SQLiteHistoricalEvidenceStore,
    ensure_private_storage_directory,
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


def test_sqlite_creates_owner_only_database(tmp_path):
    path = tmp_path / "historical.sqlite3"

    SQLiteHistoricalEvidenceStore(path)

    assert path.stat().st_mode & 0o777 == 0o600


@pytest.mark.parametrize("read_only", [False, True])
def test_sqlite_rejects_unsafe_existing_database_mode(tmp_path, read_only):
    path = tmp_path / "historical.sqlite3"
    SQLiteHistoricalEvidenceStore(path)
    path.chmod(0o640)

    with pytest.raises(ValueError, match="owner-only"):
        SQLiteHistoricalEvidenceStore(path, read_only=read_only)


@pytest.mark.parametrize("operation", ["append", "load_all"])
def test_sqlite_rejects_database_made_unsafe_after_store_creation(tmp_path, operation):
    path = tmp_path / "historical.sqlite3"
    store = SQLiteHistoricalEvidenceStore(path)
    path.chmod(0o640)

    with pytest.raises(ValueError, match="owner-only"):
        if operation == "append":
            store.append(batch())
        else:
            store.load_all(tenant_id="customer-a", resource="Purchase Invoice")

    assert path.stat().st_mode & 0o777 == 0o640


@pytest.mark.parametrize("operation", ["append", "load_all"])
def test_sqlite_does_not_recreate_database_removed_after_store_creation(
    tmp_path, operation
):
    path = tmp_path / "historical.sqlite3"
    store = SQLiteHistoricalEvidenceStore(path)
    path.unlink()

    with pytest.raises(ValueError, match="database file is required"):
        if operation == "append":
            store.append(batch())
        else:
            store.load_all(tenant_id="customer-a", resource="Purchase Invoice")

    assert not path.exists()


def test_sqlite_rejects_unsafe_parent_before_file_creation(tmp_path):
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)
    path = parent / "historical.sqlite3"

    with pytest.raises(ValueError, match="parent directory must be owner-only"):
        SQLiteHistoricalEvidenceStore(path)

    assert not path.exists()


def test_private_directory_creation_rejects_existing_unsafe_mode_without_changing_it(
    tmp_path,
):
    parent = tmp_path / "shared"
    parent.mkdir(mode=0o755)
    parent.chmod(0o755)

    with pytest.raises(ValueError, match="parent directory must be owner-only"):
        ensure_private_storage_directory(parent, create=True)

    assert parent.stat().st_mode & 0o777 == 0o755


def test_private_directory_creation_rejects_symlink_without_changing_target(tmp_path):
    target = tmp_path / "shared"
    target.mkdir(mode=0o755)
    target.chmod(0o755)
    symlink = tmp_path / "state"
    symlink.symlink_to(target, target_is_directory=True)

    with pytest.raises(ValueError, match="parent directory must be owner-only"):
        ensure_private_storage_directory(symlink, create=True)

    assert target.stat().st_mode & 0o777 == 0o755


def test_sqlite_rejects_foreign_parent_owner(tmp_path, monkeypatch):
    path = tmp_path / "historical.sqlite3"
    SQLiteHistoricalEvidenceStore(path)
    real_euid = os.geteuid()
    monkeypatch.setattr(
        sqlite_historical_evidence.os,
        "geteuid",
        lambda: real_euid + 1,
    )

    with pytest.raises(ValueError, match="parent directory must be owner-only"):
        SQLiteHistoricalEvidenceStore(path)


def test_sqlite_rejects_foreign_database_owner(tmp_path, monkeypatch):
    path = tmp_path / "historical.sqlite3"
    SQLiteHistoricalEvidenceStore(path)
    real_euid = os.geteuid()
    monkeypatch.setattr(
        sqlite_historical_evidence,
        "ensure_private_storage_directory",
        lambda _path: None,
    )
    monkeypatch.setattr(
        sqlite_historical_evidence.os,
        "geteuid",
        lambda: real_euid + 1,
    )

    with pytest.raises(ValueError, match="regular owner-only"):
        SQLiteHistoricalEvidenceStore(path)


@pytest.mark.parametrize("read_only", [False, True])
def test_sqlite_rejects_symlink_and_hard_link_database(tmp_path, read_only):
    target = tmp_path / "target.sqlite3"
    SQLiteHistoricalEvidenceStore(target)
    symlink = tmp_path / "symlink.sqlite3"
    symlink.symlink_to(target)
    hardlink = tmp_path / "hardlink.sqlite3"
    os.link(target, hardlink)

    with pytest.raises(ValueError, match="regular owner-only"):
        SQLiteHistoricalEvidenceStore(symlink, read_only=read_only)
    with pytest.raises(ValueError, match="one link"):
        SQLiteHistoricalEvidenceStore(hardlink, read_only=read_only)


def test_sqlite_read_only_load_does_not_modify_database(tmp_path):
    path = tmp_path / "historical.sqlite3"
    original = batch()
    SQLiteHistoricalEvidenceStore(path).append(original)
    connection = sqlite3.connect(path)
    try:
        journal_mode = connection.execute("PRAGMA journal_mode").fetchone()[0]
        schema = connection.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall()
    finally:
        connection.close()
    database_bytes = path.read_bytes()

    store = SQLiteHistoricalEvidenceStore(path, read_only=True)

    assert store.load_all(
        tenant_id="customer-a", resource="Purchase Invoice"
    ) == (original,)
    assert path.read_bytes() == database_bytes
    connection = sqlite3.connect(path)
    try:
        assert connection.execute("PRAGMA journal_mode").fetchone()[0] == journal_mode
        assert connection.execute(
            "SELECT type, name, sql FROM sqlite_master ORDER BY type, name"
        ).fetchall() == schema
    finally:
        connection.close()


def test_sqlite_read_only_requires_existing_database(tmp_path):
    path = tmp_path / "missing.sqlite3"

    with pytest.raises(ValueError, match="database file is required"):
        SQLiteHistoricalEvidenceStore(path, read_only=True)

    assert not path.exists()


def test_sqlite_read_only_requires_expected_schema(tmp_path):
    path = tmp_path / "wrong-schema.sqlite3"
    connection = sqlite3.connect(path)
    connection.close()
    path.chmod(0o600)
    database_bytes = path.read_bytes()

    with pytest.raises(sqlite3.OperationalError, match="schema is missing"):
        SQLiteHistoricalEvidenceStore(path, read_only=True)

    assert path.read_bytes() == database_bytes


def test_sqlite_read_only_rejects_append(tmp_path):
    path = tmp_path / "historical.sqlite3"
    SQLiteHistoricalEvidenceStore(path)
    store = SQLiteHistoricalEvidenceStore(path, read_only=True)

    with pytest.raises(sqlite3.OperationalError, match="read-only"):
        store.append(batch())


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


def test_list_resources_is_tenant_scoped_deterministic_and_read_only(tmp_path):
    store = SQLiteHistoricalEvidenceStore(tmp_path / "historical.sqlite3")
    store.append(batch(resource="Resource Z"))
    store.append(batch(resource="Resource A"))
    store.append(batch(tenant_id="customer-b", resource="Resource B"))

    before = store.load_all(tenant_id="customer-a", resource="Resource A")

    assert store.list_resources(tenant_id="customer-a") == (
        "Resource A",
        "Resource Z",
    )
    assert store.list_resources(tenant_id="customer-b") == ("Resource B",)
    assert store.load_all(tenant_id="customer-a", resource="Resource A") == before


@pytest.mark.parametrize("tenant_id", ["", " tenant-a", "tenant-a "])
def test_list_resources_rejects_invalid_tenant_scope(tmp_path, tenant_id):
    store = SQLiteHistoricalEvidenceStore(tmp_path / "historical.sqlite3")

    with pytest.raises(ValueError):
        store.list_resources(tenant_id=tenant_id)


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
