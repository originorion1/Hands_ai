"""Hosted composition with synthetic OIDC, vault and two tenant boundaries."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import time
from contextlib import contextmanager
from email.message import Message
from urllib.parse import parse_qs, urlsplit

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

from deployment.customer.hosted import create_hosted_customer_portal
from deployment.customer.oidc import VerifiedIdentity
from deployment.customer.service import CustomerReportBinding
from deployment.customer.tenants import TenantRegistry
from orion_connector_sdk import ReviewedConnector

ISSUER = "https://idp.example.invalid/tenant"
ORIGIN = "https://portal.example.invalid"
CLIENT = "synthetic-orion-client"


def _bootstrap(registry, signer, tenant, subject):
    approval = {
        "approval_id": f"synthetic-{tenant}-approval", "tenant_id": tenant,
        "organization_name": f"Synthetic {tenant}", "country": "US",
        "timezone": "UTC", "sector": "synthetic", "data_residency": "test",
        "retention_days": 1, "admin_issuer": ISSUER, "admin_subject": subject,
        "expires_at": int(time.time()) + 300,
    }
    message = b"ORION CUSTOMER BOOTSTRAP V1\0" + json.dumps(
        approval, sort_keys=True, separators=(",", ":")
    ).encode()
    identity = VerifiedIdentity(ISSUER, subject, f"session-{subject}", int(time.time()) + 300)
    registry.provision(
        identity, approval=approval,
        signature=base64.b64encode(signer.sign(message)).decode(),
    )
    return identity


def _request(app, path, *, query="", method="GET", cookie="", csrf="", body=None,
             scheme="https", host="portal.example.invalid"):
    raw = b"" if body is None else json.dumps(body).encode()
    environ = {
        "wsgi.url_scheme": scheme, "HTTP_HOST": host,
        "REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query,
        "HTTP_COOKIE": cookie, "HTTP_ORIGIN": ORIGIN,
        "HTTP_X_ORION_CSRF": csrf, "CONTENT_LENGTH": str(len(raw)),
        "CONTENT_TYPE": "application/json", "wsgi.input": io.BytesIO(raw),
    }
    response = {}

    def start(status, headers):
        response["status"] = status
        response["headers"] = headers

    response["body"] = b"".join(app(environ, start))
    return response


def _login(app, key, assertions, subject, tenant):
    start = _request(app, "/customer/login", query=f"tenant={tenant}")
    assert start["status"] == "302 Found"
    query = {name: parts[0] for name, parts in parse_qs(
        urlsplit(dict(start["headers"])["Location"]).query,
    ).items()}
    now = int(time.time())
    assertions["token"] = jwt.encode({
        "iss": ISSUER, "sub": subject, "aud": CLIENT, "iat": now,
        "exp": now + 240, "nonce": query["nonce"],
        "tenant_id": "tenant-beta" if tenant == "tenant-alpha" else "tenant-alpha",
    }, key, algorithm="RS256", headers={"kid": "synthetic-key"})
    callback = _request(
        app, "/callback", query=f"state={query['state']}&code=synthetic-code",
    )
    assert callback["status"] == "303 See Other"
    cookies = [value for name, value in callback["headers"] if name == "Set-Cookie"]
    assert "Secure" in cookies[0] and "HttpOnly" in cookies[0]
    assert "SameSite=Lax" in cookies[0] and "__Host-orion_session=" in cookies[0]
    return cookies[0].split(";", 1)[0], cookies[1].split(";", 1)[0].partition("=")[2]


def test_hosted_portal_keeps_identity_vault_grant_and_report_server_side(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    signer = Ed25519PrivateKey.generate()
    registry = TenantRegistry(private / "tenants.sqlite", approval_public_key=signer.public_key())
    alpha = _bootstrap(registry, signer, "tenant-alpha", "alpha-admin")
    _bootstrap(registry, signer, "tenant-beta", "beta-admin")
    analyst = VerifiedIdentity(ISSUER, "alpha-analyst", "analyst-setup", int(time.time()) + 300)
    registry.add_member(alpha, tenant_id="tenant-alpha", member=analyst, role="analyst")
    rsa = generate_private_key(public_exponent=65537, key_size=2048)
    public = rsa.public_key().public_bytes(
        serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    fields = [
        {"name": name, "label": name.replace("_", " ").title(), "kind": kind,
         "required": True, "write_only": kind == "secret"}
        for name, kind in (("endpoint", "https_url"), ("api_key", "secret"),
                           ("api_secret", "secret"), ("company", "text"))
    ]
    schema = json.dumps({
        "schema_version": "orion.connector-configuration.v1", "connector": "erpnext",
        "fields": fields,
    }, sort_keys=True).encode()
    vault = {}
    assertions = {}
    active = {"tenant-alpha": True, "tenant-beta": True}

    def vault_store(tenant, connector, key, secret):
        reference = "vault:" + ("a" if tenant == "tenant-alpha" else "b") * 28
        vault[reference] = (tenant, connector, key, secret)
        return reference

    class ReportSource:
        def __init__(self, tenant):
            self.tenant_id = tenant

        def read_view(self):
            return {
                "report_kind": "metadata_study",
                "lineage": {"tenant_id": self.tenant_id,
                            "authorization_id": f"grant-{self.tenant_id}",
                            "run_id": f"run-{self.tenant_id}", "evidence_ids": ["evidence-a"]},
                "authority": {"erp_writes": 0, "execution_allowed": False},
                "study": {"selected_question": "Which permitted structure is linked?"},
            }

    @contextmanager
    def grant_commit(_authorization_id):
        yield

    def lookup(tenant):
        if tenant not in active:
            return None
        return CustomerReportBinding(
            ReportSource(tenant), f"grant-{tenant}",
            lambda _authorization_id: active[tenant], grant_commit,
        )

    class TokenResponse:
        status = 200

        def __init__(self):
            self.headers = Message()
            self.headers["Content-Type"] = "application/json"

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def geturl(self):
            return "https://idp.example.invalid/token"

        def read(self, _limit):
            return json.dumps({"id_token": assertions["token"]}).encode()

    def token_opener(request, *, timeout):
        assert timeout == 10
        assert request.full_url == "https://idp.example.invalid/token"
        assert request.get_method() == "POST"
        assert parse_qs(request.data.decode())["client_secret"] == ["synthetic-idp-secret"]
        return TokenResponse()

    app = create_hosted_customer_portal(
        origin=ORIGIN, registry=registry, oidc_issuer=ISSUER,
        oidc_authorization_endpoint="https://idp.example.invalid/authorize",
        oidc_token_endpoint="https://idp.example.invalid/token",
        oidc_client_id=CLIENT, oidc_redirect_uri=f"{ORIGIN}/callback",
        oidc_public_keys={"synthetic-key": public},
        oidc_client_secret_reference="vault:" + "i" * 28,
        vault_read_client_secret=lambda _reference: "synthetic-idp-secret",
        vault_store_connector=vault_store,
        vault_revoke_connector=lambda reference: vault.pop(reference, None),
        report_lookup=lookup,
        reviewed_connector=ReviewedConnector(
            "erpnext", "originorion-connector-erpnext-discovery", "0.2.0rc4",
            "orion.discovery.erpnext_adapter:ERPNextDiscoveryAdapter",
        ),
        connector_schema_bytes=schema,
        connector_schema_sha256=hashlib.sha256(schema).hexdigest(),
        approved_erp_origin="https://erp.example.com",
    )
    app.login._token_exchange._opener = token_opener  # Synthetic test only.
    assert _request(app, "/customer/api/overview", scheme="http")["status"] == "403 Forbidden"
    assert _request(app, "/customer/api/overview", host="forged.example.invalid")[
        "status"] == "403 Forbidden"
    alpha_cookie, alpha_csrf = _login(app, rsa, assertions, "alpha-admin", "tenant-alpha")
    beta_cookie, beta_csrf = _login(app, rsa, assertions, "beta-admin", "tenant-beta")
    analyst_cookie, analyst_csrf = _login(
        app, rsa, assertions, "alpha-analyst", "tenant-alpha",
    )
    alpha_report = _request(app, "/customer/api/report", cookie=alpha_cookie)
    beta_report = _request(app, "/customer/api/report", cookie=beta_cookie)
    assert json.loads(alpha_report["body"])["lineage"]["tenant_id"] == "tenant-alpha"
    assert json.loads(beta_report["body"])["lineage"]["tenant_id"] == "tenant-beta"
    assert _request(app, "/customer/api/erpnext/enroll", method="POST",
                    cookie=analyst_cookie, csrf=analyst_csrf,
                    body={"label": "Synthetic ERP", "endpoint": "https://erp.example.com",
                          "company": "Synthetic Alpha", "api_key": "synthetic-key-value",
                          "api_secret": "synthetic-secret-value"})["status"] == "401 Unauthorized"
    enrollment = _request(app, "/customer/api/erpnext/enroll", method="POST",
                          cookie=alpha_cookie, csrf=alpha_csrf,
                          body={"label": "Synthetic ERP", "endpoint": "https://erp.example.com",
                                "company": "Synthetic Alpha", "api_key": "synthetic-key-value",
                                "api_secret": "synthetic-secret-value"})
    assert enrollment["status"] == "202 Accepted"
    assert b"synthetic-secret-value" not in enrollment["body"]
    assert b"vault:" not in enrollment["body"]
    assert b"synthetic-secret-value" not in registry._path.read_bytes()
    assert b"vault:" not in _request(app, "/customer/api/overview", cookie=alpha_cookie)["body"]
    proposal = {
        "tenant_id": "tenant-alpha", "companies": ["Synthetic Alpha"],
        "fields_by_doctype": [["Purchase Order", ["name"]]],
        "sensitive_exclusions": ["payroll"],
        "historical_start": "2025-01-01", "historical_end": "2025-12-31",
        "max_requests": 25, "max_duration_seconds": 1800,
        "max_evidence_bytes": 65536, "retention_days": 1,
        "revocation_operator": "Synthetic Operator", "read_only": True,
    }
    assert _request(app, "/customer/api/authorization-proposals", method="POST",
                    cookie=analyst_cookie, csrf=analyst_csrf, body=proposal)[
        "status"] == "401 Unauthorized"
    submitted = _request(app, "/customer/api/authorization-proposals", method="POST",
                         cookie=alpha_cookie, csrf=alpha_csrf, body=proposal)
    assert submitted["status"] == "202 Accepted"
    assert json.loads(submitted["body"])["execution_authority"] is False
    assert _request(app, "/customer/api/switch", method="POST", cookie=alpha_cookie,
                    csrf=alpha_csrf, body={"tenant_id": "tenant-beta"})[
        "status"] == "401 Unauthorized"
    active["tenant-alpha"] = False
    denied = _request(app, "/customer/api/report", cookie=alpha_cookie)
    assert denied["status"] == "401 Unauthorized"
    assert any(name == "Set-Cookie" and "Max-Age=0" in value
               for name, value in denied["headers"])
    assert _request(app, "/customer/api/report", cookie=beta_cookie)["status"] == "200 OK"
    assert _request(app, "/customer/api/logout", method="POST", cookie=beta_cookie,
                    csrf=beta_csrf, body={})["status"] == "200 OK"
    assert _request(app, "/customer/api/report", cookie=beta_cookie)[
        "status"] == "401 Unauthorized"


def test_hosted_portal_requires_backend_dependencies(tmp_path):
    private = tmp_path / "private"
    private.mkdir(mode=0o700)
    signer = Ed25519PrivateKey.generate()
    registry = TenantRegistry(private / "tenants.sqlite", approval_public_key=signer.public_key())
    with pytest.raises(ValueError, match="reviewed hosted portal dependencies"):
        create_hosted_customer_portal(
            origin=ORIGIN, registry=registry, oidc_issuer=ISSUER,
            oidc_authorization_endpoint="https://idp.example.invalid/authorize",
            oidc_token_endpoint="https://idp.example.invalid/token",
            oidc_client_id=CLIENT, oidc_redirect_uri=f"{ORIGIN}/callback",
            oidc_public_keys={}, oidc_client_secret_reference="",
            vault_read_client_secret=lambda _reference: "x",
            vault_store_connector=lambda *_args: "vault:" + "v" * 28,
            vault_revoke_connector=lambda _reference: None,
            report_lookup=lambda _tenant: None,
            reviewed_connector=ReviewedConnector(
                "erpnext", "originorion-connector-erpnext-discovery", "0.2.0rc4",
                "orion.discovery.erpnext_adapter:ERPNextDiscoveryAdapter",
            ),
            connector_schema_bytes=b"{}", connector_schema_sha256="0" * 64,
            approved_erp_origin="https://erp.example.com",
        )
