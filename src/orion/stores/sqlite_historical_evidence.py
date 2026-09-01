"""Append-only SQLite persistence for immutable historical evidence batches."""

from __future__ import annotations

import hmac
import sqlite3
from pathlib import Path

from ..history.evidence import (
    HistoricalEvidenceBatch,
    HistoricalEvidenceError,
    _validate_resource_value,
    _validate_tenant,
    historical_evidence_checksum,
    historical_evidence_from_json,
    historical_evidence_to_json,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orion_historical_evidence (
    tenant_id TEXT NOT NULL,
    resource TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence >= 1),
    created_at TEXT NOT NULL,
    payload_json TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    PRIMARY KEY (tenant_id, resource, sequence)
)
"""


class HistoricalEvidenceConflictError(HistoricalEvidenceError):
    """Raised when a sequence already holds different immutable evidence."""


class HistoricalEvidenceSequenceError(HistoricalEvidenceError):
    """Raised when batch history is non-consecutive."""


class HistoricalEvidenceIntegrityError(HistoricalEvidenceError):
    """Raised when stored evidence cannot be authenticated and decoded."""


class SQLiteHistoricalEvidenceStore:
    """Transactional append-only store with tenant and resource isolation."""

    def __init__(self, database_path: str | Path) -> None:
        self._path = Path(database_path)
        if not str(self._path):
            raise ValueError("historical evidence database path must be non-empty")
        if not self._path.parent.exists():
            raise ValueError("historical evidence database parent directory does not exist")
        if self._path.exists() and self._path.is_dir():
            raise ValueError("historical evidence database path must not be a directory")

        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute(_SCHEMA)
            connection.commit()
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def append(self, batch: HistoricalEvidenceBatch) -> None:
        """Append a single next sequence, accepting only exact replays."""

        if not isinstance(batch, HistoricalEvidenceBatch):
            raise HistoricalEvidenceError("batch must be HistoricalEvidenceBatch")
        payload_json = historical_evidence_to_json(batch)
        checksum = historical_evidence_checksum(payload_json)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT payload_json, checksum_sha256
                FROM orion_historical_evidence
                WHERE tenant_id = ? AND resource = ? AND sequence = ?
                """,
                (batch.tenant_id, batch.resource, batch.sequence),
            ).fetchone()
            if existing is not None:
                if existing[0] == payload_json and hmac.compare_digest(existing[1], checksum):
                    connection.commit()
                    return
                raise HistoricalEvidenceConflictError(
                    "historical evidence sequence already exists with different state"
                )

            latest = connection.execute(
                """
                SELECT MAX(sequence)
                FROM orion_historical_evidence
                WHERE tenant_id = ? AND resource = ?
                """,
                (batch.tenant_id, batch.resource),
            ).fetchone()[0]
            expected = 1 if latest is None else latest + 1
            if batch.sequence != expected:
                raise HistoricalEvidenceSequenceError(
                    "historical evidence sequence must be strictly consecutive"
                )
            connection.execute(
                """
                INSERT INTO orion_historical_evidence (
                    tenant_id, resource, sequence, created_at, payload_json, checksum_sha256
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    batch.tenant_id,
                    batch.resource,
                    batch.sequence,
                    batch.created_at.isoformat(),
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

    def load_all(self, *, tenant_id: str, resource: str) -> tuple[HistoricalEvidenceBatch, ...]:
        """Load, authenticate, and validate one tenant/resource history."""

        _validate_query_scope(tenant_id, resource)
        connection = self._connect()
        try:
            rows = connection.execute(
                """
                SELECT sequence, created_at, payload_json, checksum_sha256
                FROM orion_historical_evidence
                WHERE tenant_id = ? AND resource = ?
                ORDER BY sequence ASC
                """,
                (tenant_id, resource),
            ).fetchall()
        finally:
            connection.close()

        batches: list[HistoricalEvidenceBatch] = []
        for expected_sequence, row in enumerate(rows, start=1):
            sequence, created_at, payload_json, stored_checksum = row
            if sequence != expected_sequence:
                raise HistoricalEvidenceIntegrityError(
                    "historical evidence sequence gap detected"
                )
            actual_checksum = historical_evidence_checksum(payload_json)
            if not hmac.compare_digest(stored_checksum, actual_checksum):
                raise HistoricalEvidenceIntegrityError(
                    "historical evidence checksum verification failed"
                )
            try:
                batch = historical_evidence_from_json(payload_json)
            except HistoricalEvidenceError as exc:
                raise HistoricalEvidenceIntegrityError(
                    "historical evidence payload validation failed"
                ) from exc
            if (
                batch.tenant_id != tenant_id
                or batch.resource != resource
                or batch.sequence != sequence
                or batch.created_at.isoformat() != created_at
            ):
                raise HistoricalEvidenceIntegrityError(
                    "historical evidence database envelope does not match payload"
                )
            batches.append(batch)
        return tuple(batches)


def _validate_query_scope(tenant_id: str, resource: str) -> None:
    _validate_tenant(tenant_id)
    _validate_resource_value(resource)
