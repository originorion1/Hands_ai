"""Synthetic provider contracts; no Auth0 or Google endpoint is contacted."""

from __future__ import annotations

import base64
import io
import json
from email.message import Message
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit
from urllib.request import ProxyHandler

import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from deployment.customer.auth0_provider import review_auth0_metadata
from deployment.customer.google_secret_vault import (
    GoogleSecretManagerVault,
    VaultUnavailable,
    _crc32c,
)
from deployment.customer.hosted import create_auth0_google_portal
from deployment.customer.tenants import TenantRegistry
from orion_connector_sdk import ReviewedConnector


def _jwk():
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048).public_key().public_numbers()

    def encoded(value):
        return base64.urlsafe_b64encode(value.to_bytes((value.bit_length() + 7) // 8, "big")).rstrip(b"=").decode()

    return {"kty": "RSA", "alg": "RS256", "use": "sig", "kid": "reviewed-key",
            "n": encoded(key.n), "e": encoded(key.e)}


def test_auth0_metadata_pins_issuer_endpoints_and_rs256_keys():
    issuer = "https://synthetic.auth0.example/"
    discovery = {"issuer": issuer,
                 "authorization_endpoint": f"{issuer}authorize",
                 "token_endpoint": f"{issuer}oauth/token",
                 "jwks_uri": f"{issuer}.well-known/jwks.json"}
    key = _jwk()
    reviewed = review_auth0_metadata(issuer=issuer, discovery=discovery, jwks={"keys": [key]})
    assert reviewed.authorization_endpoint == discovery["authorization_endpoint"]
    assert b"BEGIN PUBLIC KEY" in reviewed.public_keys["reviewed-key"]
    with pytest.raises(TypeError):
        reviewed.public_keys["forged"] = b"not a key"
    with pytest.raises(ValueError, match="endpoint origin"):
        review_auth0_metadata(
            issuer=issuer, discovery={**discovery, "token_endpoint": "https://other.invalid/token"},
            jwks={"keys": [key]},
        )
    with pytest.raises(ValueError, match="issuer"):
        review_auth0_metadata(
            issuer=issuer, discovery={**discovery, "issuer": "https://other.invalid/"},
            jwks={"keys": [key]},
        )
    with pytest.raises(ValueError, match="key"):
        review_auth0_metadata(
            issuer=issuer, discovery=discovery, jwks={"keys": [{**key, "alg": "HS256"}]},
        )
    with pytest.raises(ValueError, match="path"):
        review_auth0_metadata(
            issuer=issuer, discovery={**discovery, "jwks_uri": issuer + "other-keys"},
            jwks={"keys": [key]},
        )


class _Response:
    def __init__(self, url, payload, status=200):
        self.url, self.status = url, status
        self.body = io.BytesIO(json.dumps(payload).encode())
        self.headers = Message()
        self.headers["Content-Type"] = "application/json"

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def geturl(self):
        return self.url

    def read(self, count):
        return self.body.read(count)


class _SecretManager:
    def __init__(self):
        self.secrets = {}
        self.calls = []
        self.fail_add = False
        self.corrupt_read = False

    def __call__(self, request, *, timeout):
        assert timeout == 10
        url = request.full_url
        assert url.startswith("https://secretmanager.googleapis.com/v1/projects/synth-project/secrets")
        assert request.get_header("Authorization") == "Bearer synthetic-" + "x" * 24
        method = request.get_method()
        self.calls.append((method, url))
        split = urlsplit(url)
        path = split.path.removeprefix("/v1/")
        if method == "POST" and split.query:
            secret_id = parse_qs(split.query)["secretId"][0]
            name = "projects/synth-project/secrets/" + secret_id
            if name in self.secrets:
                raise HTTPError(url, 409, "conflict", {}, None)
            self.secrets[name] = None
            return _Response(url, {"name": name})
        if method == "POST" and path.endswith(":addVersion"):
            if self.fail_add:
                raise HTTPError(url, 503, "unavailable", {}, None)
            name = path.removesuffix(":addVersion")
            body = json.loads(request.data)
            self.secrets[name] = body["payload"]
            return _Response(url, {"name": name + "/versions/1"})
        if method == "GET" and path.endswith("/versions/1:access"):
            name = path.removesuffix("/versions/1:access")
            payload = self.secrets.get(name)
            if payload is None:
                raise HTTPError(url, 404, "not found", {}, None)
            if self.corrupt_read:
                payload = {**payload, "dataCrc32c": "0"}
            return _Response(url, {"name": name + "/versions/1", "payload": payload})
        if method == "DELETE":
            self.secrets.pop(path, None)
            return _Response(url, {})
        raise AssertionError("unexpected synthetic vault request")


def _vault(fake):
    return GoogleSecretManagerVault(
        project_id="synth-project", access_token=lambda: "synthetic-" + "x" * 24,
        opener=fake,
    )


def test_google_secret_manager_connector_scope_custody_and_revocation():
    fake = _SecretManager()
    vault = _vault(fake)
    ref = vault.store_connector("tenant-one", "erpnext", "synthetic-key", "synthetic-secret")
    assert ref.startswith("vault:conn-")
    assert vault.read_connector(ref, tenant="tenant-one", connector="erpnext") == (
        "synthetic-key", "synthetic-secret",
    )
    with pytest.raises(VaultUnavailable):
        vault.read_connector(ref, tenant="tenant-two", connector="erpnext")
    before = len(fake.calls)
    with pytest.raises(VaultUnavailable):
        vault.read_connector(ref, tenant="FORGED", connector="erpnext")
    assert len(fake.calls) == before
    with pytest.raises(VaultUnavailable):
        vault.read_client_secret(ref)
    fake.corrupt_read = True
    with pytest.raises(VaultUnavailable, match="payload denied"):
        vault.read_connector(ref, tenant="tenant-one", connector="erpnext")
    fake.corrupt_read = False
    vault.revoke_connector(ref)
    with pytest.raises(VaultUnavailable):
        vault.read_connector(ref, tenant="tenant-one", connector="erpnext")
    assert all("synthetic-secret" not in url for _, url in fake.calls)


def test_google_secret_manager_oidc_secret_and_failure_cleanup():
    fake = _SecretManager()
    vault = _vault(fake)
    oidc_ref = "vault:oidc-" + "a" * 32
    oidc_name = vault._resource(oidc_ref, kind="oidc")
    value = b"synthetic-client-secret"
    fake.secrets[oidc_name] = {"data": base64.b64encode(value).decode(),
                               "dataCrc32c": str(_crc32c(value))}
    assert vault.read_client_secret(oidc_ref) == value.decode()
    with pytest.raises(VaultUnavailable):
        vault.revoke_connector(oidc_ref)
    fake.fail_add = True
    with pytest.raises(VaultUnavailable):
        vault.store_connector("tenant-one", "erpnext", "synthetic-key", "synthetic-secret")
    assert list(fake.secrets) == [oidc_name]


def test_google_secret_manager_denies_bad_identity_and_ambient_proxy(monkeypatch):
    monkeypatch.setenv("HTTPS_PROXY", "http://untrusted.invalid:8080")
    monkeypatch.setenv("ALL_PROXY", "http://untrusted.invalid:8080")
    vault = GoogleSecretManagerVault(
        project_id="synth-project", access_token=lambda: "synthetic-" + "x" * 24,
    )
    assert all(not handler.proxies for handler in vault._opener.__self__.handlers
               if isinstance(handler, ProxyHandler))
    with pytest.raises(VaultUnavailable):
        GoogleSecretManagerVault(
            project_id="synth-project", access_token=lambda: "secret\nheader injection",
            opener=_SecretManager(),
        ).read_client_secret("vault:oidc-" + "a" * 32)


@pytest.mark.parametrize(("origin", "allow_localhost"), [
    ("https://portal.example.invalid", False),
    ("https://localhost:8443", True),
])
def test_reviewed_provider_composition_uses_private_callbacks(
    tmp_path, origin, allow_localhost,
):
    issuer = "https://synthetic.auth0.example/"
    provider = review_auth0_metadata(
        issuer=issuer,
        discovery={"issuer": issuer, "authorization_endpoint": issuer + "authorize",
                   "token_endpoint": issuer + "oauth/token",
                   "jwks_uri": issuer + ".well-known/jwks.json"},
        jwks={"keys": [_jwk()]},
    )
    fake = _SecretManager()
    vault = _vault(fake)
    oidc_ref = "vault:oidc-" + "a" * 32
    oidc_name = vault._resource(oidc_ref, kind="oidc")
    value = b"synthetic-client-secret"
    fake.secrets[oidc_name] = {"data": base64.b64encode(value).decode(),
                               "dataCrc32c": str(_crc32c(value))}
    registry = TenantRegistry(
        tmp_path / "tenants.sqlite", approval_public_key=Ed25519PrivateKey.generate().public_key(),
    )
    app = create_auth0_google_portal(
        origin=origin, registry=registry, provider=provider,
        oidc_client_id="synthetic-client", oidc_client_secret_reference=oidc_ref,
        vault=vault, report_lookup=lambda _tenant: None,
        reviewed_connector=ReviewedConnector(
            "erpnext", "originorion-connector-erpnext-discovery", "0.2.0rc4",
            "orion.discovery.erpnext_adapter:ERPNextDiscoveryAdapter",
        ),
        connector_schema_bytes=b"{}", connector_schema_sha256="0" * 64,
        approved_erp_origin="https://erp.example.invalid",
        allow_development_localhost=allow_localhost,
    )
    assert app.login._verifier._issuer == issuer
    assert app.login._token_exchange._resolve_secret(oidc_ref) == value.decode()
    assert app.origin == origin
    assert app.login._redirect_uri == f"{origin}/callback"
