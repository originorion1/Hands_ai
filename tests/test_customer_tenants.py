"""Synthetic tenant bootstrap and membership remain isolated and revocable."""

import base64
import json
import sqlite3
import time
from concurrent.futures import ThreadPoolExecutor

import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from deployment.customer.backup import backup_synthetic_tenant, restore_synthetic_tenant
from deployment.customer.oidc import VerifiedIdentity
from deployment.customer.tenants import TenantDenied, TenantRegistry
from orion_connector_sdk import CONTRACT_VERSION, ConnectorRegistration


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


def _lifecycle(private, tenant, action, nonce):
    decision = {
        "approval_id": f"synthetic-lifecycle-{nonce:04d}",
        "tenant_id": tenant,
        "action": action,
        "expires_at": int(time.time()) + 300,
    }
    message = b"ORION TENANT LIFECYCLE V1\0" + json.dumps(
        decision, sort_keys=True, separators=(",", ":")
    ).encode()
    return decision, base64.b64encode(private.sign(message)).decode()


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


def test_registry_denies_clock_rollback_before_tenant_access(registry, monkeypatch):
    store, signer = registry
    admin = _identity("alpha-admin", "session")
    approval = _approval("tenant-alpha", admin.subject, 1011)
    store.provision(admin, approval=approval, signature=_signed(signer, approval))
    observed = int(time.time())
    with monkeypatch.context() as patch:
        patch.setattr("deployment.customer.tenants.time.time", lambda: observed - 120)
        with pytest.raises(TenantDenied, match="clock rollback"):
            store.authorize(admin, tenant_id="tenant-alpha")
    assert store.authorize(admin, tenant_id="tenant-alpha").role == "administrator"


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


def test_tenant_emergency_stop_reactivation_and_offboarding(registry):
    store, signer = registry
    alpha = _identity("alpha-admin", "alpha-session")
    beta = _identity("beta-admin", "beta-session")
    for tenant, admin, nonce in (
        ("tenant-alpha", alpha, 2001),
        ("tenant-beta", beta, 2002),
    ):
        approval = _approval(tenant, admin.subject, nonce)
        store.provision(admin, approval=approval, signature=_signed(signer, approval))
    decision, signature = _lifecycle(signer, "tenant-alpha", "suspend", 2003)
    assert store.apply_lifecycle_decision(decision=decision, signature=signature) == "suspended"
    with pytest.raises(TenantDenied):
        store.authorize(alpha, tenant_id="tenant-alpha")
    assert store.authorize(beta, tenant_id="tenant-beta").role == "administrator"
    with pytest.raises(TenantDenied, match="already used"):
        store.apply_lifecycle_decision(decision=decision, signature=signature)
    decision, signature = _lifecycle(signer, "tenant-alpha", "reactivate", 2004)
    assert store.apply_lifecycle_decision(decision=decision, signature=signature) == "active"
    assert store.authorize(alpha, tenant_id="tenant-alpha").role == "administrator"
    for action, nonce in (("suspend", 2005), ("offboard", 2006)):
        decision, signature = _lifecycle(signer, "tenant-alpha", action, nonce)
        store.apply_lifecycle_decision(decision=decision, signature=signature)
    with pytest.raises(TenantDenied):
        store.authorize(alpha, tenant_id="tenant-alpha")
    decision, signature = _lifecycle(signer, "tenant-alpha", "reactivate", 2007)
    with pytest.raises(TenantDenied, match="transition unavailable"):
        store.apply_lifecycle_decision(decision=decision, signature=signature)


def test_lifecycle_tamper_and_wrong_tenant_denied(registry):
    store, signer = registry
    admin = _identity("alpha-admin", "session")
    approval = _approval("tenant-alpha", admin.subject, 2010)
    store.provision(admin, approval=approval, signature=_signed(signer, approval))
    decision, signature = _lifecycle(signer, "tenant-alpha", "suspend", 2011)
    with pytest.raises(TenantDenied, match="signature invalid"):
        store.apply_lifecycle_decision(
            decision={**decision, "tenant_id": "tenant-beta"}, signature=signature
        )
    with pytest.raises(TenantDenied, match="transition unavailable"):
        wrong, wrong_signature = _lifecycle(signer, "tenant-beta", "suspend", 2012)
        store.apply_lifecycle_decision(decision=wrong, signature=wrong_signature)
    assert store.authorize(admin, tenant_id="tenant-alpha").role == "administrator"


def test_credential_references_rotate_revoke_and_remain_tenant_scoped(registry):
    store, signer = registry
    alpha = _identity("alpha-admin", "alpha-session")
    beta = _identity("beta-admin", "beta-session")
    for tenant, admin, nonce in (
        ("tenant-alpha", alpha, 2020),
        ("tenant-beta", beta, 2021),
    ):
        approval = _approval(tenant, admin.subject, nonce)
        store.provision(admin, approval=approval, signature=_signed(signer, approval))
    connector = ConnectorRegistration(
        "synthetic", "originorion-connector-synthetic", "0.2.0rc1",
        "orion_connector_synthetic:SyntheticConnector", CONTRACT_VERSION,
    )
    first = "vault:" + "a" * 32
    second = "vault:" + "b" * 32
    store.enroll_credential_reference(
        alpha, tenant_id="tenant-alpha", connector=connector,
        label="Main", credential_ref=first,
    )
    with pytest.raises(TenantDenied):
        store.rotate_credential_reference(
            beta, tenant_id="tenant-alpha", label="Main", new_credential_ref=second
        )
    with pytest.raises(TenantDenied):
        store.enroll_credential_reference(
            alpha, tenant_id="tenant-beta", connector=connector,
            label="Other", credential_ref=second,
        )
    retired = store.rotate_credential_reference(
        alpha, tenant_id="tenant-alpha", label="Main", new_credential_ref=second
    )
    assert retired == first
    with sqlite3.connect(store._path) as db:
        rows = db.execute(
            "SELECT tenant_id,credential_ref,generation,status FROM credential_refs"
        ).fetchall()
    assert rows == [("tenant-alpha", second, 2, "active")]
    assert store.revoke_credential_reference(
        alpha, tenant_id="tenant-alpha", label="Main"
    ) == second
    with sqlite3.connect(store._path) as db:
        retired_refs = db.execute(
            "SELECT credential_ref,generation FROM retired_credential_refs ORDER BY generation"
        ).fetchall()
    assert retired_refs == [(first, 1), (second, 2)]
    with pytest.raises(TenantDenied):
        store.rotate_credential_reference(
            alpha, tenant_id="tenant-alpha", label="Main", new_credential_ref=first
        )


def test_synthetic_evidence_retention_and_cross_tenant_custody(registry):
    store, signer = registry
    alpha = _identity("alpha-admin", "alpha-session")
    beta = _identity("beta-admin", "beta-session")
    for tenant, admin, nonce in (
        ("tenant-alpha", alpha, 2030),
        ("tenant-beta", beta, 2031),
    ):
        approval = _approval(tenant, admin.subject, nonce)
        store.provision(admin, approval=approval, signature=_signed(signer, approval))
    payload = b'{"synthetic":true,"finding":"inventory difference"}'
    digest = store.put_synthetic_evidence(
        alpha, tenant_id="tenant-alpha", evidence_id="evidence-alpha-001", payload=payload
    )
    assert store.get_synthetic_evidence(
        alpha, tenant_id="tenant-alpha", evidence_id="evidence-alpha-001"
    ) == payload
    with pytest.raises(TenantDenied):
        store.get_synthetic_evidence(
            beta, tenant_id="tenant-alpha", evidence_id="evidence-alpha-001"
        )
    with pytest.raises(TenantDenied, match="synthetic evidence marker"):
        store.put_synthetic_evidence(
            alpha, tenant_id="tenant-alpha", evidence_id="evidence-alpha-002",
            payload=b'{"synthetic":false}',
        )
    with pytest.raises(TenantDenied, match="invalid synthetic evidence"):
        store.put_synthetic_evidence(
            alpha, tenant_id="tenant-alpha", evidence_id="evidence-alpha-002",
            payload=b'{"synthetic":false,"synthetic":true}',
        )
    with sqlite3.connect(store._path) as db:
        db.execute(
            "UPDATE synthetic_evidence SET created_at=? WHERE tenant_id=?",
            (int(time.time()) - 31 * 86400, "tenant-alpha"),
        )
    assert store.purge_expired_synthetic_evidence(tenant_id="tenant-alpha") == 1
    with pytest.raises(TenantDenied):
        store.get_synthetic_evidence(
            alpha, tenant_id="tenant-alpha", evidence_id="evidence-alpha-001"
        )
    with sqlite3.connect(store._path) as db:
        audit = db.execute(
            "SELECT digest,event FROM evidence_audit WHERE tenant_id='tenant-alpha'"
        ).fetchall()
    assert audit == [(digest, "retention_deleted")]


def test_offboarding_purges_synthetic_payload_but_preserves_audit(registry):
    store, signer = registry
    admin = _identity("alpha-admin", "session")
    approval = _approval("tenant-alpha", admin.subject, 2040)
    store.provision(admin, approval=approval, signature=_signed(signer, approval))
    payload = b'{"synthetic":true,"finding":"sample"}'
    digest = store.put_synthetic_evidence(
        admin, tenant_id="tenant-alpha", evidence_id="evidence-alpha-003", payload=payload
    )
    for action, nonce in (("suspend", 2041), ("offboard", 2042)):
        decision, signature = _lifecycle(signer, "tenant-alpha", action, nonce)
        store.apply_lifecycle_decision(decision=decision, signature=signature)
    with sqlite3.connect(store._path) as db:
        assert db.execute("SELECT COUNT(*) FROM synthetic_evidence").fetchone()[0] == 0
        assert db.execute("SELECT digest FROM evidence_audit").fetchone()[0] == digest
        assert db.execute("SELECT active FROM members").fetchone()[0] == 0


def test_encrypted_tenant_backup_restore_and_wrong_tenant_denial(registry):
    store, signer = registry
    alpha = _identity("alpha-admin", "alpha-session")
    beta = _identity("beta-admin", "beta-session")
    for tenant, admin, nonce in (
        ("tenant-alpha", alpha, 2050),
        ("tenant-beta", beta, 2051),
    ):
        approval = _approval(tenant, admin.subject, nonce)
        store.provision(admin, approval=approval, signature=_signed(signer, approval))
    payload = b'{"synthetic":true,"finding":"backup sample"}'
    store.put_synthetic_evidence(
        alpha, tenant_id="tenant-alpha", evidence_id="evidence-alpha-004", payload=payload
    )
    key = Fernet.generate_key()
    encrypted = backup_synthetic_tenant(
        store, alpha, tenant_id="tenant-alpha", encryption_key=key
    )
    assert payload not in encrypted
    with pytest.raises(TenantDenied, match="tenant mismatch"):
        restore_synthetic_tenant(
            store, beta, tenant_id="tenant-beta", encrypted_backup=encrypted, encryption_key=key
        )
    with pytest.raises(TenantDenied, match="authentication failed"):
        restore_synthetic_tenant(
            store, alpha, tenant_id="tenant-alpha", encrypted_backup=encrypted,
            encryption_key=Fernet.generate_key(),
        )
    tampered = bytearray(encrypted)
    tampered[len(tampered) // 2] = ord("A") if tampered[len(tampered) // 2] != ord("A") else ord("B")
    with pytest.raises(TenantDenied, match="authentication failed"):
        restore_synthetic_tenant(
            store, alpha, tenant_id="tenant-alpha", encrypted_backup=bytes(tampered),
            encryption_key=key,
        )
    with sqlite3.connect(store._path) as db:
        db.execute("DELETE FROM synthetic_evidence WHERE tenant_id='tenant-alpha'")
    assert restore_synthetic_tenant(
        store, alpha, tenant_id="tenant-alpha", encrypted_backup=encrypted, encryption_key=key
    ) == 1
    assert store.get_synthetic_evidence(
        alpha, tenant_id="tenant-alpha", evidence_id="evidence-alpha-004"
    ) == payload
    with pytest.raises(TenantDenied):
        restore_synthetic_tenant(
            store, alpha, tenant_id="tenant-alpha", encrypted_backup=encrypted,
            encryption_key=key,
        )
