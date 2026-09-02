import sqlite3
from pathlib import Path

import pytest

from orion.stores.sqlite_checkpoint import SQLiteStudyCheckpointStore
from orion.stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore


@pytest.mark.parametrize(
    ("store_type", "table_name"),
    (
        (SQLiteStudyCheckpointStore, "orion_study_checkpoints"),
        (SQLiteHistoricalEvidenceStore, "orion_historical_evidence"),
    ),
)
def test_read_only_connection_rejects_sql_and_preserves_sidecars(
    tmp_path, store_type, table_name
):
    path = tmp_path / "state.sqlite3"
    store_type(path)
    tracked_paths = (path, Path(f"{path}-wal"), Path(f"{path}-shm"))
    before = _snapshot(tracked_paths)

    store = store_type(path, read_only=True)
    if store_type is SQLiteStudyCheckpointStore:
        assert store.load_latest(tenant_id="synthetic-tenant") is None
    else:
        assert store.load_all(
            tenant_id="synthetic-tenant",
            resource="Synthetic Resource",
        ) == ()

    connection = store._connect()
    try:
        with pytest.raises(sqlite3.OperationalError, match="readonly"):
            connection.execute(f"DELETE FROM {table_name}")
    finally:
        connection.close()

    assert _snapshot(tracked_paths) == before


def _snapshot(paths: tuple[Path, ...]) -> tuple[tuple[bool, bytes | None], ...]:
    return tuple(
        (path.exists(), path.read_bytes() if path.exists() else None)
        for path in paths
    )
