"""Synthetic IdP assertions cannot supply ORION tenant authority."""

import time

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from deployment.customer.oidc import IdentityDenied, OIDCVerifier

ISSUER = "https://idp.example.invalid/tenant"
AUDIENCE = "orion-customer-preview"
NONCE = "synthetic-nonce-1234567890"


@pytest.fixture
def provider():
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    public = private.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    return private, OIDCVerifier(issuer=ISSUER, audience=AUDIENCE, public_keys={"kid1": public})


def _token(key, **changes):
    now = int(time.time())
    claims = {
        "iss": ISSUER,
        "sub": "synthetic-user",
        "aud": AUDIENCE,
        "iat": now,
        "exp": now + 3600,
        "nonce": NONCE,
        "tenant_id": "attacker-supplied-tenant",
    }
    claims.update(changes)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": "kid1"})


def test_valid_idp_assertion_is_short_lived_and_tenant_claim_is_ignored(provider):
    private, verifier = provider
    identity = verifier.verify(_token(private), expected_nonce=NONCE)
    assert identity.issuer == ISSUER
    assert identity.subject == "synthetic-user"
    assert identity.expires_at <= time.time() + 300
    assert not hasattr(identity, "tenant_id")


@pytest.mark.parametrize(
    "claims",
    [
        {"iss": "https://other.invalid/tenant"},
        {"aud": "other-application"},
        {"aud": [AUDIENCE, "other-application"]},
        {"exp": 1},
        {"iat": 1},
        {"nonce": "wrong-nonce-1234567890"},
    ],
)
def test_wrong_issuer_audience_expiry_age_or_nonce_denied(provider, claims):
    private, verifier = provider
    with pytest.raises(IdentityDenied):
        verifier.verify(_token(private, **claims), expected_nonce=NONCE)


def test_forged_or_unknown_signer_denied(provider):
    private, verifier = provider
    other = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(IdentityDenied):
        verifier.verify(_token(other), expected_nonce=NONCE)
    forged = jwt.encode(
        {"iss": ISSUER, "sub": "synthetic-user", "aud": AUDIENCE},
        "not-a-provider-key-with-32-bytes-minimum",
        algorithm="HS256",
        headers={"kid": "kid1"},
    )
    with pytest.raises(IdentityDenied):
        verifier.verify(forged, expected_nonce=NONCE)
    with pytest.raises(IdentityDenied):
        verifier.verify(_token(private), expected_nonce="short")
    with pytest.raises(IdentityDenied):
        verifier.verify("malformed-token", expected_nonce=NONCE)


def test_provider_configuration_rejects_http_and_private_key(provider):
    private, _ = provider
    pem = private.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    with pytest.raises(ValueError):
        OIDCVerifier(issuer="http://idp.example.invalid", audience=AUDIENCE, public_keys={"kid1": pem})
    with pytest.raises(ValueError, match="invalid OIDC provider key"):
        OIDCVerifier(issuer=ISSUER, audience=AUDIENCE, public_keys={"kid1": pem})
