"""Append-only SQLite store for profile-aware evidence batches."""

from __future__ import annotations

import hmac
import sqlite3
from pathlib import Path

from ..history.evidence import (
    HistoricalEvidenceConflictError,
    HistoricalEvidenceError,
    HistoricalEvidenceIntegrityError,
)
from ..history.profiled_evidence import (
    ProfiledEvidenceBatch,
    profiled_evidence_checksum,
    profiled_evidence_from_json,
    profiled_evidence_to_json,
)


class SQLiteProfiledEvidenceStore:
    def __init__(self, database_path: str | Path) -> None:
        self._path = Path(database_path)
        if self._path.exists() and self._path.is_dir():
            raise ValueError("profiled evidence path must not be a directory")
        if not self._path.parent.exists():
            raise ValueError("profiled evidence parent directory does not exist")
        connection = sqlite3.connect(self._path)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("""CREATE TABLE IF NOT EXISTS orion_profiled_evidence (tenant_id TEXT NOT NULL, resource TEXT NOT NULL, profile_id TEXT NOT NULL, sequence INTEGER NOT NULL, created_at TEXT NOT NULL, payload_json TEXT NOT NULL, checksum_sha256 TEXT NOT NULL, PRIMARY KEY (tenant_id, resource, profile_id, sequence))""")
            connection.commit()
        finally:
            connection.close()

    def append(self, batch: ProfiledEvidenceBatch) -> None:
        payload = profiled_evidence_to_json(batch)
        checksum = profiled_evidence_checksum(payload)
        connection = sqlite3.connect(self._path)
        try:
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA busy_timeout=5000")
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute("SELECT payload_json, checksum_sha256 FROM orion_profiled_evidence WHERE tenant_id=? AND resource=? AND profile_id=? AND sequence=?", (batch.tenant_id, batch.resource, batch.profile_id, batch.sequence)).fetchone()
            if existing:
                if existing[0] == payload and hmac.compare_digest(existing[1], checksum):
                    connection.commit()
                    return
                raise HistoricalEvidenceConflictError("profiled sequence already exists with different state")
            latest = connection.execute("SELECT MAX(sequence) FROM orion_profiled_evidence WHERE tenant_id=? AND resource=? AND profile_id=?", (batch.tenant_id, batch.resource, batch.profile_id)).fetchone()[0]
            expected = 1 if latest is None else latest + 1
            if batch.sequence != expected:
                raise HistoricalEvidenceError("profiled sequence must be consecutive")
            existing_payloads = connection.execute("SELECT payload_json FROM orion_profiled_evidence WHERE tenant_id=? AND resource=? AND profile_id=?", (batch.tenant_id, batch.resource, batch.profile_id)).fetchall()
            prior = [profiled_evidence_from_json(item[0]) for item in existing_payloads]
            prior_names = {item.evidence.payload["record"]["name"] for old in prior for item in old.observations}
            prior_observations = {item.observation_id for old in prior for item in old.observations}
            prior_evidence = {item.evidence.evidence_id for old in prior for item in old.observations}
            if any(item.evidence.payload["record"]["name"] in prior_names or item.observation_id in prior_observations or item.evidence.evidence_id in prior_evidence for item in batch.observations):
                raise HistoricalEvidenceError("duplicate profiled identity across history")
            connection.execute("INSERT INTO orion_profiled_evidence VALUES (?,?,?,?,?,?,?)", (batch.tenant_id, batch.resource, batch.profile_id, batch.sequence, batch.created_at.isoformat(), payload, checksum))
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def load_all(self, *, tenant_id: str, resource: str, profile_id: str) -> tuple[ProfiledEvidenceBatch, ...]:
        if not all(isinstance(value, str) and value.strip() == value and value for value in (tenant_id, resource, profile_id)):
            raise HistoricalEvidenceError("profiled query scope is invalid")
        connection = sqlite3.connect(self._path)
        try:
            rows = connection.execute("SELECT sequence, created_at, payload_json, checksum_sha256 FROM orion_profiled_evidence WHERE tenant_id=? AND resource=? AND profile_id=? ORDER BY sequence", (tenant_id, resource, profile_id)).fetchall()
        finally:
            connection.close()
        batches = []
        names: set[str] = set()
        observation_ids = set()
        evidence_ids = set()
        for expected, (sequence, created_at, payload, stored_checksum) in enumerate(rows, 1):
            if sequence != expected or not hmac.compare_digest(stored_checksum, profiled_evidence_checksum(payload)):
                raise HistoricalEvidenceIntegrityError("profiled evidence integrity verification failed")
            try:
                batch = profiled_evidence_from_json(payload)
            except HistoricalEvidenceError as exc:
                raise HistoricalEvidenceIntegrityError("profiled evidence payload validation failed") from exc
            if batch.tenant_id != tenant_id or batch.resource != resource or batch.profile_id != profile_id or batch.sequence != sequence or batch.created_at.isoformat() != created_at:
                raise HistoricalEvidenceIntegrityError("profiled evidence envelope mismatch")
            for observation in batch.observations:
                name = observation.evidence.payload["record"]["name"]
                if name in names or observation.observation_id in observation_ids or observation.evidence.evidence_id in evidence_ids:
                    raise HistoricalEvidenceIntegrityError("duplicate profiled identity across history")
                names.add(name)
                observation_ids.add(observation.observation_id)
                evidence_ids.add(observation.evidence.evidence_id)
            batches.append(batch)
        return tuple(batches)
