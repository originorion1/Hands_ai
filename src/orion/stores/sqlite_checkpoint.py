"""Transactional SQLite persistence for ORION study checkpoints.

This adapter stores immutable append-only checkpoint history. Authorization
and credentials are deliberately absent from the stored checkpoint contract.
"""

from __future__ import annotations

import hmac
import sqlite3
from pathlib import Path

from ..discovery.checkpoint import (
    StudyCheckpoint,
    StudyCheckpointConflictError,
    StudyCheckpointIntegrityError,
    StudyCheckpointSequenceError,
    checkpoint_checksum,
    checkpoint_from_json,
    checkpoint_to_json,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orion_study_checkpoints (
    tenant_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    PRIMARY KEY (tenant_id, sequence)
)
"""


class SQLiteStudyCheckpointStore:
    """Append-only transactional checkpoint store."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        read_only: bool = False,
    ) -> None:
        self._path = Path(database_path)
        self._read_only = read_only

        if not str(self._path):
            raise ValueError(
                "checkpoint database path must be non-empty"
            )

        if not self._path.parent.exists():
            raise ValueError(
                "checkpoint database parent directory does not exist"
            )

        if self._path.exists() and self._path.is_dir():
            raise ValueError(
                "checkpoint database path must not be a directory"
            )

        if self._read_only and not self._path.is_file():
            raise ValueError("checkpoint database file is required")

        connection = self._connect()

        try:
            if self._read_only:
                if connection.execute(
                    """
                    SELECT 1 FROM sqlite_master
                    WHERE type = 'table' AND name = 'orion_study_checkpoints'
                    """
                ).fetchone() is None:
                    raise sqlite3.OperationalError(
                        "checkpoint database schema is missing"
                    )
            else:
                connection.execute("PRAGMA journal_mode=WAL")
                connection.execute("PRAGMA synchronous=FULL")
                connection.execute(_SCHEMA)
                connection.commit()
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        if self._read_only:
            return sqlite3.connect(
                f"{self._path.resolve().as_uri()}?mode=ro&immutable=1",
                timeout=5.0,
                uri=True,
            )
        connection = sqlite3.connect(
            self._path,
            timeout=5.0,
        )
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def append(self, checkpoint: StudyCheckpoint) -> None:
        """Append exactly one monotonic immutable checkpoint."""

        if self._read_only:
            raise sqlite3.OperationalError("checkpoint store is read-only")

        payload_json = checkpoint_to_json(checkpoint)
        checksum = checkpoint_checksum(payload_json)

        connection = self._connect()

        try:
            connection.execute("BEGIN IMMEDIATE")

            existing = connection.execute(
                """
                SELECT payload_json, checksum_sha256
                FROM orion_study_checkpoints
                WHERE tenant_id = ? AND sequence = ?
                """,
                (
                    checkpoint.tenant_id,
                    checkpoint.sequence,
                ),
            ).fetchone()

            if existing is not None:
                existing_payload, existing_checksum = existing

                if (
                    existing_payload == payload_json
                    and hmac.compare_digest(
                        existing_checksum,
                        checksum,
                    )
                ):
                    connection.commit()
                    return

                raise StudyCheckpointConflictError(
                    "checkpoint sequence already exists with different state"
                )

            latest = connection.execute(
                """
                SELECT MAX(sequence)
                FROM orion_study_checkpoints
                WHERE tenant_id = ?
                """,
                (checkpoint.tenant_id,),
            ).fetchone()

            latest_sequence = latest[0]
            expected_sequence = (
                1
                if latest_sequence is None
                else latest_sequence + 1
            )

            if checkpoint.sequence != expected_sequence:
                raise StudyCheckpointSequenceError(
                    "checkpoint sequence must be strictly consecutive"
                )

            connection.execute(
                """
                INSERT INTO orion_study_checkpoints (
                    tenant_id,
                    sequence,
                    created_at,
                    payload_json,
                    checksum_sha256
                )
                VALUES (?, ?, ?, ?, ?)
                """,
                (
                    checkpoint.tenant_id,
                    checkpoint.sequence,
                    checkpoint.created_at.isoformat(),
                    payload_json,
                    checksum,
                ),
            )

            connection.commit()

        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def load_latest(
        self,
        *,
        tenant_id: str,
    ) -> StudyCheckpoint | None:
        """Load and integrity-check only the requested tenant's latest state."""

        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id must be non-empty")

        connection = self._connect()

        try:
            row = connection.execute(
                """
                SELECT
                    sequence,
                    created_at,
                    payload_json,
                    checksum_sha256
                FROM orion_study_checkpoints
                WHERE tenant_id = ?
                ORDER BY sequence DESC
                LIMIT 1
                """,
                (tenant_id,),
            ).fetchone()
        finally:
            connection.close()

        if row is None:
            return None

        sequence, created_at, payload_json, stored_checksum = row

        actual_checksum = checkpoint_checksum(payload_json)

        if not hmac.compare_digest(
            stored_checksum,
            actual_checksum,
        ):
            raise StudyCheckpointIntegrityError(
                "checkpoint checksum verification failed"
            )

        checkpoint = checkpoint_from_json(payload_json)

        if (
            checkpoint.tenant_id != tenant_id
            or checkpoint.sequence != sequence
            or checkpoint.created_at.isoformat() != created_at
        ):
            raise StudyCheckpointIntegrityError(
                "checkpoint database envelope does not match payload"
            )

        return checkpoint
