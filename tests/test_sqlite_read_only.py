import sqlite3

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
def test_read_only_connection_sees_wal_and_rejects_sql(
    tmp_path, store_type, table_name
):
    path = tmp_path / "state.sqlite3"
    store_type(path)
    writer = sqlite3.connect(path)
    try:
        assert writer.execute("PRAGMA journal_mode=WAL").fetchone()[0] == "wal"
        writer.execute("CREATE TABLE wal_probe (value TEXT NOT NULL)")
        writer.execute("INSERT INTO wal_probe VALUES ('committed-through-wal')")
        writer.commit()
        assert path.with_name(f"{path.name}-wal").stat().st_size > 0

        store = store_type(path, read_only=True)
        connection = store._connect()
        try:
            assert connection.execute("SELECT value FROM wal_probe").fetchone() == (
                "committed-through-wal",
            )
            with pytest.raises(sqlite3.OperationalError, match="readonly"):
                connection.execute(f"DELETE FROM {table_name}")
        finally:
            connection.close()
    finally:
        writer.close()
