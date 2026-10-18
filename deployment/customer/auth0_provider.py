"""Validate operator-fetched Auth0 OIDC metadata before portal composition.

Discovery and JWKS are data, never authority to change the reviewed issuer or
redirect origin. Fetching them is an operator step outside this pure adapter.
"""

from __future__ import annotations

import base64
import re
from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from urllib.parse import urlsplit

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicNumbers

_KID = re.compile(r"[A-Za-z0-9_-]{1,80}\Z")


@dataclass(frozen=True, slots=True)
class ReviewedAuth0Provider:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    public_keys: Mapping[str, bytes]


def _same_origin_url(value: object, issuer: str) -> bool:
    if type(value) is not str:
        return False
    target, provider = urlsplit(value), urlsplit(issuer)
    return (
        target.scheme == "https" and target.netloc == provider.netloc
        and target.path.startswith("/") and target.path != "/"
        and not target.username and not target.password
        and not target.query and not target.fragment
    )


def _rsa_public_pem(jwk: Mapping[str, object]) -> bytes:
    if (jwk.get("kty") != "RSA" or jwk.get("alg") != "RS256"
            or jwk.get("use") != "sig" or type(jwk.get("n")) is not str
            or type(jwk.get("e")) is not str):
        raise ValueError("reviewed Auth0 signing key required")
    try:
        def integer(value: str) -> int:
            if not re.fullmatch(r"[A-Za-z0-9_-]+", value):
                raise ValueError("invalid key encoding")
            return int.from_bytes(base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)), "big")

        key = RSAPublicNumbers(integer(jwk["e"]), integer(jwk["n"])).public_key()
        if key.key_size < 2048:
            raise ValueError("short signing key")
        return key.public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("reviewed Auth0 signing key required") from exc


def review_auth0_metadata(
    *, issuer: str, discovery: Mapping[str, object], jwks: Mapping[str, object],
) -> ReviewedAuth0Provider:
    """Pin one issuer, endpoint origin and reviewed RS256 key set.

    The caller fetches documents under direct, verified TLS and imposes a body
    limit. This function performs no network access and accepts no secret.
    """
    if type(issuer) is not str:
        raise ValueError("reviewed Auth0 issuer required")
    target = urlsplit(issuer)
    if (target.scheme != "https" or not target.hostname
            or target.username or target.password or target.query or target.fragment
            or target.path not in ("", "/") or not isinstance(discovery, Mapping)
            or not isinstance(jwks, Mapping) or discovery.get("issuer") != issuer):
        raise ValueError("reviewed Auth0 issuer required")
    endpoints = (
        discovery.get("authorization_endpoint"), discovery.get("token_endpoint"),
        discovery.get("jwks_uri"),
    )
    if not all(_same_origin_url(value, issuer) for value in endpoints):
        raise ValueError("Auth0 endpoint origin denied")
    if [urlsplit(value).path for value in endpoints] != [
        "/authorize", "/oauth/token", "/.well-known/jwks.json",
    ]:
        raise ValueError("Auth0 endpoint path denied")
    keys = jwks.get("keys")
    if type(keys) is not list or not 1 <= len(keys) <= 8:
        raise ValueError("reviewed Auth0 JWKS required")
    public_keys: dict[str, bytes] = {}
    for key in keys:
        if not isinstance(key, Mapping):
            raise TypeError("reviewed Auth0 JWKS required")
        kid = key.get("kid")
        if type(kid) is not str or not _KID.fullmatch(kid) or kid in public_keys:
            raise ValueError("reviewed Auth0 key identity required")
        public_keys[kid] = _rsa_public_pem(key)
    return ReviewedAuth0Provider(issuer, *endpoints, MappingProxyType(public_keys))
