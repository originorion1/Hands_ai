"""Private tenant membership registry with independently approved bootstrap.

Only verified IdP identities enter this API. The bootstrap approval is signed
offline by an operator key whose private half is never present in the service.
No HTTP enrollment route is exposed by this module.
"""

from __future__ import annotations

import base64
import hashlib
import json
import os
import re
import sqlite3
import stat
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from orion_connector_sdk import ConnectorRegistration

from .catalog import _unique_object
from .oidc import VerifiedIdentity

_TENANT = re.compile(r"[a-z][a-z0-9-]{2,62}\Z")
_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9 &(),./_-]{0,127}\Z")
_COUNTRY = re.compile(r"[A-Z]{2}\Z")
_APPROVAL_ID = re.compile(r"[A-Za-z0-9_-]{20,80}\Z")
_ROLES = frozenset({"administrator", "analyst"})
_APPROVAL_DOMAIN = b"ORION CUSTOMER BOOTSTRAP V1\0"
_LIFECYCLE_DOMAIN = b"ORION TENANT LIFECYCLE V1\0"
_LIFECYCLE_ACTIONS = frozenset({"suspend", "reactivate", "offboard"})
_CREDENTIAL_REF = re.compile(r"vault:[A-Za-z0-9_-]{24,128}\Z")
_LABEL = re.compile(r"[A-Za-z][A-Za-z0-9 _-]{0,63}\Z")
_EVIDENCE_ID = re.compile(r"[A-Za-z0-9_-]{12,80}\Z")
_MAX_SYNTHETIC_EVIDENCE_BYTES = 64 * 1024


def is_canonical_tenant_slug(value: object) -> bool:
    """Use the registry's single canonical tenant identity grammar."""
    return isinstance(value, str) and _TENANT.fullmatch(value) is not None


class TenantDenied(ValueError):
    """Tenant membership, bootstrap, or session authority is unavailable."""


@dataclass(frozen=True, slots=True)
class TenantAccess:
    tenant_id: str
    role: str
    subject: str
    session_expires_at: int


def _valid_bootstrap(payload: dict) -> None:
    required = {
        "approval_id", "tenant_id", "organization_name", "country", "timezone",
        "sector", "data_residency", "retention_days", "admin_issuer", "admin_subject",
        "expires_at",
    }
    if set(payload) not in (required, required | {"external_company_reference"}):
        raise TenantDenied("invalid onboarding approval")
    if (
        type(payload["approval_id"]) is not str
        or not _APPROVAL_ID.fullmatch(payload["approval_id"])
        or type(payload["tenant_id"]) is not str
        or not _TENANT.fullmatch(payload["tenant_id"])
        or any(
            type(payload[name]) is not str or not _TEXT.fullmatch(payload[name])
            for name in ("organization_name", "sector", "data_residency")
        )
        or type(payload["country"]) is not str
        or not _COUNTRY.fullmatch(payload["country"])
        or type(payload["timezone"]) is not str
        or type(payload["retention_days"]) is not int
        or not 1 <= payload["retention_days"] <= 3650
        or type(payload["admin_issuer"]) is not str
        or not payload["admin_issuer"].startswith("https://")
        or type(payload["admin_subject"]) is not str
        or not payload["admin_subject"].strip()
        or type(payload["expires_at"]) is not int
        or ("external_company_reference" in payload and (
            type(payload["external_company_reference"]) is not str
            or not _TEXT.fullmatch(payload["external_company_reference"])
        ))
    ):
        raise TenantDenied("invalid onboarding approval")
    try:
        ZoneInfo(payload["timezone"])
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise TenantDenied("invalid onboarding timezone") from exc


class TenantRegistry:
    """SQLite-backed tenant and session authority; no connector credentials."""

    def __init__(self, path: Path, *, approval_public_key: Ed25519PublicKey):
        if not isinstance(approval_public_key, Ed25519PublicKey):
            raise TypeError("trusted onboarding approval key required")
        self._path = path
        self._approval_key = approval_public_key
        with self._connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS tenants (
                    tenant_id TEXT PRIMARY KEY, organization_name TEXT NOT NULL,
                    country TEXT NOT NULL, timezone TEXT NOT NULL, sector TEXT NOT NULL,
                    data_residency TEXT NOT NULL, retention_days INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status IN ('active','suspended','offboarded'))
                );
                CREATE TABLE IF NOT EXISTS members (
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    issuer TEXT NOT NULL, subject TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('administrator','analyst')),
                    active INTEGER NOT NULL CHECK(active IN (0,1)),
                    PRIMARY KEY (tenant_id, issuer, subject)
                );
                CREATE TABLE IF NOT EXISTS member_invitations (
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    issuer TEXT NOT NULL, subject TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('administrator','analyst')),
                    expires_at INTEGER NOT NULL,
                    PRIMARY KEY (tenant_id, issuer, subject)
                );
                CREATE TABLE IF NOT EXISTS tenant_external_references (
                    tenant_id TEXT PRIMARY KEY REFERENCES tenants(tenant_id),
                    company_reference TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS consumed_approvals (
                    approval_id TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS revoked_sessions (
                    issuer TEXT NOT NULL, session_id TEXT NOT NULL,
                    PRIMARY KEY (issuer, session_id)
                );
                CREATE TABLE IF NOT EXISTS credential_refs (
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    label TEXT NOT NULL, connector TEXT NOT NULL,
                    credential_ref TEXT NOT NULL UNIQUE,
                    generation INTEGER NOT NULL CHECK(generation > 0),
                    status TEXT NOT NULL CHECK(status IN ('active','revoked')),
                    PRIMARY KEY (tenant_id, label)
                );
                CREATE TABLE IF NOT EXISTS connection_profiles (
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    label TEXT NOT NULL, endpoint TEXT NOT NULL,
                    company TEXT NOT NULL,
                    PRIMARY KEY (tenant_id, label)
                );
                CREATE TABLE IF NOT EXISTS authorization_proposals (
                    proposal_id TEXT PRIMARY KEY, tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    scope_sha256 TEXT NOT NULL, payload_json TEXT NOT NULL,
                    submitted_at INTEGER NOT NULL,
                    status TEXT NOT NULL CHECK(status='pending_governed_validation')
                );
                CREATE TABLE IF NOT EXISTS retired_credential_refs (
                    credential_ref TEXT PRIMARY KEY,
                    tenant_id TEXT NOT NULL, label TEXT NOT NULL,
                    generation INTEGER NOT NULL, retired_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS synthetic_evidence (
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    evidence_id TEXT NOT NULL, payload BLOB NOT NULL,
                    digest TEXT NOT NULL, created_at INTEGER NOT NULL,
                    PRIMARY KEY (tenant_id, evidence_id)
                );
                CREATE TABLE IF NOT EXISTS evidence_audit (
                    tenant_id TEXT NOT NULL, evidence_id TEXT NOT NULL,
                    digest TEXT NOT NULL, event TEXT NOT NULL,
                    recorded_at INTEGER NOT NULL,
                    PRIMARY KEY (tenant_id, evidence_id, event)
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        if (
            not self._path.is_absolute()
            or self._path.parent.is_symlink()
            or not self._path.parent.is_dir()
            or self._path.parent.stat().st_mode & 0o077
            or self._path.is_symlink()
        ):
            raise TenantDenied("tenant registry custody unavailable")
        if not self._path.exists():
            try:
                descriptor = os.open(
                    self._path, os.O_CREAT | os.O_EXCL | os.O_RDWR | os.O_NOFOLLOW, 0o600
                )
                os.close(descriptor)
            except FileExistsError:
                pass
        if self._path.is_symlink():
            raise TenantDenied("tenant registry custody unavailable")
        metadata = self._path.stat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_mode & 0o077:
            raise TenantDenied("tenant registry permissions unsafe")
        db = sqlite3.connect(self._path, timeout=5, isolation_level=None)
        db.execute("PRAGMA foreign_keys=ON")
        try:
            db.execute(
                "CREATE TABLE IF NOT EXISTS registry_clock ("
                "id INTEGER PRIMARY KEY CHECK(id=1), latest_second INTEGER NOT NULL)"
            )
            db.execute("BEGIN IMMEDIATE")
            now = int(time.time())
            previous = db.execute(
                "SELECT latest_second FROM registry_clock WHERE id=1"
            ).fetchone()
            if previous is not None and now < previous[0]:
                raise TenantDenied("tenant registry clock rollback denied")
            db.execute(
                "INSERT INTO registry_clock(id,latest_second) VALUES (1,?) "
                "ON CONFLICT(id) DO UPDATE SET latest_second=excluded.latest_second",
                (now,),
            )
            db.commit()
        except BaseException:
            db.rollback()
            db.close()
            raise
        return db

    @contextmanager
    def _connection(self):
        db = self._connect()
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    def provision(self, identity: VerifiedIdentity, *, approval: dict, signature: str) -> str:
        if not isinstance(identity, VerifiedIdentity) or identity.expires_at <= time.time():
            raise TenantDenied("authenticated administrator required")
        if type(approval) is not dict:
            raise TenantDenied("invalid onboarding approval")
        _valid_bootstrap(approval)
        if (
            approval["expires_at"] <= time.time()
            or approval["admin_issuer"] != identity.issuer
            or approval["admin_subject"] != identity.subject
        ):
            raise TenantDenied("onboarding approval expired or mismatched")
        try:
            decoded = base64.b64decode(signature, validate=True)
            self._approval_key.verify(
                decoded,
                _APPROVAL_DOMAIN
                + json.dumps(approval, sort_keys=True, separators=(",", ":")).encode(),
            )
        except (TypeError, ValueError, InvalidSignature) as exc:
            raise TenantDenied("onboarding approval signature invalid") from exc
        try:
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                revoked = db.execute(
                    "SELECT 1 FROM revoked_sessions WHERE issuer=? AND session_id=?",
                    (identity.issuer, identity.session_id),
                ).fetchone()
                if revoked:
                    raise TenantDenied("onboarding identity session revoked")
                db.execute(
                    "INSERT INTO consumed_approvals(approval_id) VALUES (?)",
                    (approval["approval_id"],),
                )
                db.execute(
                    "INSERT INTO tenants VALUES (?,?,?,?,?,?,?,?)",
                    (
                        approval["tenant_id"], approval["organization_name"],
                        approval["country"], approval["timezone"], approval["sector"],
                        approval["data_residency"], approval["retention_days"], "active",
                    ),
                )
                db.execute(
                    "INSERT INTO members VALUES (?,?,?,?,1)",
                    (approval["tenant_id"], identity.issuer, identity.subject, "administrator"),
                )
                if "external_company_reference" in approval:
                    db.execute(
                        "INSERT INTO tenant_external_references VALUES (?,?)",
                        (approval["tenant_id"], approval["external_company_reference"]),
                    )
                db.commit()
        except sqlite3.IntegrityError as exc:
            raise TenantDenied("tenant or onboarding approval already used") from exc
        return approval["tenant_id"]

    def authorize(
        self, identity: VerifiedIdentity, *, tenant_id: str, required_role: str = "analyst"
    ) -> TenantAccess:
        if (
            not isinstance(identity, VerifiedIdentity)
            or identity.expires_at <= time.time()
            or required_role not in _ROLES
        ):
            raise TenantDenied("authenticated membership required")
        with self._connection() as db:
            revoked = db.execute(
                "SELECT 1 FROM revoked_sessions WHERE issuer=? AND session_id=?",
                (identity.issuer, identity.session_id),
            ).fetchone()
            row = db.execute(
                """SELECT m.role FROM members m JOIN tenants t USING(tenant_id)
                WHERE m.tenant_id=? AND m.issuer=? AND m.subject=?
                  AND m.active=1 AND t.status='active'""",
                (tenant_id, identity.issuer, identity.subject),
            ).fetchone()
        if revoked or row is None or (required_role == "administrator" and row[0] != "administrator"):
            raise TenantDenied("authenticated membership required")
        return TenantAccess(tenant_id, row[0], identity.subject, identity.expires_at)

    def add_member(
        self, administrator: VerifiedIdentity, *, tenant_id: str,
        member: VerifiedIdentity, role: str,
    ) -> None:
        if (
            not isinstance(administrator, VerifiedIdentity)
            or not isinstance(member, VerifiedIdentity)
            or administrator.expires_at <= time.time()
            or member.expires_at <= time.time()
            or role not in _ROLES
        ):
            raise TenantDenied("verified member and role required")
        try:
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                self._require_admin(db, administrator, tenant_id)
                revoked = db.execute(
                    "SELECT 1 FROM revoked_sessions WHERE issuer=? AND session_id=?",
                    (member.issuer, member.session_id),
                ).fetchone()
                if revoked:
                    raise TenantDenied("member identity session revoked")
                db.execute(
                    "INSERT INTO members VALUES (?,?,?,?,1)",
                    (tenant_id, member.issuer, member.subject, role),
                )
                db.commit()
        except sqlite3.IntegrityError as exc:
            raise TenantDenied("member already enrolled") from exc

    def invite_member(
        self, administrator: VerifiedIdentity, *, tenant_id: str,
        issuer: str, subject: str, role: str, expires_at: int,
    ) -> None:
        """Create an exact-identity invitation; an IdP-verified user must accept it."""
        if (
            type(issuer) is not str or not issuer.startswith("https://")
            or type(subject) is not str or not subject.strip() or len(subject) > 256
            or role not in _ROLES or type(expires_at) is not int
            or not time.time() < expires_at <= time.time() + 7 * 86400
        ):
            raise TenantDenied("invalid member invitation")
        try:
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                self._require_admin(db, administrator, tenant_id)
                if db.execute(
                    "SELECT 1 FROM members WHERE tenant_id=? AND issuer=? AND subject=? AND active=1",
                    (tenant_id, issuer, subject),
                ).fetchone():
                    raise TenantDenied("member already active")
                db.execute(
                    "INSERT INTO member_invitations VALUES (?,?,?,?,?)",
                    (tenant_id, issuer, subject, role, expires_at),
                )
        except sqlite3.IntegrityError as exc:
            raise TenantDenied("member invitation already exists") from exc

    def accept_invitation(self, identity: VerifiedIdentity, *, tenant_id: str) -> TenantAccess:
        """Consume an invitation only for the exact verified IdP subject."""
        if not isinstance(identity, VerifiedIdentity) or identity.expires_at <= time.time():
            raise TenantDenied("verified invited identity required")
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute(
                "SELECT 1 FROM tenants WHERE tenant_id=? AND status='active'", (tenant_id,),
            ).fetchone()
            revoked = db.execute(
                "SELECT 1 FROM revoked_sessions WHERE issuer=? AND session_id=?",
                (identity.issuer, identity.session_id),
            ).fetchone()
            row = db.execute(
                "SELECT role,expires_at FROM member_invitations WHERE tenant_id=? AND issuer=? AND subject=?",
                (tenant_id, identity.issuer, identity.subject),
            ).fetchone()
            if active is None or revoked or row is None or row[1] <= time.time():
                raise TenantDenied("member invitation unavailable")
            db.execute(
                "INSERT INTO members VALUES (?,?,?,?,1) "
                "ON CONFLICT(tenant_id,issuer,subject) DO UPDATE SET role=excluded.role,active=1",
                (tenant_id, identity.issuer, identity.subject, row[0]),
            )
            db.execute(
                "DELETE FROM member_invitations WHERE tenant_id=? AND issuer=? AND subject=?",
                (tenant_id, identity.issuer, identity.subject),
            )
        return TenantAccess(tenant_id, row[0], identity.subject, identity.expires_at)

    def remove_member(
        self, administrator: VerifiedIdentity, *, tenant_id: str,
        issuer: str, subject: str,
    ) -> None:
        """Recheck administrator authority and deny removal of the last administrator."""
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_admin(db, administrator, tenant_id)
            row = db.execute(
                "SELECT role FROM members WHERE tenant_id=? AND issuer=? AND subject=? AND active=1",
                (tenant_id, issuer, subject),
            ).fetchone()
            if row is None:
                raise TenantDenied("member unavailable")
            if row[0] == "administrator":
                remaining = db.execute(
                    "SELECT COUNT(*) FROM members WHERE tenant_id=? AND role='administrator' AND active=1",
                    (tenant_id,),
                ).fetchone()[0]
                if remaining <= 1:
                    raise TenantDenied("last administrator cannot be removed")
            db.execute(
                "UPDATE members SET active=0 WHERE tenant_id=? AND issuer=? AND subject=?",
                (tenant_id, issuer, subject),
            )
            db.execute(
                "DELETE FROM member_invitations WHERE tenant_id=? AND issuer=? AND subject=?",
                (tenant_id, issuer, subject),
            )

    @staticmethod
    def _require_admin(db: sqlite3.Connection, identity: VerifiedIdentity, tenant_id: str) -> None:
        if not isinstance(identity, VerifiedIdentity) or identity.expires_at <= time.time():
            raise TenantDenied("administrator membership required")
        revoked = db.execute(
            "SELECT 1 FROM revoked_sessions WHERE issuer=? AND session_id=?",
            (identity.issuer, identity.session_id),
        ).fetchone()
        allowed = db.execute(
            """SELECT 1 FROM members m JOIN tenants t USING(tenant_id)
            WHERE m.tenant_id=? AND m.issuer=? AND m.subject=? AND m.active=1
              AND m.role='administrator' AND t.status='active'""",
            (tenant_id, identity.issuer, identity.subject),
        ).fetchone()
        if revoked or allowed is None:
            raise TenantDenied("administrator membership required")

    def enroll_credential_reference(
        self, administrator: VerifiedIdentity, *, tenant_id: str,
        connector: ConnectorRegistration, label: str, credential_ref: str,
        endpoint: str | None = None, company: str | None = None,
    ) -> None:
        """Record an external vault reference, never a submitted API secret."""
        if (
            not isinstance(connector, ConnectorRegistration)
            or connector.execution_allowed is not False
            or type(label) is not str
            or not _LABEL.fullmatch(label)
            or type(credential_ref) is not str
            or not _CREDENTIAL_REF.fullmatch(credential_ref)
            or (endpoint is None) != (company is None)
            or (endpoint is not None and (
                type(endpoint) is not str or not endpoint.startswith("https://")
                or type(company) is not str or not _TEXT.fullmatch(company)
            ))
        ):
            raise TenantDenied("invalid reviewed credential reference")
        try:
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                self._require_admin(db, administrator, tenant_id)
                db.execute(
                    "INSERT INTO credential_refs VALUES (?,?,?,?,1,'active')",
                    (tenant_id, label, connector.name, credential_ref),
                )
                if endpoint is not None:
                    db.execute(
                        "INSERT INTO connection_profiles VALUES (?,?,?,?)",
                        (tenant_id, label, endpoint, company),
                    )
        except sqlite3.IntegrityError as exc:
            raise TenantDenied("credential reference already enrolled") from exc

    def rotate_credential_reference(
        self, administrator: VerifiedIdentity, *, tenant_id: str,
        label: str, new_credential_ref: str,
    ) -> str:
        if type(new_credential_ref) is not str or not _CREDENTIAL_REF.fullmatch(new_credential_ref):
            raise TenantDenied("invalid reviewed credential reference")
        try:
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                self._require_admin(db, administrator, tenant_id)
                old = db.execute(
                    """SELECT credential_ref,generation FROM credential_refs
                    WHERE tenant_id=? AND label=? AND status='active'""",
                    (tenant_id, label),
                ).fetchone()
                if old is None:
                    raise TenantDenied("credential reference unavailable")
                db.execute(
                    "INSERT INTO retired_credential_refs VALUES (?,?,?,?,?)",
                    (old[0], tenant_id, label, old[1], int(time.time())),
                )
                changed = db.execute(
                    """UPDATE credential_refs SET credential_ref=?, generation=generation+1
                    WHERE tenant_id=? AND label=? AND status='active'""",
                    (new_credential_ref, tenant_id, label),
                ).rowcount
                if changed != 1:
                    raise TenantDenied("credential reference unavailable")
        except sqlite3.IntegrityError as exc:
            raise TenantDenied("credential reference already enrolled") from exc
        return old[0]

    def revoke_credential_reference(
        self, administrator: VerifiedIdentity, *, tenant_id: str, label: str,
    ) -> str:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_admin(db, administrator, tenant_id)
            old = db.execute(
                """SELECT credential_ref,generation FROM credential_refs
                WHERE tenant_id=? AND label=? AND status='active'""",
                (tenant_id, label),
            ).fetchone()
            if old is None:
                raise TenantDenied("credential reference unavailable")
            db.execute(
                "INSERT INTO retired_credential_refs VALUES (?,?,?,?,?)",
                (old[0], tenant_id, label, old[1], int(time.time())),
            )
            changed = db.execute(
                "UPDATE credential_refs SET status='revoked' WHERE tenant_id=? AND label=? AND status='active'",
                (tenant_id, label),
            ).rowcount
            if changed != 1:
                raise TenantDenied("credential reference unavailable")
        return old[0]

    def put_synthetic_evidence(
        self, identity: VerifiedIdentity, *, tenant_id: str,
        evidence_id: str, payload: bytes,
    ) -> str:
        """Persist only explicitly marked synthetic evidence under current membership."""
        if (
            type(evidence_id) is not str
            or not _EVIDENCE_ID.fullmatch(evidence_id)
            or type(payload) is not bytes
            or not 0 < len(payload) <= _MAX_SYNTHETIC_EVIDENCE_BYTES
        ):
            raise TenantDenied("invalid synthetic evidence")
        try:
            value = json.loads(payload, object_pairs_hook=_unique_object)
        except (UnicodeError, json.JSONDecodeError, ValueError) as exc:
            raise TenantDenied("invalid synthetic evidence") from exc
        if type(value) is not dict or value.get("synthetic") is not True:
            raise TenantDenied("synthetic evidence marker required")
        digest = hashlib.sha256(payload).hexdigest()
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_member(db, identity, tenant_id)
            try:
                db.execute(
                    "INSERT INTO synthetic_evidence VALUES (?,?,?,?,?)",
                    (tenant_id, evidence_id, payload, digest, int(time.time())),
                )
            except sqlite3.IntegrityError as exc:
                raise TenantDenied("evidence identifier already used") from exc
        return digest

    def get_synthetic_evidence(
        self, identity: VerifiedIdentity, *, tenant_id: str, evidence_id: str,
    ) -> bytes:
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_member(db, identity, tenant_id)
            row = db.execute(
                "SELECT payload,digest FROM synthetic_evidence WHERE tenant_id=? AND evidence_id=?",
                (tenant_id, evidence_id),
            ).fetchone()
        if row is None or hashlib.sha256(row[0]).hexdigest() != row[1]:
            raise TenantDenied("evidence unavailable or corrupt")
        return row[0]

    def purge_expired_synthetic_evidence(self, *, tenant_id: str) -> int:
        """Apply the signed onboarding retention period, preserving digest audit."""
        now = int(time.time())
        with self._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT retention_days FROM tenants WHERE tenant_id=?",
                (tenant_id,),
            ).fetchone()
            if row is None:
                raise TenantDenied("tenant retention policy unavailable")
            expired = db.execute(
                """SELECT evidence_id,digest FROM synthetic_evidence
                WHERE tenant_id=? AND created_at <= ?""",
                (tenant_id, now - row[0] * 86400),
            ).fetchall()
            for evidence_id, digest in expired:
                db.execute(
                    "INSERT INTO evidence_audit VALUES (?,?,?,?,?)",
                    (tenant_id, evidence_id, digest, "retention_deleted", now),
                )
                db.execute(
                    "DELETE FROM synthetic_evidence WHERE tenant_id=? AND evidence_id=?",
                    (tenant_id, evidence_id),
                )
        return len(expired)

    @staticmethod
    def _require_member(db: sqlite3.Connection, identity: VerifiedIdentity, tenant_id: str) -> None:
        if not isinstance(identity, VerifiedIdentity) or identity.expires_at <= time.time():
            raise TenantDenied("authenticated membership required")
        revoked = db.execute(
            "SELECT 1 FROM revoked_sessions WHERE issuer=? AND session_id=?",
            (identity.issuer, identity.session_id),
        ).fetchone()
        allowed = db.execute(
            """SELECT 1 FROM members m JOIN tenants t USING(tenant_id)
            WHERE m.tenant_id=? AND m.issuer=? AND m.subject=? AND m.active=1
              AND t.status='active'""",
            (tenant_id, identity.issuer, identity.subject),
        ).fetchone()
        if revoked or allowed is None:
            raise TenantDenied("authenticated membership required")

    def logout(self, identity: VerifiedIdentity) -> None:
        if not isinstance(identity, VerifiedIdentity):
            raise TenantDenied("authenticated session required")
        with self._connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO revoked_sessions VALUES (?,?)",
                (identity.issuer, identity.session_id),
            )

    def apply_lifecycle_decision(self, *, decision: dict, signature: str) -> str:
        """Consume one operator-signed state transition, including emergency stop."""
        if (
            type(decision) is not dict
            or set(decision) != {"approval_id", "tenant_id", "action", "expires_at"}
            or type(decision["approval_id"]) is not str
            or not _APPROVAL_ID.fullmatch(decision["approval_id"])
            or type(decision["tenant_id"]) is not str
            or not _TENANT.fullmatch(decision["tenant_id"])
            or type(decision["action"]) is not str
            or decision["action"] not in _LIFECYCLE_ACTIONS
            or type(decision["expires_at"]) is not int
            or decision["expires_at"] <= time.time()
        ):
            raise TenantDenied("invalid tenant lifecycle decision")
        try:
            self._approval_key.verify(
                base64.b64decode(signature, validate=True),
                _LIFECYCLE_DOMAIN
                + json.dumps(decision, sort_keys=True, separators=(",", ":")).encode(),
            )
        except (TypeError, ValueError, InvalidSignature) as exc:
            raise TenantDenied("tenant lifecycle signature invalid") from exc
        expected, target = {
            "suspend": ("active", "suspended"),
            "reactivate": ("suspended", "active"),
            "offboard": ("suspended", "offboarded"),
        }[decision["action"]]
        try:
            with self._connection() as db:
                db.execute("BEGIN IMMEDIATE")
                db.execute(
                    "INSERT INTO consumed_approvals VALUES (?)", (decision["approval_id"],)
                )
                changed = db.execute(
                    "UPDATE tenants SET status=? WHERE tenant_id=? AND status=?",
                    (target, decision["tenant_id"], expected),
                ).rowcount
                if changed != 1:
                    raise TenantDenied("tenant lifecycle transition unavailable")
                if decision["action"] == "offboard":
                    now = int(time.time())
                    for evidence_id, digest in db.execute(
                        "SELECT evidence_id,digest FROM synthetic_evidence WHERE tenant_id=?",
                        (decision["tenant_id"],),
                    ).fetchall():
                        db.execute(
                            "INSERT INTO evidence_audit VALUES (?,?,?,?,?)",
                            (decision["tenant_id"], evidence_id, digest, "offboard_deleted", now),
                        )
                    db.execute(
                        "DELETE FROM synthetic_evidence WHERE tenant_id=?",
                        (decision["tenant_id"],),
                    )
                    for label, reference, generation in db.execute(
                        """SELECT label,credential_ref,generation FROM credential_refs
                        WHERE tenant_id=? AND status='active'""",
                        (decision["tenant_id"],),
                    ).fetchall():
                        db.execute(
                            "INSERT INTO retired_credential_refs VALUES (?,?,?,?,?)",
                            (reference, decision["tenant_id"], label, generation, now),
                        )
                    db.execute(
                        "UPDATE credential_refs SET status='revoked' WHERE tenant_id=?",
                        (decision["tenant_id"],),
                    )
                    db.execute(
                        "UPDATE members SET active=0 WHERE tenant_id=?",
                        (decision["tenant_id"],),
                    )
                    db.execute(
                        "DELETE FROM member_invitations WHERE tenant_id=?",
                        (decision["tenant_id"],),
                    )
                    db.execute(
                        "DELETE FROM authorization_proposals WHERE tenant_id=?",
                        (decision["tenant_id"],),
                    )
                    db.execute(
                        "DELETE FROM connection_profiles WHERE tenant_id=?",
                        (decision["tenant_id"],),
                    )
                    db.execute(
                        "DELETE FROM tenant_external_references WHERE tenant_id=?",
                        (decision["tenant_id"],),
                    )
        except sqlite3.IntegrityError as exc:
            raise TenantDenied("tenant lifecycle decision already used") from exc
        return target
