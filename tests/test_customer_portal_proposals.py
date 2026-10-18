"""Offline multi-user lifecycle and review-only onboarding boundaries."""

from __future__ import annotations

import base64
import json
import time
from datetime import date

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from deployment.customer.oidc import VerifiedIdentity
from deployment.customer.portal import (
    AuthorizationProposal,
    OrganizationProposal,
    PortalProposalDenied,
)
from deployment.customer.tenants import TenantDenied, TenantRegistry

ISSUER = "https://idp.example.invalid/tenant"


def _identity(subject: str, session: str) -> VerifiedIdentity:
    return VerifiedIdentity(ISSUER, subject, session, int(time.time()) + 300)


def _bootstrap(registry, signer, tenant, subject, nonce, company):
    approval = {
        "approval_id": f"synthetic-portal-{nonce:04d}",
        "tenant_id": tenant, "organization_name": f"Synthetic {tenant}",
        "external_company_reference": company,
        "country": "US", "timezone": "UTC", "sector": "hospitality",
        "data_residency": "approved-region", "retention_days": 30,
        "admin_issuer": ISSUER, "admin_subject": subject,
        "expires_at": int(time.time()) + 300,
    }
    payload = b"ORION CUSTOMER BOOTSTRAP V1\0" + json.dumps(
        approval, sort_keys=True, separators=(",", ":")
    ).encode()
    registry.provision(
        _identity(subject, f"bootstrap-{nonce}"), approval=approval,
        signature=base64.b64encode(signer.sign(payload)).decode(),
    )


@pytest.fixture
def tenants(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    signer = Ed25519PrivateKey.generate()
    registry = TenantRegistry(private / "tenant.sqlite", approval_public_key=signer.public_key())
    _bootstrap(registry, signer, "tenant-alpha", "alpha-admin", 1001, "ERP Alpha Company")
    _bootstrap(registry, signer, "tenant-beta", "beta-admin", 1002, "ERP Beta Company")
    return registry


def test_invitation_acceptance_is_exact_identity_one_use_and_tenant_scoped(tenants):
    admin = _identity("alpha-admin", "admin-current")
    analyst = _identity("analyst", "analyst-current")
    tenants.invite_member(
        admin, tenant_id="tenant-alpha", issuer=ISSUER, subject=analyst.subject,
        role="analyst", expires_at=int(time.time()) + 120,
    )
    with pytest.raises(TenantDenied):
        tenants.accept_invitation(_identity("wrong-user", "wrong"), tenant_id="tenant-alpha")
    with pytest.raises(TenantDenied):
        tenants.accept_invitation(analyst, tenant_id="tenant-beta")
    access = tenants.accept_invitation(analyst, tenant_id="tenant-alpha")
    assert access.role == "analyst"
    with pytest.raises(TenantDenied):
        tenants.accept_invitation(analyst, tenant_id="tenant-alpha")
    with pytest.raises(TenantDenied):
        tenants.authorize(analyst, tenant_id="tenant-alpha", required_role="administrator")
    tenants.remove_member(admin, tenant_id="tenant-alpha", issuer=ISSUER, subject="analyst")
    with pytest.raises(TenantDenied):
        tenants.authorize(analyst, tenant_id="tenant-alpha")
    assert tenants.authorize(_identity("beta-admin", "beta-current"), tenant_id="tenant-beta")


def test_invitation_denies_analyst_revoked_expired_and_last_admin_removal(tenants):
    admin = _identity("alpha-admin", "admin-current")
    analyst = _identity("analyst", "analyst-current")
    with pytest.raises(TenantDenied, match="last administrator"):
        tenants.remove_member(admin, tenant_id="tenant-alpha", issuer=ISSUER, subject="alpha-admin")
    with pytest.raises(TenantDenied, match="invalid member invitation"):
        tenants.invite_member(
            admin, tenant_id="tenant-alpha", issuer=ISSUER, subject="analyst",
            role="analyst", expires_at=int(time.time()) - 1,
        )
    tenants.invite_member(
        admin, tenant_id="tenant-alpha", issuer=ISSUER, subject="analyst",
        role="analyst", expires_at=int(time.time()) + 120,
    )
    tenants.logout(analyst)
    with pytest.raises(TenantDenied, match="invitation unavailable"):
        tenants.accept_invitation(analyst, tenant_id="tenant-alpha")
    with pytest.raises(TenantDenied):
        tenants.invite_member(
            _identity("beta-admin", "beta-current"), tenant_id="tenant-alpha",
            issuer=ISSUER, subject="other", role="administrator",
            expires_at=int(time.time()) + 120,
        )


def test_organization_proposal_preserves_external_reference_without_authority(tenants):
    proposal = OrganizationProposal(
        organization_name="Synthetic Organization", tenant_id="tenant-gamma",
        external_company_reference="ERP Gamma Company", country="US", timezone="UTC",
        administrator_issuer=ISSUER, administrator_subject="gamma-admin",
        retention_days=30,
    )
    packet = proposal.review_packet()
    assert packet["external_company_reference"] == "ERP Gamma Company"
    assert packet["tenant_id"] == "tenant-gamma"
    assert packet["execution_authority"] is False
    with pytest.raises(TenantDenied):
        tenants.authorize(_identity("gamma-admin", "gamma"), tenant_id="tenant-gamma")
    with pytest.raises(PortalProposalDenied):
        OrganizationProposal(
            organization_name="Synthetic Organization", tenant_id="Tenant-Gamma",
            external_company_reference="ERP Gamma Company", country="US", timezone="UTC",
            administrator_issuer=ISSUER, administrator_subject="gamma-admin",
            retention_days=30,
        )
    with tenants._connection() as db:
        assert db.execute(
            "SELECT company_reference FROM tenant_external_references WHERE tenant_id='tenant-alpha'"
        ).fetchone() == ("ERP Alpha Company",)


def test_authorization_wizard_proposal_is_bounded_and_never_a_grant():
    request = AuthorizationProposal(
        tenant_id="tenant-alpha", companies=("ERP Alpha Company",),
        fields_by_doctype=(("Purchase Order", ("name", "company")),),
        sensitive_exclusions=("payroll", "personal records", "attachments"),
        historical_start=date(2025, 1, 1), historical_end=date(2025, 12, 31),
        max_requests=25, max_duration_seconds=1800, max_evidence_bytes=256 * 1024,
        retention_days=1, revocation_operator="Synthetic Operator",
    )
    packet = request.review_packet()
    assert packet["status"] == "pending_governed_validation"
    assert packet["execution_authority"] is False
    assert len(packet["scope_sha256"]) == 64
    with pytest.raises(PortalProposalDenied):
        AuthorizationProposal(
            tenant_id="tenant-alpha", companies=("ERP Alpha Company",),
            fields_by_doctype=(("Payroll Entry", ("name",)),),
            sensitive_exclusions=("payroll",), historical_start=date(2025, 1, 1),
            historical_end=date(2025, 12, 31), max_requests=26,
            max_duration_seconds=1800, max_evidence_bytes=1024,
            retention_days=1, revocation_operator="Synthetic Operator",
        )


def test_offboarding_erases_pending_portal_scope_and_external_reference(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    signer = Ed25519PrivateKey.generate()
    registry = TenantRegistry(private / "tenant.sqlite", approval_public_key=signer.public_key())
    _bootstrap(registry, signer, "tenant-alpha", "alpha-admin", 2001, "ERP Alpha Company")
    registry.invite_member(
        _identity("alpha-admin", "admin-current"), tenant_id="tenant-alpha",
        issuer=ISSUER, subject="pending", role="analyst",
        expires_at=int(time.time()) + 120,
    )
    with registry._connection() as db:
        db.execute(
            "INSERT INTO authorization_proposals VALUES (?,?,?,?,?,?)",
            ("synthetic-proposal", "tenant-alpha", "0" * 64, "{}", int(time.time()),
             "pending_governed_validation"),
        )
    for action, nonce in (("suspend", 2002), ("offboard", 2003)):
        decision = {
            "approval_id": f"synthetic-lifecycle-{nonce:04d}",
            "tenant_id": "tenant-alpha", "action": action,
            "expires_at": int(time.time()) + 300,
        }
        payload = b"ORION TENANT LIFECYCLE V1\0" + json.dumps(
            decision, sort_keys=True, separators=(",", ":")
        ).encode()
        registry.apply_lifecycle_decision(
            decision=decision, signature=base64.b64encode(signer.sign(payload)).decode(),
        )
    with registry._connection() as db:
        assert db.execute("SELECT COUNT(*) FROM member_invitations").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM authorization_proposals").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM tenant_external_references").fetchone()[0] == 0
