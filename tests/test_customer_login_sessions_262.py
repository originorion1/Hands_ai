"""Synthetic OIDC code flow, membership and browser session denial tests."""

from __future__ import annotations

import base64
import json
import time
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

from deployment.customer.login import CustomerLogin, LoginDenied
from deployment.customer.oidc import OIDCVerifier, VerifiedIdentity
from deployment.customer.tenants import TenantRegistry

ISSUER = "https://idp.example.invalid/tenant"
AUDIENCE = "orion-customer"


class SyntheticExchange:
    assertion = None

    def __call__(self, code, verifier, redirect):
        assert code in {"synthetic-one-use-code", "one-use"}
        assert len(verifier) >= 32
        assert redirect == "https://command.example.invalid/callback"
        return self.assertion


def _bootstrap(registry, signer, tenant, subject, nonce):
    approval = {
        "approval_id": f"synthetic-bootstrap-{nonce:04d}", "tenant_id": tenant,
        "organization_name": f"Synthetic {tenant}", "country": "US", "timezone": "UTC",
        "sector": "hospitality", "data_residency": "approved-region", "retention_days": 30,
        "admin_issuer": ISSUER, "admin_subject": subject, "expires_at": int(time.time()) + 300,
    }
    message = b"ORION CUSTOMER BOOTSTRAP V1\0" + json.dumps(
        approval, sort_keys=True, separators=(",", ":")
    ).encode()
    signature = base64.b64encode(signer.sign(message)).decode()
    identity = VerifiedIdentity(ISSUER, subject, f"bootstrap-{nonce}", int(time.time()) + 300)
    registry.provision(identity, approval=approval, signature=signature)
    return identity


@pytest.fixture
def context(tmp_path):
    directory = tmp_path / "private"
    directory.mkdir(mode=0o700)
    signer = Ed25519PrivateKey.generate()
    registry = TenantRegistry(directory / "registry.sqlite", approval_public_key=signer.public_key())
    alpha = _bootstrap(registry, signer, "tenant-alpha", "alpha-admin", 1001)
    beta = _bootstrap(registry, signer, "tenant-beta", "beta-admin", 1002)
    analyst = VerifiedIdentity(ISSUER, "alpha-analyst", "enrollment", int(time.time()) + 300)
    registry.add_member(alpha, tenant_id="tenant-alpha", member=analyst, role="analyst")
    rsa_key = generate_private_key(public_exponent=65537, key_size=2048)
    pem = rsa_key.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    verifier = OIDCVerifier(issuer=ISSUER, audience=AUDIENCE, public_keys={"key-1": pem})
    exchange = SyntheticExchange()
    login = CustomerLogin(
        registry, verifier, authorization_endpoint="https://idp.example.invalid/authorize",
        client_id=AUDIENCE, redirect_uri="https://command.example.invalid/callback",
        token_exchange=exchange,
    )
    return registry, login, rsa_key, alpha, beta


def _assertion(key, subject, nonce, *, tenant_claim="tenant-beta"):
    now = int(time.time())
    return jwt.encode({
        "iss": ISSUER, "sub": subject, "aud": AUDIENCE, "iat": now,
        "exp": now + 240, "nonce": nonce, "tenant_id": tenant_claim,
    }, key, algorithm="RS256", headers={"kid": "key-1"})


def _complete(login, key, subject, tenant="tenant-alpha", **changes):
    start = login.start(tenant_id=tenant)
    query = {key: values[0] for key, values in parse_qs(urlsplit(start.authorization_url).query).items()}
    assert query["response_type"] == "code"
    assert query["code_challenge_method"] == "S256"
    assert "code_verifier" not in query
    if changes.get("nonce") == "wrong":
        nonce = "wrong-nonce-0123456789"
    else:
        nonce = query["nonce"]
    assertion = _assertion(key, subject, nonce)

    login._token_exchange.assertion = assertion
    return login.finish(state=query["state"], code="synthetic-one-use-code"), query


def _secret(session):
    return session.cookie.split(";", 1)[0].split("=", 1)[1]


def test_admin_login_requires_membership_and_csrf(context):
    _registry, login, key, _alpha, _beta = context
    session, query = _complete(login, key, "alpha-admin")
    secret = _secret(session)
    assert "Secure" in session.cookie and "HttpOnly" in session.cookie
    assert "SameSite=Lax" in session.cookie and "Path=/" in session.cookie
    assert login.authorize(secret, required_role="administrator").tenant_id == "tenant-alpha"
    with pytest.raises(LoginDenied, match="CSRF"):
        login.authorize(secret, mutating=True)
    with pytest.raises(LoginDenied, match="CSRF"):
        login.authorize(secret, mutating=True, csrf_token="forged")
    assert login.authorize(secret, mutating=True,
                           csrf_token=session.csrf_token).tenant_id == "tenant-alpha"
    with pytest.raises(LoginDenied, match="state"):
        login.finish(state=query["state"], code="synthetic-one-use-code")
    with pytest.raises(LoginDenied, match="CSRF"):
        login.logout(secret, csrf_token="forged")
    cleared = login.logout(secret, csrf_token=session.csrf_token)
    assert "Max-Age=0" in cleared
    with pytest.raises(LoginDenied, match="session"):
        login.authorize(secret)


def test_untrusted_tenant_claim_and_wrong_tenant_are_denied(context):
    _registry, login, key, _alpha, _beta = context
    # The assertion falsely claims tenant-beta, but membership binds tenant-alpha.
    session, _query = _complete(login, key, "alpha-admin")
    assert login.authorize(_secret(session)).tenant_id == "tenant-alpha"
    with pytest.raises(LoginDenied, match="membership"):
        _complete(login, key, "alpha-admin", tenant="tenant-beta")
    with pytest.raises(LoginDenied, match="membership"):
        _complete(login, key, "stranger", tenant="tenant-alpha")


def test_analyst_cannot_escalate_or_reuse_expired_session(context):
    registry, login, key, _alpha, _beta = context
    session, _query = _complete(login, key, "alpha-analyst")
    secret = _secret(session)
    assert login.authorize(secret).role == "analyst"
    with pytest.raises(LoginDenied, match="membership"):
        login.authorize(secret, required_role="administrator")
    with registry._connection() as db:
        db.execute("UPDATE browser_sessions SET expires_at=1")
    with pytest.raises(LoginDenied, match="session"):
        login.authorize(secret)


def test_tenant_suspension_denies_existing_browser_session(context):
    registry, login, key, _alpha, _beta = context
    session, _query = _complete(login, key, "alpha-admin")
    secret = _secret(session)
    with registry._connection() as db:
        db.execute("UPDATE tenants SET status='suspended' WHERE tenant_id='tenant-alpha'")
    with pytest.raises(LoginDenied, match="membership"):
        login.authorize(secret)


def test_nonce_and_forged_state_fail_closed(context):
    _registry, login, key, _alpha, _beta = context
    with pytest.raises(LoginDenied, match="identity"):
        _complete(login, key, "alpha-admin", nonce="wrong")
    with pytest.raises(LoginDenied, match="state"):
        login.finish(state="forged-state" * 4, code="synthetic-one-use-code")


def test_concurrent_state_consumed_once(context):
    _registry, login, key, _alpha, _beta = context
    start = login.start(tenant_id="tenant-alpha")
    query = {key: values[0] for key, values in parse_qs(urlsplit(start.authorization_url).query).items()}
    assertion = _assertion(key, "alpha-admin", query["nonce"])
    login._token_exchange.assertion = assertion
    def attempt(_):
        try:
            login.finish(state=query["state"], code="one-use")
        except LoginDenied:
            return "denied"
        return "accepted"
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = sorted(pool.map(attempt, range(2)))
    assert results == ["accepted", "denied"]


def test_invalid_provider_origin_and_insecure_redirect_denied(context):
    registry, _login, _key, _alpha, _beta = context
    verifier = OIDCVerifier(
        issuer=ISSUER, audience=AUDIENCE,
        public_keys={"key-1": _key.public_key().public_bytes(
            serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
        )},
    )
    with pytest.raises(ValueError, match="issuer origin"):
        CustomerLogin(registry, verifier,
                      authorization_endpoint="https://other.example.invalid/authorize",
                      client_id=AUDIENCE, redirect_uri="https://command.example.invalid/callback",
                      token_exchange=SyntheticExchange())
    with pytest.raises(ValueError, match="configuration"):
        CustomerLogin(registry, verifier,
                      authorization_endpoint="https://idp.example.invalid/authorize",
                      client_id=AUDIENCE, redirect_uri="http://command.example.invalid/callback",
                      token_exchange=SyntheticExchange())
