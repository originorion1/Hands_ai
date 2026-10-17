"""Offline verification of an established OIDC provider's signed assertion.

Provider keys are provisioned and rotated by an operator outside this service.
The untrusted assertion never provides tenant membership or signing keys.
"""

from __future__ import annotations

import hashlib
import hmac
import re
import time
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import urlsplit

import jwt
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

_KID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")
_MAX_TOKEN_BYTES = 8192
_MAX_SESSION_SECONDS = 300


class IdentityDenied(ValueError):
    """An assertion cannot establish an authenticated user identity."""


@dataclass(frozen=True, slots=True)
class VerifiedIdentity:
    issuer: str
    subject: str
    session_id: str
    expires_at: int


class OIDCVerifier:
    """Verify RS256 assertions against pinned IdP public keys and audience."""

    def __init__(self, *, issuer: str, audience: str, public_keys: Mapping[str, bytes]):
        target = urlsplit(issuer)
        if (
            target.scheme != "https"
            or not target.hostname
            or target.username is not None
            or target.password is not None
            or target.query
            or target.fragment
            or not audience
            or not public_keys
        ):
            raise ValueError("invalid OIDC provider configuration")
        verified_keys = {}
        for kid, pem in public_keys.items():
            if type(kid) is not str or not _KID.fullmatch(kid) or type(pem) is not bytes:
                raise ValueError("invalid OIDC provider key")
            try:
                key = serialization.load_pem_public_key(pem)
            except (ValueError, TypeError) as exc:
                raise ValueError("invalid OIDC provider key") from exc
            if not isinstance(key, RSAPublicKey) or key.key_size < 2048:
                raise ValueError("invalid OIDC provider key")
            verified_keys[kid] = key
        self._issuer = issuer
        self._audience = audience
        self._keys = MappingProxyType(verified_keys)

    def verify(self, assertion: str, *, expected_nonce: str) -> VerifiedIdentity:
        if type(assertion) is not str or not 0 < len(assertion.encode()) <= _MAX_TOKEN_BYTES:
            raise IdentityDenied("invalid identity assertion")
        if type(expected_nonce) is not str or not 16 <= len(expected_nonce) <= 128:
            raise IdentityDenied("missing login nonce")
        try:
            header = jwt.get_unverified_header(assertion)
            if (
                type(header) is not dict
                or header.get("alg") != "RS256"
                or header.get("typ") not in (None, "JWT")
                or type(header.get("kid")) is not str
                or header["kid"] not in self._keys
                or set(header) not in ({"alg", "kid"}, {"alg", "typ", "kid"})
            ):
                raise IdentityDenied("unsupported identity assertion header")
            claims = jwt.decode(
                assertion,
                self._keys[header["kid"]],
                algorithms=["RS256"],
                audience=self._audience,
                issuer=self._issuer,
                options={"require": ["iss", "sub", "aud", "iat", "exp", "nonce"]},
                leeway=0,
            )
        except jwt.PyJWTError as exc:
            raise IdentityDenied("identity assertion rejected") from exc
        if (
            type(claims.get("sub")) is not str
            or not claims["sub"].strip()
            or claims.get("aud") != self._audience
            or type(claims.get("iat")) is not int
            or type(claims.get("exp")) is not int
            or type(claims.get("nonce")) is not str
            or not hmac.compare_digest(claims["nonce"], expected_nonce)
            or not 0 <= time.time() - claims["iat"] <= _MAX_SESSION_SECONDS
            or claims["iat"] >= claims["exp"]
        ):
            raise IdentityDenied("invalid identity assertion claims")
        return VerifiedIdentity(
            self._issuer,
            claims["sub"],
            hashlib.sha256(assertion.encode()).hexdigest(),
            min(claims["exp"], int(time.time()) + _MAX_SESSION_SECONDS),
        )
