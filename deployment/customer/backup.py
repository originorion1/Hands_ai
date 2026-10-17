"""Bounded encrypted backup of one tenant's synthetic evidence only."""

from __future__ import annotations

import base64
import hashlib
import json
import time

from cryptography.fernet import Fernet, InvalidToken

from .catalog import _unique_object
from .oidc import VerifiedIdentity
from .tenants import (
    _EVIDENCE_ID,
    _MAX_SYNTHETIC_EVIDENCE_BYTES,
    TenantDenied,
    TenantRegistry,
)

_MAX_ENCRYPTED_BYTES = 1024 * 1024
_MAX_RECORDS = 32
_MAX_AUDIT_RECORDS = 128


def _cipher(key: bytes) -> Fernet:
    try:
        return Fernet(key)
    except (TypeError, ValueError) as exc:
        raise TenantDenied("backup key unavailable") from exc


def backup_synthetic_tenant(
    registry: TenantRegistry, administrator: VerifiedIdentity, *, tenant_id: str,
    encryption_key: bytes,
) -> bytes:
    """Return authenticated ciphertext for private operator custody."""
    with registry._connection() as db:
        db.execute("BEGIN IMMEDIATE")
        registry._require_admin(db, administrator, tenant_id)
        rows = db.execute(
            """SELECT evidence_id,payload,digest,created_at FROM synthetic_evidence
            WHERE tenant_id=? ORDER BY evidence_id""",
            (tenant_id,),
        ).fetchall()
        audit_rows = db.execute(
            """SELECT evidence_id,digest,event,recorded_at FROM evidence_audit
            WHERE tenant_id=? ORDER BY evidence_id,event""",
            (tenant_id,),
        ).fetchall()
    if len(rows) > _MAX_RECORDS or len(audit_rows) > _MAX_AUDIT_RECORDS:
        raise TenantDenied("synthetic backup capacity exceeded")
    records = [
        {
            "evidence_id": evidence_id,
            "payload": base64.b64encode(payload).decode(),
            "digest": digest,
            "created_at": created_at,
        }
        for evidence_id, payload, digest, created_at in rows
    ]
    document = {
        "schema": "orion.synthetic-tenant-backup.v1",
        "tenant_id": tenant_id,
        "created_at": int(time.time()),
        "records": records,
        "audits": [
            {"evidence_id": evidence_id, "digest": digest, "event": event,
             "recorded_at": recorded_at}
            for evidence_id, digest, event, recorded_at in audit_rows
        ],
    }
    plaintext = json.dumps(document, sort_keys=True, separators=(",", ":")).encode()
    if len(plaintext) > _MAX_ENCRYPTED_BYTES // 2:
        raise TenantDenied("synthetic backup capacity exceeded")
    return _cipher(encryption_key).encrypt(plaintext)


def restore_synthetic_tenant(
    registry: TenantRegistry, administrator: VerifiedIdentity, *, tenant_id: str,
    encrypted_backup: bytes, encryption_key: bytes,
) -> int:
    """Restore only unexpired records into the same active tenant, atomically."""
    if type(encrypted_backup) is not bytes or not 0 < len(encrypted_backup) <= _MAX_ENCRYPTED_BYTES:
        raise TenantDenied("invalid synthetic backup")
    try:
        document = json.loads(
            _cipher(encryption_key).decrypt(encrypted_backup),
            object_pairs_hook=_unique_object,
        )
    except (InvalidToken, UnicodeError, json.JSONDecodeError, ValueError) as exc:
        raise TenantDenied("synthetic backup authentication failed") from exc
    if (
        type(document) is not dict
        or set(document) != {"schema", "tenant_id", "created_at", "records", "audits"}
        or document["schema"] != "orion.synthetic-tenant-backup.v1"
        or document["tenant_id"] != tenant_id
        or type(document["records"]) is not list
        or len(document["records"]) > _MAX_RECORDS
        or type(document["audits"]) is not list
        or len(document["audits"]) > _MAX_AUDIT_RECORDS
    ):
        raise TenantDenied("synthetic backup tenant mismatch")
    with registry._connection() as db:
        db.execute("BEGIN IMMEDIATE")
        registry._require_admin(db, administrator, tenant_id)
        policy = db.execute(
            "SELECT retention_days FROM tenants WHERE tenant_id=? AND status='active'",
            (tenant_id,),
        ).fetchone()
        if policy is None:
            raise TenantDenied("tenant retention policy unavailable")
        for record in document["audits"]:
            if (
                type(record) is not dict
                or set(record) != {"evidence_id", "digest", "event", "recorded_at"}
                or type(record["evidence_id"]) is not str
                or not _EVIDENCE_ID.fullmatch(record["evidence_id"])
                or type(record["digest"]) is not str
                or len(record["digest"]) != 64
                or type(record["event"]) is not str
                or record["event"] not in {"retention_deleted", "offboard_deleted", "restored"}
                or type(record["recorded_at"]) is not int
            ):
                raise TenantDenied("invalid synthetic backup audit")
            existing_audit = db.execute(
                """SELECT digest,recorded_at FROM evidence_audit
                WHERE tenant_id=? AND evidence_id=? AND event=?""",
                (tenant_id, record["evidence_id"], record["event"]),
            ).fetchone()
            if existing_audit is not None:
                if existing_audit != (record["digest"], record["recorded_at"]):
                    raise TenantDenied("conflicting synthetic backup audit")
                continue
            db.execute(
                "INSERT INTO evidence_audit VALUES (?,?,?,?,?)",
                (tenant_id, record["evidence_id"], record["digest"],
                 record["event"], record["recorded_at"]),
            )
        for record in document["records"]:
            if type(record) is not dict or set(record) != {
                "evidence_id", "payload", "digest", "created_at"
            }:
                raise TenantDenied("invalid synthetic backup record")
            try:
                payload = base64.b64decode(record["payload"], validate=True)
            except (TypeError, ValueError) as exc:
                raise TenantDenied("invalid synthetic backup record") from exc
            try:
                value = json.loads(payload, object_pairs_hook=_unique_object)
            except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
                raise TenantDenied("invalid synthetic backup record") from exc
            if (
                type(record["evidence_id"]) is not str
                or not _EVIDENCE_ID.fullmatch(record["evidence_id"])
                or type(record["digest"]) is not str
                or type(record["created_at"]) is not int
                or not 0 < len(payload) <= _MAX_SYNTHETIC_EVIDENCE_BYTES
                or record["created_at"] > time.time()
                or record["created_at"] + policy[0] * 86400 <= time.time()
                or hashlib.sha256(payload).hexdigest() != record["digest"]
                or type(value) is not dict
                or value.get("synthetic") is not True
            ):
                raise TenantDenied("invalid or expired synthetic backup record")
            existing = db.execute(
                "SELECT digest FROM synthetic_evidence WHERE tenant_id=? AND evidence_id=?",
                (tenant_id, record["evidence_id"]),
            ).fetchone()
            if existing is not None:
                raise TenantDenied("synthetic evidence already present")
            db.execute(
                "INSERT INTO synthetic_evidence VALUES (?,?,?,?,?)",
                (tenant_id, record["evidence_id"], payload, record["digest"], record["created_at"]),
            )
            existing_restoration = db.execute(
                """SELECT digest FROM evidence_audit
                WHERE tenant_id=? AND evidence_id=? AND event='restored'""",
                (tenant_id, record["evidence_id"]),
            ).fetchone()
            if existing_restoration is None:
                db.execute(
                    "INSERT INTO evidence_audit VALUES (?,?,?,?,?)",
                    (tenant_id, record["evidence_id"], record["digest"],
                     "restored", int(time.time())),
                )
            elif existing_restoration[0] != record["digest"]:
                raise TenantDenied("conflicting synthetic backup audit")
    return len(document["records"])
