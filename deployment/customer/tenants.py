"""Private tenant membership registry with independently approved bootstrap.

Only verified IdP identities enter this API. The bootstrap approval is signed
offline by an operator key whose private half is never present in the service.
No HTTP enrollment route is exposed by this module.
"""

from __future__ import annotations

import base64
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

from .oidc import VerifiedIdentity

_TENANT = re.compile(r"[a-z][a-z0-9-]{2,62}\Z")
_TEXT = re.compile(r"[A-Za-z0-9][A-Za-z0-9 &(),./_-]{0,127}\Z")
_COUNTRY = re.compile(r"[A-Z]{2}\Z")
_APPROVAL_ID = re.compile(r"[A-Za-z0-9_-]{20,80}\Z")
_ROLES = frozenset({"administrator", "analyst"})
_APPROVAL_DOMAIN = b"ORION CUSTOMER BOOTSTRAP V1\0"


class TenantDenied(ValueError):
    """Tenant membership, bootstrap, or session authority is unavailable."""


@dataclass(frozen=True, slots=True)
class TenantAccess:
    tenant_id: str
    role: str
    subject: str
    session_expires_at: int


def _valid_bootstrap(payload: dict) -> None:
    if set(payload) != {
        "approval_id", "tenant_id", "organization_name", "country", "timezone",
        "sector", "data_residency", "retention_days", "admin_issuer", "admin_subject",
        "expires_at",
    }:
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
                    status TEXT NOT NULL CHECK(status IN ('active','suspended'))
                );
                CREATE TABLE IF NOT EXISTS members (
                    tenant_id TEXT NOT NULL REFERENCES tenants(tenant_id),
                    issuer TEXT NOT NULL, subject TEXT NOT NULL,
                    role TEXT NOT NULL CHECK(role IN ('administrator','analyst')),
                    active INTEGER NOT NULL CHECK(active IN (0,1)),
                    PRIMARY KEY (tenant_id, issuer, subject)
                );
                CREATE TABLE IF NOT EXISTS consumed_approvals (
                    approval_id TEXT PRIMARY KEY
                );
                CREATE TABLE IF NOT EXISTS revoked_sessions (
                    issuer TEXT NOT NULL, session_id TEXT NOT NULL,
                    PRIMARY KEY (issuer, session_id)
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
                revoked = db.execute(
                    "SELECT 1 FROM revoked_sessions WHERE issuer=? AND session_id=?",
                    (administrator.issuer, administrator.session_id),
                ).fetchone()
                allowed = db.execute(
                    """SELECT 1 FROM members m JOIN tenants t USING(tenant_id)
                    WHERE m.tenant_id=? AND m.issuer=? AND m.subject=? AND m.active=1
                      AND m.role='administrator' AND t.status='active'""",
                    (tenant_id, administrator.issuer, administrator.subject),
                ).fetchone()
                if revoked or allowed is None:
                    raise TenantDenied("administrator membership required")
                db.execute(
                    "INSERT INTO members VALUES (?,?,?,?,1)",
                    (tenant_id, member.issuer, member.subject, role),
                )
                db.commit()
        except sqlite3.IntegrityError as exc:
            raise TenantDenied("member already enrolled") from exc

    def logout(self, identity: VerifiedIdentity) -> None:
        if not isinstance(identity, VerifiedIdentity):
            raise TenantDenied("authenticated session required")
        with self._connection() as db:
            db.execute(
                "INSERT OR IGNORE INTO revoked_sessions VALUES (?,?)",
                (identity.issuer, identity.session_id),
            )
