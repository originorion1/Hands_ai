"""Explicit loopback HTTPS development origin; no relaxed proxy trust."""

from __future__ import annotations

import hashlib
import io
from urllib.parse import parse_qs, urlsplit

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

from deployment.customer.hosted import create_hosted_customer_portal
from deployment.customer.portal_http import CustomerPortalHTTP
from deployment.customer.tenants import TenantRegistry
from orion_connector_sdk import ReviewedConnector

_ORIGIN = "https://localhost:8443"
_ISSUER = "https://idp.example.invalid/tenant"
_CLIENT = "synthetic-local-client"


def _runtime(registry_path, *, origin=_ORIGIN, redirect_uri=None,
             allow_development_localhost=True):
    signer = Ed25519PrivateKey.generate()
    registry = TenantRegistry(
        registry_path, approval_public_key=signer.public_key(),
    )
    rsa = generate_private_key(public_exponent=65537, key_size=2048)
    public_key = rsa.public_key().public_bytes(
        serialization.Encoding.PEM,
        serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    schema = b"{}"
    return create_hosted_customer_portal(
        origin=origin, registry=registry, oidc_issuer=_ISSUER,
        oidc_authorization_endpoint="https://idp.example.invalid/authorize",
        oidc_token_endpoint="https://idp.example.invalid/token",
        oidc_client_id=_CLIENT,
        oidc_redirect_uri=redirect_uri or f"{origin}/callback",
        oidc_public_keys={"synthetic-key": public_key},
        oidc_client_secret_reference="vault:oidc-" + "s" * 28,
        vault_read_client_secret=lambda _reference: "synthetic-secret",
        vault_store_connector=lambda *_args: "vault:conn-" + "c" * 28,
        vault_revoke_connector=lambda _reference: None,
        report_lookup=lambda _tenant: None,
        reviewed_connector=ReviewedConnector(
            "erpnext", "originorion-connector-erpnext-discovery", "0.2.0rc4",
            "orion.discovery.erpnext_adapter:ERPNextDiscoveryAdapter",
        ),
        connector_schema_bytes=schema,
        connector_schema_sha256=hashlib.sha256(schema).hexdigest(),
        approved_erp_origin="https://erp.example.com",
        allow_development_localhost=allow_development_localhost,
    )


def _request(app, *, path, query="", scheme="https", host="localhost:8443",
             forwarded_host=None):
    result = {}
    environ = {
        "wsgi.url_scheme": scheme, "HTTP_HOST": host, "REQUEST_METHOD": "GET",
        "PATH_INFO": path, "QUERY_STRING": query, "CONTENT_LENGTH": "0",
        "wsgi.input": io.BytesIO(b""),
    }
    if forwarded_host is not None:
        environ["HTTP_X_FORWARDED_HOST"] = forwarded_host

    def start(status, headers):
        result["status"] = status
        result["headers"] = headers

    result["body"] = b"".join(app(environ, start))
    return result


def test_explicit_localhost_8443_keeps_fixed_oidc_redirect_and_origin_checks(tmp_path):
    app = _runtime(tmp_path / "registry.sqlite")
    response = _request(
        app, path="/customer/login", query="tenant=synthetic-local",
    )
    assert response["status"] == "302 Found"
    authorization = urlsplit(dict(response["headers"])["Location"])
    query = parse_qs(authorization.query)
    assert query["redirect_uri"] == ["https://localhost:8443/callback"]
    assert _request(
        app, path="/customer/login", query="tenant=synthetic-local",
        forwarded_host="attacker.example",
    )["status"] == "302 Found"
    assert query["response_type"] == ["code"]
    assert query["code_challenge_method"] == ["S256"]
    assert len(query["state"][0]) >= 30
    assert len(query["nonce"][0]) >= 30
    assert len(query["code_challenge"][0]) == 43

    assert _request(app, path="/customer/")["status"] == "200 OK"
    assert _request(app, path="/customer/", scheme="http")["status"] == "403 Forbidden"
    assert _request(app, path="/customer/", host="localhost")["status"] == "403 Forbidden"
    assert _request(app, path="/customer/", host="localhost:8444")["status"] == "403 Forbidden"
    assert _request(app, path="/customer/", host="evil.example:8443")["status"] == "403 Forbidden"


@pytest.mark.parametrize("origin", [
    "https://127.0.0.1:8443", "https://evil.example:8443", "https://localhost:8444",
])
def test_development_opt_in_never_accepts_other_nonstandard_origins(tmp_path, origin):
    with pytest.raises(ValueError, match="reviewed HTTPS portal origin"):
        _runtime(tmp_path / "registry.sqlite", origin=origin)


def test_localhost_8443_requires_explicit_development_opt_in(tmp_path):
    with pytest.raises(ValueError, match="reviewed HTTPS portal origin"):
        _runtime(
            tmp_path / "registry.sqlite", allow_development_localhost=False,
        )


def test_localhost_callback_must_match_configured_https_origin(tmp_path):
    with pytest.raises(ValueError, match="reviewed HTTPS portal origin"):
        _runtime(
            tmp_path / "registry.sqlite",
            redirect_uri="https://localhost:443/callback",
        )


def test_portal_http_rejects_non_boolean_development_opt_in():
    login = type("LoginStub", (), {"_redirect_uri": f"{_ORIGIN}/callback"})()
    service = type("ServiceStub", (), {"login": login})()
    with pytest.raises(ValueError, match="reviewed HTTPS portal origin"):
        CustomerPortalHTTP(
            login=login, service=service, origin=_ORIGIN,
            allow_development_localhost=1,
        )
