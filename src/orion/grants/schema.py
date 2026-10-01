"""Strict schema validation for offline ORION host-application grants."""

from __future__ import annotations

import re
from collections.abc import Mapping
from typing import Any

REQUIRED = {
    "record_type", "record_version", "grant_id", "issuer", "issued_at", "expires_at",
    "target", "authorization", "limits", "software_binding", "revocation", "audit", "integrity",
}
RECORD_TYPE = "ORION_HOST_APPLICATION_GRANT"
RECORD_VERSION = "1.0"
_SHA256 = re.compile(r"^[0-9a-f]{64}$")


class GrantSchemaError(ValueError):
    """Raised when a grant violates the offline schema."""


def _nonempty(value: Any, field: str) -> None:
    if not isinstance(value, str) or not value:
        raise GrantSchemaError(f"{field} must be a non-empty string")


def validate_grant(grant: Mapping[str, Any]) -> None:
    if not isinstance(grant, Mapping):
        raise GrantSchemaError("grant must be an object")
    missing = REQUIRED - set(grant)
    if missing:
        raise GrantSchemaError(f"missing required fields: {sorted(missing)}")
    if grant["record_type"] != RECORD_TYPE or grant["record_version"] != RECORD_VERSION:
        raise GrantSchemaError("unsupported grant record type/version")
    _nonempty(grant["grant_id"], "grant_id")

    objects = {}
    for name in ("issuer", "target", "authorization", "limits", "software_binding",
                 "revocation", "audit", "integrity"):
        value = grant[name]
        if not isinstance(value, Mapping):
            raise GrantSchemaError(f"{name} must be an object")
        objects[name] = value

    for key in ("authority", "key_id"):
        _nonempty(objects["issuer"].get(key), f"issuer.{key}")

    for key in ("tenant_id", "company", "source_id", "origin"):
        _nonempty(objects["target"].get(key), f"target.{key}")
    if not objects["target"]["origin"].startswith("https://"):
        raise GrantSchemaError("target.origin must use HTTPS")

    auth = objects["authorization"]
    if auth.get("mode") != "read_only":
        raise GrantSchemaError("authorization.mode must be read_only")
    if auth.get("operations") != ["metadata_discovery"]:
        raise GrantSchemaError("authorization.operations must be exactly metadata_discovery")
    for key in ("writes", "record_access", "execution", "autonomous_permission"):
        if auth.get(key) is not False:
            raise GrantSchemaError(f"authorization.{key} must be false")

    limits = objects["limits"]
    for key in ("max_requests", "max_response_bytes", "rate_limit"):
        value = limits.get(key)
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise GrantSchemaError(f"limits.{key} must be a positive integer")

    software = objects["software_binding"]
    for key in ("repository", "release_ref", "grant_policy_version"):
        _nonempty(software.get(key), f"software_binding.{key}")
    _nonempty(software.get("release_tree_sha256"), "software_binding.release_tree_sha256")
    if not _SHA256.fullmatch(software["release_tree_sha256"]):
        raise GrantSchemaError("software_binding.release_tree_sha256 must be a SHA-256 hex digest")

    _nonempty(objects["revocation"].get("authority"), "revocation.authority")
    _nonempty(objects["revocation"].get("reference"), "revocation.reference")
    _nonempty(objects["audit"].get("audit_reference"), "audit.audit_reference")

    integrity = objects["integrity"]
    if integrity.get("canonicalization") != "orion-json-v1":
        raise GrantSchemaError("integrity.canonicalization must be orion-json-v1")
    if integrity.get("signature_algorithm") != "Ed25519":
        raise GrantSchemaError("integrity.signature_algorithm must be Ed25519")
    digest = integrity.get("payload_sha256")
    if not isinstance(digest, str) or not _SHA256.fullmatch(digest):
        raise GrantSchemaError("integrity.payload_sha256 must be a SHA-256 hex digest")
    _nonempty(integrity.get("signature"), "integrity.signature")

    for field in ("issued_at", "expires_at"):
        _nonempty(grant[field], field)
