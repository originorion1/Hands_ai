import base64
from datetime import UTC, datetime, timedelta

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from orion.grants.canonical import canonical_bytes, grant_digest, payload_without_integrity
from orion.grants.schema import GrantSchemaError, validate_grant
from orion.grants.verification import (
    GrantExpiredError,
    GrantScopeError,
    GrantSignatureError,
    verify_grant,
)


def make_grant(private_key):
    issued = datetime(2026, 10, 1, 10, 0, tzinfo=UTC)
    expires = issued + timedelta(hours=1)
    grant = {
        "record_type": "ORION_HOST_APPLICATION_GRANT",
        "record_version": "1.0",
        "grant_id": "grant-test-001",
        "issuer": {"authority": "offline-control-plane", "key_id": "controller-ed25519-v1"},
        "issued_at": issued.isoformat().replace("+00:00", "Z"),
        "expires_at": expires.isoformat().replace("+00:00", "Z"),
        "target": {
            "tenant_id": "tenant-test",
            "company": "Example Co",
            "source_id": "erp-001",
            "origin": "https://example.invalid/api",
        },
        "authorization": {
            "mode": "read_only",
            "operations": ["metadata_discovery"],
            "writes": False,
            "record_access": False,
            "execution": False,
            "autonomous_permission": False,
        },
        "limits": {"max_requests": 10, "max_response_bytes": 10000, "rate_limit": 2},
        "software_binding": {
            "repository": "originorion1/Hands_ai",
            "release_ref": "laboratory/orion-v0.1",
            "release_tree_sha256": "a" * 64,
            "grant_policy_version": "1",
        },
        "revocation": {"authority": "offline-control-plane", "reference": "revocations-v1"},
        "audit": {"audit_reference": "journal-001"},
        "integrity": {
            "canonicalization": "orion-json-v1",
            "signature_algorithm": "Ed25519",
            "payload_sha256": "",
            "signature": "",
        },
    }
    grant["integrity"]["payload_sha256"] = grant_digest(grant)
    signature = private_key.sign(canonical_bytes(payload_without_integrity(grant)))
    grant["integrity"]["signature"] = base64.b64encode(signature).decode("ascii")
    return grant


def test_canonicalization_is_stable():
    a = {"b": 2, "a": {"z": 1, "y": True}}
    b = {"a": {"y": True, "z": 1}, "b": 2}
    assert canonical_bytes(a) == canonical_bytes(b)


def test_floats_are_rejected():
    with pytest.raises(ValueError):
        canonical_bytes({"x": 1.5})


def test_valid_signed_grant_verifies():
    private = Ed25519PrivateKey.generate()
    grant = make_grant(private)
    digest = verify_grant(
        grant,
        private.public_key().public_bytes_raw(),
        now=datetime(2026, 10, 1, 10, 30, tzinfo=UTC),
        expected_target={"tenant_id": "tenant-test"},
        expected_release_tree_sha256="a" * 64,
    )
    assert digest == grant["integrity"]["payload_sha256"]


def test_tampering_is_rejected():
    private = Ed25519PrivateKey.generate()
    grant = make_grant(private)
    grant["target"]["company"] = "Changed Test Company"
    grant["integrity"]["payload_sha256"] = grant_digest(grant)
    with pytest.raises(GrantSignatureError, match="invalid Ed25519 signature"):
        verify_grant(grant, private.public_key().public_bytes_raw())


def test_expired_grant_is_rejected():
    private = Ed25519PrivateKey.generate()
    grant = make_grant(private)
    with pytest.raises(GrantExpiredError):
        verify_grant(
            grant,
            private.public_key().public_bytes_raw(),
            now=datetime(2026, 10, 1, 12, 0, 1, tzinfo=UTC),
        )


def test_scope_is_bound():
    private = Ed25519PrivateKey.generate()
    grant = make_grant(private)
    with pytest.raises(GrantScopeError):
        verify_grant(
            grant,
            private.public_key().public_bytes_raw(),
            now=datetime(2026, 10, 1, 10, 30, tzinfo=UTC),
            expected_target={"source_id": "wrong"},
        )


def test_schema_rejects_write_authority():
    private = Ed25519PrivateKey.generate()
    grant = make_grant(private)
    grant["authorization"]["writes"] = True
    with pytest.raises(GrantSchemaError):
        validate_grant(grant)


def test_verifier_requires_only_public_key():
    private = Ed25519PrivateKey.generate()
    grant = make_grant(private)
    assert verify_grant(
        grant,
        private.public_key().public_bytes_raw(),
        now=datetime(2026, 10, 1, 10, 30, tzinfo=UTC),
    )
