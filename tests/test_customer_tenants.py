"""Synthetic tenant bootstrap and membership remain isolated and revocable."""

import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from deployment.customer.oidc import VerifiedIdentity
from deployment.customer.tenants import TenantDenied, TenantRegistry


def _identity(subject, session):
    return VerifiedIdentity(
        "https://idp.example.invalid/tenant", subject, session, int(time.time()) + 300
    )


def _approval(tenant, subject, nonce):
    return {
        "approval_id": f"synthetic-approval-{nonce:04d}",
        "tenant_id": tenant,
        "organization_name": f"Synthetic {tenant}",
        "country": "US",
        "timezone": "UTC",
        "sector": "hospitality",
        "data_residency": "approved-region",
        "retention_days": 30,
        "admin_issuer": "https://idp.example.invalid/tenant",
        "admin_subject": subject,
        "expires_at": int(time.time()) + 300,
    }


def _signed(private, approval):
    message = b"ORION CUSTOMER BOOTSTRAP V1\0" + json.dumps(
        approval, sort_keys=True, separators=(",", ":")
    ).encode()
    return base64.b64encode(private.sign(message)).decode()


@pytest.fixture
def registry(tmp_path):
    private = Ed25519PrivateKey.generate()
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    return TenantRegistry(directory / "tenants.sqlite", approval_public_key=private.public_key()), private


def test_two_tenants_and_roles_never_cross(registry):
    store, signer = registry
    alpha_admin = _identity("alpha-admin", "alpha-session")
    beta_admin = _identity("beta-admin", "beta-session")
    for tenant, admin, nonce in (
        ("tenant-alpha", alpha_admin, 1001),
        ("tenant-beta", beta_admin, 1002),
    ):
        approval = _approval(tenant, admin.subject, nonce)
        assert store.provision(admin, approval=approval, signature=_signed(signer, approval)) == tenant
        assert store.authorize(admin, tenant_id=tenant, required_role="administrator").role == "administrator"
    with pytest.raises(TenantDenied):
        store.authorize(alpha_admin, tenant_id="tenant-beta")
    analyst = _identity("alpha-analyst", "analyst-session")
    store.add_member(alpha_admin, tenant_id="tenant-alpha", member=analyst, role="analyst")
    assert store.authorize(analyst, tenant_id="tenant-alpha").role == "analyst"
    with pytest.raises(TenantDenied):
        store.authorize(analyst, tenant_id="tenant-alpha", required_role="administrator")
    with pytest.raises(TenantDenied):
        store.authorize(analyst, tenant_id="tenant-beta")
    with pytest.raises(TenantDenied):
        store.add_member(analyst, tenant_id="tenant-alpha", member=beta_admin, role="administrator")


def test_forged_replayed_or_mismatched_bootstrap_denied(registry):
    store, signer = registry
    admin = _identity("alpha-admin", "session")
    approval = _approval("tenant-alpha", admin.subject, 1003)
    signature = _signed(signer, approval)
    wrong_purpose = base64.b64encode(
        signer.sign(json.dumps(approval, sort_keys=True, separators=(",", ":")).encode())
    ).decode()
    with pytest.raises(TenantDenied, match="signature invalid"):
        store.provision(admin, approval=approval, signature=wrong_purpose)
    with pytest.raises(TenantDenied, match="signature invalid"):
        store.provision(admin, approval={**approval, "retention_days": 300}, signature=signature)
    other = _identity("different-user", "other-session")
    with pytest.raises(TenantDenied, match="mismatched"):
        store.provision(other, approval=approval, signature=signature)
    store.provision(admin, approval=approval, signature=signature)
    with pytest.raises(TenantDenied, match="already used"):
        store.provision(admin, approval=approval, signature=signature)


def test_logout_and_expiry_deny_membership(registry):
    store, signer = registry
    admin = _identity("alpha-admin", "session")
    approval = _approval("tenant-alpha", admin.subject, 1004)
    store.provision(admin, approval=approval, signature=_signed(signer, approval))
    store.logout(admin)
    with pytest.raises(TenantDenied):
        store.authorize(admin, tenant_id="tenant-alpha")
    with pytest.raises(TenantDenied):
        store.add_member(admin, tenant_id="tenant-alpha", member=_identity("x", "x"), role="analyst")
    expired = VerifiedIdentity(admin.issuer, admin.subject, "new-session", 1)
    with pytest.raises(TenantDenied):
        store.authorize(expired, tenant_id="tenant-alpha")


def test_concurrent_bootstrap_consumes_approval_once(registry):
    store, signer = registry
    admin = _identity("alpha-admin", "session")
    approval = _approval("tenant-alpha", admin.subject, 1005)
    signature = _signed(signer, approval)
    with ThreadPoolExecutor(max_workers=2) as pool:
        attempts = list(pool.map(lambda _: _try_provision(store, admin, approval, signature), range(2)))
    assert sorted(attempts) == ["denied", "provisioned"]


def _try_provision(store, admin, approval, signature):
    try:
        store.provision(admin, approval=approval, signature=signature)
        return "provisioned"
    except TenantDenied:
        return "denied"
