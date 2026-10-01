"""Offline verification of signed ORION host-application grants.

This module verifies grants only. It never creates signatures and never requires
or accepts a controller private key.
"""

from __future__ import annotations

import base64
import binascii
from datetime import UTC, datetime
from typing import Any

from .canonical import canonical_bytes, grant_digest, payload_without_integrity
from .schema import GrantSchemaError, validate_grant

try:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey
except ImportError:  # pragma: no cover - exercised only in environments without dependency
    InvalidSignature = None
    Ed25519PublicKey = None


class GrantError(ValueError):
    """Base class for invalid grants."""


class GrantSignatureError(GrantError):
    """Raised when a grant signature cannot be verified."""


class GrantExpiredError(GrantError):
    """Raised when the grant is not currently valid."""


class GrantScopeError(GrantError):
    """Raised when a grant does not match the expected target/release."""


def _timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise GrantError(f"invalid RFC3339 timestamp: {value!r}") from exc
    if parsed.tzinfo is None:
        raise GrantError("timestamps must include a timezone")
    return parsed.astimezone(UTC)


def _b64(value: str, field: str) -> bytes:
    try:
        return base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error) as exc:
        raise GrantSignatureError(f"{field} is not valid base64") from exc


def verify_grant(
    grant: dict[str, Any],
    public_key: bytes,
    *,
    now: datetime | None = None,
    expected_target: dict[str, str] | None = None,
    expected_release_tree_sha256: str | None = None,
) -> str:
    """Validate and cryptographically verify a grant.

    Returns the verified payload SHA-256 digest. The public key is the only key
    material accepted by this function; a controller/private signing key is never
    needed.
    """
    try:
        validate_grant(grant)
    except GrantSchemaError as exc:
        raise GrantError(str(exc)) from exc

    calculated = grant_digest(grant)
    if grant["integrity"]["payload_sha256"] != calculated:
        raise GrantSignatureError("payload_sha256 does not match canonical grant payload")

    if Ed25519PublicKey is None:
        raise GrantSignatureError("cryptography package with Ed25519 support is required")

    signature = _b64(grant["integrity"]["signature"], "integrity.signature")
    try:
        key = Ed25519PublicKey.from_public_bytes(public_key)
        key.verify(signature, canonical_bytes(payload_without_integrity(grant)))
    except (ValueError, InvalidSignature) as exc:
        raise GrantSignatureError("invalid Ed25519 signature") from exc

    current = (now or datetime.now(UTC)).astimezone(UTC)
    if current < _timestamp(grant["issued_at"]) or current >= _timestamp(grant["expires_at"]):
        raise GrantExpiredError("grant is outside its validity interval")

    if expected_target:
        target = grant["target"]
        for key, value in expected_target.items():
            if target.get(key) != value:
                raise GrantScopeError(f"target mismatch for {key}")
    if (
        expected_release_tree_sha256 is not None
        and grant["software_binding"]["release_tree_sha256"] != expected_release_tree_sha256
    ):
        raise GrantScopeError("release tree digest mismatch")

    return calculated
