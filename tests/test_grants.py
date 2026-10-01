from datetime import UTC, datetime, timedelta
import base64

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from orion.grants.canonical import canonical_bytes, grant_digest, payload_without_integrity
from orion.grants.schema import GrantSchemaError, validate_grant
from orion.grants.verification import GrantExpiredError, GrantScopeError, GrantSignatureError, verify_grant

NOW = datetime(2026, 10, 1, 14, 0, tzinfo=UTC)


def make_grant():
    grant = {
        "record_type": "ORION_HOST_APPLICATION_GRANT",
        "record_version": "1.0",
        "grant_id": "grant-test-001",
        "issuer": {"authority": "orion-control-plane", "key_id": "controller-ed25519-01"},
        "issued_at": NOW.isoformat().replace("+00:00", "Z"),
        "expires_at": (NOW + timedelta(hours=1)).isoformat().replace("+00:00", "Z"),
        "target": {
            "tenant_id": "tenant-test",
            "company": "Example Co",
            "source_id": "erp-metadata",
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
        "limits": {"max_requests": 100, "max_response_bytes": 1000000, "rate_limit": 2},
        "software_binding": {
            "repository": "originorion1/Hands_ai",
            "release_ref": "laboratory/orion-v0.1",
            "release_tree_sha256": "a" * 64,
            "grant_policy_version": "1",
        },
        "revocation": {"authority": "orion-control-plane", "reference": "revocations/tenant-test"},
        "audit": {"audit_reference": "grant-audit/grant-test-001"},
        "integrity": {
            "canonicalization": "orion-json-v1",
            "signature_algorithm": "Ed25519",
        },
    }
    grant["integrity"]["payload_sha256"] = grant_digest(grant)
    return grant


def signed_grant():
    private = Ed25519PrivateKey.generate()
    grant = make_grant()
    grant["integrity"]["signature"] = base64.b64encode(
        private.sign(canonical_bytes(payload_without_integrity(grant)))
    ).decode("ascii")
    return grant, private.public_key().public_bytes_raw()


def test_canonicalization_is_key_order_independent():
    assert canonical_bytes({"b": 2, "a": {"z": 1, "y": [3, 2]}}) == canonical_bytes(
        {"a": {"y": [3, 2], "z": 1}, "b": 2}
    )


def test_floats_are_rejected_from_signed_records():
    with pytest.raises(ValueError, match="floating-point"):
        canonical_bytes({"rate": 1.5})


def test_valid_grant_verifies_with_public_key_only():
    grant, public_key = signed_grant()
    assert verify_grant(grant, public_key, now=NOW, expected_release_tree_sha256="a" * 64)


def test_payload_tampering_is_rejected():
    grant, public_key = signed_grant()
    grant["target"]["company"] = "Tampered Co"
    with pytest.raises(GrantSignatureError, match="payload_sha256"):
        verify_grant(grant, public_key, now=NOW, expected_release_tree_sha256="a" * 64)


def test_signature_tampering_is_rejected():
    grant, public_key = signed_grant()
    grant["integrity"]["signature"] = base64.b64encode(b"x" * 64).decode("ascii")
    with pytest.raises(GrantSignatureError, match="invalid Ed25519 signature"):
        verify_grant(grant, public_key, now=NOW, expected_release_tree_sha256="a" * 64)


def test_expired_grant_is_rejected():
    grant, public_key = signed_grant()
    with pytest.raises(GrantExpiredError):
        verify_grant(grant, public_key, now=NOW + timedelta(hours=1), expected_release_tree_sha256="a" * 64)


def test_wrong_release_binding_is_rejected():
    grant, public_key = signed_grant()
    with pytest.raises(GrantScopeError, match="release tree"):
        verify_grant(grant, public_key, now=NOW, expected_release_tree_sha256="b" * 64)


def test_write_capability_is_rejected():
    grant, _ = signed_grant()
    grant["authorization"]["writes"] = True
    with pytest.raises(GrantSchemaError, match="writes"):
        validate_grant(grant)


def test_record_access_and_execution_are_rejected():
    grant, _ = signed_grant()
    grant["authorization"]["record_access"] = True
    with pytest.raises(GrantSchemaError, match="record_access"):
        validate_grant(grant)


def test_https_origin_is_required_by_schema_boundary():
    grant, _ = signed_grant()
    grant["target"]["origin"] = "http://example.invalid/api"
    with pytest.raises(GrantSchemaError, match="origin"):
        validate_grant(grant)
