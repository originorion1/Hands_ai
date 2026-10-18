"""Session-bound customer portal operations; no public listener or ERP access.

An HTTPS adapter may expose these methods only after independent review. Every
browser-selected tenant is checked against the current server-side session.
"""

from __future__ import annotations

import hmac
import json
import time
from dataclasses import dataclass
from uuid import uuid4

from .catalog import _unique_object
from .login import CustomerLogin, LoginDenied, _digest
from .portal import AuthorizationProposal, authorization_scope_digest
from .tenants import TenantRegistry


@dataclass(frozen=True, slots=True)
class CustomerPortalService:
    login: CustomerLogin
    registry: TenantRegistry

    def overview(self, session_secret: str) -> dict[str, object]:
        access = self.login.authorize(session_secret)
        identity = self.login._verified_session_identity(session_secret)
        with self.registry._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_session_scope(db, session_secret, access.tenant_id)
            self.registry._require_member(db, identity, access.tenant_id)
            tenant = db.execute(
                "SELECT organization_name,country,timezone,retention_days,status "
                "FROM tenants WHERE tenant_id=?", (access.tenant_id,),
            ).fetchone()
            external = db.execute(
                "SELECT company_reference FROM tenant_external_references WHERE tenant_id=?",
                (access.tenant_id,),
            ).fetchone()
            connections = db.execute(
                "SELECT c.label,c.connector,c.status,p.endpoint,p.company "
                "FROM credential_refs c LEFT JOIN connection_profiles p "
                "ON p.tenant_id=c.tenant_id AND p.label=c.label "
                "WHERE c.tenant_id=? ORDER BY c.label", (access.tenant_id,),
            ).fetchall()
        return {
            "tenant_id": access.tenant_id, "role": access.role,
            "subject": access.subject, "session_expires_at": access.session_expires_at,
            "organization_name": tenant[0], "country": tenant[1],
            "timezone": tenant[2], "retention_days": tenant[3],
            "tenant_status": tenant[4],
            "external_company_reference": external[0] if external else None,
            "connections": [
                {"label": label, "connector": connector, "status": status,
                 "endpoint": endpoint, "company": company,
                 "execution_allowed": False}
                for label, connector, status, endpoint, company in connections
            ],
            "authorization_status": "no_approved_grant_bound",
            "autonomous_study_proposals": [],
            "evidence_coverage": None,
            "investigation_progress": None,
            "findings": [],
        }

    def invite(
        self, session_secret: str, *, csrf_token: str, issuer: str,
        subject: str, role: str, expires_at: int,
    ) -> None:
        access = self.login.authorize(
            session_secret, required_role="administrator", mutating=True,
            csrf_token=csrf_token,
        )
        self.registry.invite_member(
            self.login._verified_session_identity(session_secret),
            tenant_id=access.tenant_id, issuer=issuer, subject=subject,
            role=role, expires_at=expires_at,
        )

    def remove(
        self, session_secret: str, *, csrf_token: str, issuer: str, subject: str,
    ) -> None:
        access = self.login.authorize(
            session_secret, required_role="administrator", mutating=True,
            csrf_token=csrf_token,
        )
        self.registry.remove_member(
            self.login._verified_session_identity(session_secret),
            tenant_id=access.tenant_id, issuer=issuer, subject=subject,
        )

    def submit_authorization(
        self, session_secret: str, *, csrf_token: str,
        proposal: AuthorizationProposal,
    ) -> str:
        access = self.login.authorize(
            session_secret, required_role="administrator", mutating=True,
            csrf_token=csrf_token,
        )
        if not isinstance(proposal, AuthorizationProposal) or proposal.tenant_id != access.tenant_id:
            raise LoginDenied("authorization proposal tenant differs from session")
        packet = proposal.review_packet()
        proposal_id = uuid4().hex
        identity = self.login._verified_session_identity(session_secret)
        with self.registry._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_session_scope(db, session_secret, access.tenant_id)
            self.registry._require_admin(db, identity, access.tenant_id)
            db.execute(
                "INSERT INTO authorization_proposals VALUES (?,?,?,?,?,?)",
                (proposal_id, access.tenant_id, packet["scope_sha256"],
                 json.dumps(packet, sort_keys=True, separators=(",", ":")),
                 int(time.time()), "pending_governed_validation"),
            )
        return proposal_id

    def authorization_proposal(
        self, session_secret: str, *, proposal_id: str,
    ) -> dict[str, object]:
        access = self.login.authorize(session_secret, required_role="administrator")
        identity = self.login._verified_session_identity(session_secret)
        with self.registry._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            self._require_session_scope(db, session_secret, access.tenant_id)
            self.registry._require_admin(db, identity, access.tenant_id)
            row = db.execute(
                "SELECT payload_json,scope_sha256 FROM authorization_proposals "
                "WHERE tenant_id=? AND proposal_id=?",
                (access.tenant_id, proposal_id),
            ).fetchone()
        if row is None:
            raise LoginDenied("authorization proposal unavailable")
        try:
            packet = json.loads(row[0], object_pairs_hook=_unique_object)
            scope = {key: value for key, value in packet.items() if key not in {
                "kind", "status", "execution_authority", "scope_sha256",
            }}
            if (
                packet["kind"] != "authorization_proposal"
                or packet["status"] != "pending_governed_validation"
                or packet["execution_authority"] is not False
                or packet["tenant_id"] != access.tenant_id
                or not hmac.compare_digest(packet["scope_sha256"], row[1])
                or not hmac.compare_digest(authorization_scope_digest(scope), row[1])
            ):
                raise LoginDenied("authorization proposal integrity denied")
        except (TypeError, ValueError, KeyError) as exc:
            raise LoginDenied("authorization proposal integrity denied") from exc
        return packet

    @staticmethod
    def _require_session_scope(db, session_secret: str, tenant_id: str) -> None:
        row = db.execute(
            "SELECT tenant_id,expires_at FROM browser_sessions WHERE session_sha256=?",
            (_digest(session_secret),),
        ).fetchone()
        if row is None or row[0] != tenant_id or row[1] <= time.time():
            raise LoginDenied("authenticated session scope changed")
