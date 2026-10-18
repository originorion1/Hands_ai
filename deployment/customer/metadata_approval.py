"""Separate signed approval and durable revocation for bounded metadata reads.

Portal proposals are never grants. An owner-signed packet binds the reviewed
proposal to one exact metadata grant. The private ledger serializes evidence
publication with revocation and persists denial across process restarts.
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import stat
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from .bounded_metadata import MetadataGrant, MetadataSessionDenied
from .portal import authorization_scope_digest

_DOMAIN = b"ORION METADATA APPROVAL V1\0"


def grant_scope(grant: MetadataGrant) -> dict[str, object]:
    """Identity-only packet: credential reference names, never values."""
    return {
        "authorization_id": grant.authorization_id, "tenant_id": grant.tenant_id,
        "external_customer_reference": grant.external_customer_reference,
        "company": grant.company, "origin": grant.origin,
        "credential_key_reference": grant.credential_references.api_key_variable,
        "credential_secret_reference": grant.credential_references.api_secret_variable,
        "credential_vault_reference": grant.credential_vault_reference,
        "fields_by_doctype": [[name, list(fields)] for name, fields in grant.fields_by_doctype],
        "seed_doctype": grant.seed_doctype, "starts_at": grant.starts_at.isoformat(),
        "expires_at": grant.expires_at.isoformat(), "max_attempts": grant.max_attempts,
        "max_response_bytes": grant.max_response_bytes,
        "max_evidence_bytes": grant.max_evidence_bytes,
        "retention_days": grant.retention_days,
        "read_only": True,
    }


def approval_packet(
    proposal: Mapping[str, object], grant: MetadataGrant, *, proposal_id: str,
) -> dict[str, object]:
    """Build the packet for human review; this does not activate a grant."""
    scope = {key: value for key, value in proposal.items() if key not in {
        "kind", "status", "execution_authority", "scope_sha256",
    }}
    digest = authorization_scope_digest(scope)
    if (proposal.get("kind") != "authorization_proposal"
            or proposal.get("status") != "pending_governed_validation"
            or proposal.get("execution_authority") is not False
            or proposal.get("scope_sha256") != digest
            or not proposal_id or proposal.get("tenant_id") != grant.tenant_id
            or proposal.get("read_only") is not True
            or grant.company not in proposal.get("companies", ())
            or [[name, list(fields)] for name, fields in grant.fields_by_doctype]
            != proposal.get("fields_by_doctype")
            or grant.max_attempts > proposal.get("max_requests", 0)
            or (grant.expires_at - grant.starts_at).total_seconds() >
            proposal.get("max_duration_seconds", 0)
            or grant.max_evidence_bytes > proposal.get("max_evidence_bytes", 0)
            or grant.retention_days > proposal.get("retention_days", 0)):
        raise MetadataSessionDenied("metadata grant exceeds reviewed proposal")
    return {
        "schema": "orion.metadata-approval.v1", "proposal_id": proposal_id,
        "proposal_sha256": digest, "grant": grant_scope(grant),
    }


def _body(packet: Mapping[str, object]) -> bytes:
    return json.dumps(packet, sort_keys=True, separators=(",", ":"),
                      allow_nan=False).encode()


class ApprovedMetadataGrantLedger:
    def __init__(
        self, path: Path, *, grant: MetadataGrant, proposal: Mapping[str, object],
        packet: Mapping[str, object],
        signature: bytes, approval_key: Ed25519PublicKey, existing: bool = False,
    ) -> None:
        if (not path.is_absolute() or path.is_symlink()
                or (path.exists() == (not existing))
                or not isinstance(approval_key, Ed25519PublicKey)
                or packet.get("schema") != "orion.metadata-approval.v1"
                or packet.get("grant") != grant_scope(grant)
                or not packet.get("proposal_id") or not packet.get("proposal_sha256")
                or packet != approval_packet(
                    proposal, grant, proposal_id=packet["proposal_id"]
                )):
            raise MetadataSessionDenied("separate metadata approval required")
        parent = path.parent
        info = parent.stat()
        if (parent.is_symlink() or not stat.S_ISDIR(info.st_mode)
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o700):
            raise MetadataSessionDenied("private approval ledger parent required")
        body = _body(packet)
        try:
            approval_key.verify(signature, _DOMAIN + body)
        except InvalidSignature as exc:
            raise MetadataSessionDenied("metadata approval signature denied") from exc
        self.path = path
        self.grant = grant
        self.digest = hashlib.sha256(body).hexdigest()
        self._claimed = False
        with self._connect(create=not existing) as db:
            if existing:
                row = db.execute(
                    "SELECT packet_sha256 FROM approvals WHERE authorization_id=?",
                    (self.grant.authorization_id,),
                ).fetchone()
                if row != (self.digest,):
                    raise MetadataSessionDenied("approval ledger identity changed")

    def _connect(self, *, create: bool = False) -> sqlite3.Connection:
        if create:
            descriptor = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(descriptor)
        info = self.path.stat()
        if (self.path.is_symlink() or not stat.S_ISREG(info.st_mode)
                or info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600):
            raise MetadataSessionDenied("approval ledger custody denied")
        db = sqlite3.connect(self.path, timeout=10)
        if create:
            with db:
                db.execute("CREATE TABLE approvals (authorization_id TEXT PRIMARY KEY, "
                           "packet_sha256 TEXT NOT NULL, status TEXT NOT NULL)")
                db.execute("INSERT INTO approvals VALUES (?,?, 'active')",
                           (self.grant.authorization_id, self.digest))
        return db

    def _status(self, db: sqlite3.Connection) -> tuple[str, str] | None:
        row = db.execute("SELECT packet_sha256,status FROM approvals WHERE authorization_id=?",
                         (self.grant.authorization_id,)).fetchone()
        return row

    def claim(self) -> None:
        """Consume the one permitted launch, before resolving credentials."""
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._claimed or self._status(db) != (self.digest, "active"):
                raise MetadataSessionDenied("metadata launch already used or revoked")
            db.execute("UPDATE approvals SET status='claimed' WHERE authorization_id=?",
                       (self.grant.authorization_id,))
            self._claimed = True

    def finish(self, *, complete: bool) -> None:
        """Retain consumed or interrupted state; neither permits replay."""
        if type(complete) is not bool:
            raise MetadataSessionDenied("explicit metadata completion state required")
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if self._claimed and self._status(db) == (self.digest, "claimed"):
                db.execute("UPDATE approvals SET status=? WHERE authorization_id=?",
                           ("consumed" if complete else "interrupted",
                            self.grant.authorization_id))
            self._claimed = False

    def lookup(self, authorization_id: str) -> MetadataGrant | None:
        if authorization_id != self.grant.authorization_id:
            return None
        with self._connect() as db:
            return self.grant if self._claimed and self._status(db) == (
                self.digest, "claimed",
            ) else None

    @contextmanager
    def commit_scope(self, authorization_id: str) -> Iterator[None]:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if (authorization_id != self.grant.authorization_id or not self._claimed
                    or self._status(db) != (self.digest, "claimed")):
                raise MetadataSessionDenied("metadata authorization revoked")
            yield

    def revoke(self) -> None:
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("UPDATE approvals SET status='revoked' WHERE authorization_id=?",
                       (self.grant.authorization_id,))
