"""Transactional SQLite persistence for ORION study checkpoints.

This adapter stores immutable append-only checkpoint history. Authorization
and credentials are deliberately absent from the stored checkpoint contract.
"""

from __future__ import annotations

import hmac
import json
import re
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
from ..discovery.json_boundary import unique_json_object
from ..understanding.role_checkpoint import _json, checkpoint_sha256
from ..understanding.semantic_checkpoint import (
    checkpoint_semantic,
)
from ..understanding.semantic_checkpoint import (
    restore_semantic as restore_semantic_study,
)
from ..understanding.semantic_study import SEMANTIC_EVALUATOR_VERSION, SemanticStudy

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

_SEMANTIC_SCHEMA = """
CREATE TABLE IF NOT EXISTS orion_semantic_checkpoints (
    tenant_id TEXT NOT NULL,
    company TEXT NOT NULL,
    source_id TEXT NOT NULL,
    study_id TEXT NOT NULL,
    sequence INTEGER NOT NULL CHECK (sequence BETWEEN 1 AND 100),
    payload_json TEXT NOT NULL,
    checksum_sha256 TEXT NOT NULL,
    envelope_json TEXT NOT NULL,
    envelope_sha256 TEXT NOT NULL,
    PRIMARY KEY (tenant_id, company, source_id, study_id, sequence)
)
"""
_SEMANTIC_LIMIT = 100
_SEMANTIC_KEYS = {'version', 'evaluator_version', 'base', 'policy', 'batches', 'revisions'}


def _semantic_scope(tenant_id, company, source_id, study_id):
    values = (tenant_id, company, source_id, study_id)
    if any(type(value) is not str or not value.strip() or len(value) > 200
           for value in values):
        raise ValueError('bounded tenant/company/source/study identity required')
    return values


def _semantic_document(payload, scope):
    """Inspect checkpoint/index bindings, leaving evidence semantics to recovery."""
    try:
        checkpoint_sha256(payload)
        value = json.loads(payload, object_pairs_hook=unique_json_object)
        if (type(value) is not dict or set(value) != _SEMANTIC_KEYS
                or type(value['version']) is not int or value['version'] != 2
                or value['evaluator_version'] != SEMANTIC_EVALUATOR_VERSION
                or type(value['policy']) is not dict
                or set(value['policy']) != {'instruments', 'rules'}
                or type(value['batches']) is not list
                or type(value['revisions']) is not list
                or len(value['batches']) != len(value['revisions'])
                or len(value['revisions']) > _SEMANTIC_LIMIT
                or any(type(identity) is not str or re.fullmatch('[0-9a-f]{64}', identity) is None
                       for identity in value['revisions'])
                or len(set(value['revisions'])) != len(value['revisions'])
                or _json(value) != payload):
            raise ValueError('semantic checkpoint contract mismatch')
        base = json.loads(value['base'], object_pairs_hook=unique_json_object)
        if (type(base) is not dict
                or set(base) != {'version', 'tenant_id', 'company', 'source_id', 'schema', 'records'}
                or (base['tenant_id'], base['company'], base['source_id']) != scope[:3]):
            raise ValueError('semantic checkpoint scope mismatch')
        return value, base
    except (ValueError, TypeError, KeyError) as exc:
        raise StudyCheckpointIntegrityError('semantic checkpoint/index contract mismatch') from exc


def _semantic_envelope(document, base, scope, sequence, checksum, predecessor):
    revisions = document['revisions']
    return _json({
        'version': 1, 'tenant_id': scope[0], 'company': scope[1],
        'source_id': scope[2], 'study_id': scope[3], 'sequence': sequence,
        'checkpoint_sha256': checksum, 'predecessor_sha256': predecessor,
        'evaluator_version': document['evaluator_version'],
        'policy_sha256': checkpoint_sha256(_json(document['policy'])),
        'dependencies_sha256': checkpoint_sha256(_json({
            'schema': base['schema'], 'records': base['records'],
            'batches': document['batches']})),
        'revision_id': revisions[-1] if revisions else None,
        'previous_revision': revisions[-2] if len(revisions) > 1 else None,
    })


def _semantic_extension(previous, proposed):
    """Immutable policy/base and an exact prefix of accepted revision batches."""
    length = len(previous['revisions'])
    return (previous['base'] == proposed['base']
            and previous['policy'] == proposed['policy']
            and previous['evaluator_version'] == proposed['evaluator_version']
            and length < len(proposed['revisions'])
            and previous['revisions'] == proposed['revisions'][:length]
            and previous['batches'] == proposed['batches'][:length])


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
                connection.execute(_SEMANTIC_SCHEMA)
                connection.commit()
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        if self._read_only:
            return sqlite3.connect(
                f"{self._path.resolve().as_uri()}?mode=ro",
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

    def _semantic_rows(self, connection, scope):
        rows = connection.execute(
            """
            SELECT sequence, payload_json, checksum_sha256,
                   envelope_json, envelope_sha256
            FROM orion_semantic_checkpoints
            WHERE tenant_id = ? AND company = ? AND source_id = ? AND study_id = ?
            ORDER BY sequence LIMIT ?
            """, (*scope, _SEMANTIC_LIMIT + 1),
        ).fetchall()
        if len(rows) > _SEMANTIC_LIMIT:
            raise StudyCheckpointIntegrityError('semantic checkpoint history budget exceeded')
        predecessor = None
        previous = None
        for expected_sequence, row in enumerate(rows, 1):
            sequence, payload, checksum, envelope, envelope_checksum = row
            if (type(sequence) is not int or sequence != expected_sequence
                    or any(type(value) is not str for value in row[1:])):
                raise StudyCheckpointIntegrityError('semantic checkpoint history/envelope malformed')
            document, base = _semantic_document(payload, scope)
            expected = _semantic_envelope(document, base, scope, sequence,
                                          checkpoint_sha256(payload), predecessor)
            if (not hmac.compare_digest(checksum, checkpoint_sha256(payload))
                    or envelope != expected
                    or not hmac.compare_digest(envelope_checksum, checkpoint_sha256(envelope))
                    or (previous is not None and not _semantic_extension(previous, document))):
                raise StudyCheckpointIntegrityError('semantic checkpoint chain verification failed')
            previous = document
            predecessor = envelope_checksum
        return rows

    def append_semantic(self, study: SemanticStudy, *, study_id: str, sequence: int) -> None:
        """Persist one canonical semantic snapshot in the existing checkpoint DB.

        The checkpoint remains reference-only. Index hashes detect corruption
        within a trusted store; they do not authenticate operator-modified files
        or prove that a privileged actor has not rolled back the complete DB.
        """
        if self._read_only:
            raise sqlite3.OperationalError('checkpoint store is read-only')
        if type(study) is not SemanticStudy:
            raise TypeError('canonical semantic study required')
        scope = _semantic_scope(study.base.tenant, study.base.company, study.base.source, study_id)
        if type(sequence) is not int or not 1 <= sequence <= _SEMANTIC_LIMIT:
            raise StudyCheckpointSequenceError('semantic checkpoint sequence budget exceeded')
        payload = checkpoint_semantic(study)
        document, base = _semantic_document(payload, scope)
        checksum = checkpoint_sha256(payload)
        connection = self._connect()
        try:
            connection.execute('BEGIN IMMEDIATE')
            rows = self._semantic_rows(connection, scope)
            if sequence <= len(rows):
                if rows[sequence - 1][1] != payload:
                    raise StudyCheckpointConflictError('semantic checkpoint identity already has different content')
                connection.commit()
                return
            if sequence != len(rows) + 1:
                raise StudyCheckpointSequenceError('semantic checkpoint sequence must be consecutive')
            if rows:
                previous, _ = _semantic_document(rows[-1][1], scope)
                if not _semantic_extension(previous, document):
                    raise StudyCheckpointConflictError('semantic checkpoint must extend accepted revision history')
            predecessor = rows[-1][4] if rows else None
            envelope = _semantic_envelope(document, base, scope, sequence, checksum, predecessor)
            connection.execute(
                """
                INSERT INTO orion_semantic_checkpoints (
                    tenant_id, company, source_id, study_id, sequence, payload_json,
                    checksum_sha256, envelope_json, envelope_sha256
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (*scope, sequence, payload, checksum, envelope, checkpoint_sha256(envelope)),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def restore_semantic(self, *, tenant_id: str, company: str, source_id: str,
                         study_id: str, instruments, rules, evidence_lookup) -> SemanticStudy | None:
        """Revalidate evidence and recompute canonical beliefs, never authority.

        Missing, changed or retention-expired originals fail closed with the
        existing checkpoint integrity error; historical references stay stored.
        A pre-semantic checkpoint database yields no semantic knowledge.
        """
        scope = _semantic_scope(tenant_id, company, source_id, study_id)
        connection = self._connect()
        try:
            connection.execute('BEGIN')
            if connection.execute(
                "SELECT 1 FROM sqlite_master WHERE type = 'table' AND name = 'orion_semantic_checkpoints'"
            ).fetchone() is None:
                return None
            rows = self._semantic_rows(connection, scope)
        finally:
            connection.close()
        if not rows:
            return None
        _, payload, checksum, _, _ = rows[-1]
        try:
            return restore_semantic_study(payload, expected_sha256=checksum,
                                          tenant_id=tenant_id, company=company, source_id=source_id,
                                          instruments=instruments, rules=rules,
                                          evidence_lookup=evidence_lookup)
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            raise StudyCheckpointIntegrityError('semantic recovery unavailable: evidence or policy invalid') from exc

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
