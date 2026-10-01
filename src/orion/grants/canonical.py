"""Deterministic canonicalization for signed ORION grants."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from typing import Any


class CanonicalizationError(ValueError):
    """Raised when a value cannot be represented deterministically."""


def _normalize(value: Any) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        raise CanonicalizationError("floating-point values are forbidden in grant records")
    if isinstance(value, Mapping):
        if any(not isinstance(k, str) for k in value):
            raise CanonicalizationError("object keys must be strings")
        return {k: _normalize(value[k]) for k in sorted(value)}
    if isinstance(value, (list, tuple)):
        return [_normalize(item) for item in value]
    raise CanonicalizationError(f"unsupported JSON value type: {type(value).__name__}")


def canonical_bytes(value: Mapping[str, Any]) -> bytes:
    normalized = _normalize(value)
    if not isinstance(normalized, dict):
        raise CanonicalizationError("root value must be an object")
    return json.dumps(normalized, ensure_ascii=False, separators=(",", ":"), sort_keys=True).encode(
        "utf-8"
    )


def payload_without_integrity(grant: Mapping[str, Any]) -> dict[str, Any]:
    payload = dict(grant)
    integrity = dict(payload.get("integrity", {}))
    for key in ("payload_sha256", "signature"):
        integrity.pop(key, None)
    payload["integrity"] = integrity
    return payload


def grant_digest(grant: Mapping[str, Any]) -> str:
    return hashlib.sha256(canonical_bytes(payload_without_integrity(grant))).hexdigest()
