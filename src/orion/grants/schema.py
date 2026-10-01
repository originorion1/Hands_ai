"""Strict schema validation for offline ORION host-application grants."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

REQUIRED = {
    "record_type",
    "record_version",
    "grant_id",
    "issuer",
    "issued_at",
    "expires_at",
    "target",
    "authorization",
    "limits",
    "software_binding",
    "revocation",
    "audit",
    "integrity",
}

RECORD_TYPE = "ORION_HOST_APPLICATION_GRANT"
RECORD_VERSION = "1.0"


class GrantSchemaError(ValueError):
    """Raised when a grant violates the offline schema."""


def validate_grant(grant: Mapping[str, Any]) -> None:
    if not isinstance(grant, Mapping):
        raise GrantSchemaError("grant must be an object")
    missing = REQUIRED - set(grant)
    if missing:
        raise GrantSchemaError(f"missing required fields: {sorted(missing)}")
    if grant["record_type"] != RECORD_TYPE or grant["record_version"] != RECORD_VERSION:
        raise GrantSchemaError("unsupported grant record type/version")
    if not isinstance(grant["grant_id"], str) or not grant["grant_id"]:
        raise GrantSchemaError("grant_id must be a non-empty string")

    issuer = grant["issuer"]
    target = grant["target"]
    auth = grant["authorization"]
    limits = grant["limits"]
    software = grant["software_binding"]
    integrity = grant["integrity"]
    for name, value in (("issuer", issuer), ("target", target), ("authorization", auth),
                        ("limits", limits), ("software_binding", software),
                        ("integrity", integrity)):
        if not isinstance(value, Mapping):
            raise GrantSchemaError(f"{name} must be an object")

    for key in ("authority", "key_id"):
        if not isinstance(issuer.get(key), str) or not issuer[key]:
            raise GrantSchemaError(f"issuer.{key} must be a non-empty string")

    for key in ("tenant_id", "company", "source_id", "origin"):
        if not isinstance(target.get(key), str) or not target[key]:
            raise GrantSchemaError(f"target.{key} must be a non-empty string")

    if auth.get("mode") != "read_only":
        raise GrantSchemaError("authorization.mode must be read_only")
    if auth.get("operations") != ["metadata_discovery"]:
        raise GrantSchemaError("authorization.operations must be exactly metadata_discovery")
    for key in ("writes", "record_access", "execution", "autonomous_permission"):
        if auth.get(key) is not False:
            raise GrantSchemaError(f"authorization.{key} must be false")

    for key in ("max_requests", "max_response_bytes", "rate_limit"):
        if not isinstance(limits.get(key), int) or isinstance(limits[key], bool) or limits[key] < 0:
            raise GrantSchemaError(f"limits.{key} must be a non-negative integer")

    for key in ("repository", "release_ref", "release_tree_sha256", "grant_policy_version"):
        if not isinstance(software.get(key), str) or not software[key]:
            raise GrantSchemaError(f"software_binding.{key} must be a non-empty string")

    if not isinstance(integrity.get("canonicalization"), str) or integrity["canonicalization"] != "orion-json-v1":
        raise GrantSchemaError("integrity.canonicalization must be orion-json-v1")
    if not isinstance(integrity.get("signature_algorithm"), str) or integrity["signature_algorithm"] != "Ed25519":
        raise GrantSchemaError("integrity.signature_algorithm must be Ed25519")
    if not isinstance(integrity.get("payload_sha256"), str) or len(integrity["payload_sha256"]) != 64:
        raise GrantSchemaError("integrity.payload_sha256 must be a SHA-256 hex digest")
    if not isinstance(integrity.get("signature"), str) or not integrity["signature"]:
        raise GrantSchemaError("integrity.signature must be a non-empty base64 string")

    for field in ("issued_at", "expires_at"):
        if not isinstance(grant[field], str) or not grant[field]:
            raise GrantSchemaError(f"{field} must be an RFC3339 timestamp")
