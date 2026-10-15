"""Verify independently issued, short-lived report access tokens.

ORION cannot issue these tokens. An owner-managed identity service signs them,
and a private host policy binds allowed subjects to one report tenant.
"""

from __future__ import annotations

import base64
import binascii
import json
import os
import re
import stat
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from ..stores.private_files import PrivateFileError, read_private_file

_B64URL = re.compile(r"[A-Za-z0-9_-]+\Z")
_POLICY_KEYS = frozenset(
    {"policy_version", "issuer", "audience", "tenant_id", "allowed_subjects", "ed25519_public_key"}
)
_CLAIM_KEYS = frozenset({"iss", "aud", "sub", "tenant_id", "scope", "iat", "nbf", "exp"})
_MAX_TOKEN_BYTES = 4096
_MAX_TOKEN_SECONDS = 900
_CLOCK_SKEW_SECONDS = 30


class ReportAuthenticationError(ValueError):
    """The caller has no valid report access; details are intentionally private."""


def _unique_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReportAuthenticationError("report access denied")
        result[key] = value
    return result


def _decode_segment(value: str) -> bytes:
    if not value or _B64URL.fullmatch(value) is None:
        raise ReportAuthenticationError("report access denied")
    try:
        decoded = base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
    except (ValueError, binascii.Error) as exc:
        raise ReportAuthenticationError("report access denied") from exc
    if base64.urlsafe_b64encode(decoded).rstrip(b"=").decode("ascii") != value:
        raise ReportAuthenticationError("report access denied")
    return decoded


def _json_object(body: bytes) -> dict[str, Any]:
    try:
        value = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ReportAuthenticationError("report access denied") from exc
    if type(value) is not dict:
        raise ReportAuthenticationError("report access denied")
    return value


@dataclass(frozen=True, slots=True)
class ReportAccessPolicy:
    issuer: str
    audience: str
    tenant_id: str
    allowed_subjects: frozenset[str]
    public_key: Ed25519PublicKey

    @classmethod
    def from_private_file(cls, path: Path) -> ReportAccessPolicy:
        if not path.is_absolute() or ".." in path.parts:
            raise ReportAuthenticationError("report access policy unavailable")
        try:
            current = Path(path.anchor)
            for part in path.parts[1:]:
                current /= part
                if stat.S_ISLNK(os.lstat(current).st_mode):
                    raise ReportAuthenticationError("report access policy unavailable")
            payload = _json_object(
                read_private_file(path, label="report access policy", maximum_bytes=16 * 1024)
            )
            if set(payload) != _POLICY_KEYS or payload["policy_version"] != "orion.report-access.v1":
                raise ReportAuthenticationError("report access policy unavailable")
            for name in ("issuer", "audience", "tenant_id"):
                if type(payload[name]) is not str or not payload[name] or len(payload[name]) > 256:
                    raise ReportAuthenticationError("report access policy unavailable")
            subjects = payload["allowed_subjects"]
            if (
                type(subjects) is not list
                or not 0 < len(subjects) <= 128
                or any(type(subject) is not str or not 0 < len(subject) <= 256 for subject in subjects)
                or len(set(subjects)) != len(subjects)
            ):
                raise ReportAuthenticationError("report access policy unavailable")
            key_bytes = _decode_segment(payload["ed25519_public_key"])
            if len(key_bytes) != 32:
                raise ReportAuthenticationError("report access policy unavailable")
            public_key = Ed25519PublicKey.from_public_bytes(key_bytes)
        except (OSError, PrivateFileError, TypeError, ValueError) as exc:
            raise ReportAuthenticationError("report access policy unavailable") from exc
        return cls(
            issuer=payload["issuer"],
            audience=payload["audience"],
            tenant_id=payload["tenant_id"],
            allowed_subjects=frozenset(subjects),
            public_key=public_key,
        )

    def authorize(self, authorization: str | None, *, report_tenant: str, now: int | None = None) -> None:
        if (
            type(authorization) is not str
            or len(authorization) > _MAX_TOKEN_BYTES + 7
            or not authorization.startswith("Bearer ")
            or self.tenant_id != report_tenant
        ):
            raise ReportAuthenticationError("report access denied")
        token = authorization[7:]
        if not token.isascii() or len(token) > _MAX_TOKEN_BYTES:
            raise ReportAuthenticationError("report access denied")
        parts = token.split(".")
        if len(parts) != 3:
            raise ReportAuthenticationError("report access denied")
        header = _json_object(_decode_segment(parts[0]))
        if header != {"alg": "EdDSA", "typ": "JWT"}:
            raise ReportAuthenticationError("report access denied")
        signature = _decode_segment(parts[2])
        try:
            self.public_key.verify(signature, f"{parts[0]}.{parts[1]}".encode("ascii"))
        except InvalidSignature as exc:
            raise ReportAuthenticationError("report access denied") from exc
        claims = _json_object(_decode_segment(parts[1]))
        if set(claims) != _CLAIM_KEYS:
            raise ReportAuthenticationError("report access denied")
        if (
            claims["iss"] != self.issuer
            or claims["aud"] != self.audience
            or claims["tenant_id"] != self.tenant_id
            or claims["scope"] != "orion:report:read"
            or type(claims["sub"]) is not str
            or claims["sub"] not in self.allowed_subjects
            or any(type(claims[name]) is not int for name in ("iat", "nbf", "exp"))
        ):
            raise ReportAuthenticationError("report access denied")
        current = int(time.time()) if now is None else now
        if (
            claims["iat"] > current + _CLOCK_SKEW_SECONDS
            or claims["nbf"] > current + _CLOCK_SKEW_SECONDS
            or claims["exp"] <= current
            or claims["exp"] <= claims["iat"]
            or claims["exp"] <= claims["nbf"]
            or claims["exp"] - claims["iat"] > _MAX_TOKEN_SECONDS
        ):
            raise ReportAuthenticationError("report access denied")
