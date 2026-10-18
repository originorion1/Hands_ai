"""Synthetic OIDC code flow, membership and browser session denial tests."""

from __future__ import annotations

import base64
import hashlib
import io
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key

from deployment.customer.enrollment import EnrollmentDenied, enroll_erpnext
from deployment.customer.journey import run_customer_synthetic_lineage
from deployment.customer.login import CustomerLogin, LoginDenied
from deployment.customer.oidc import OIDCVerifier, VerifiedIdentity
from deployment.customer.portal import AuthorizationProposal
from deployment.customer.portal_http import CustomerPortalHTTP
from deployment.customer.service import CustomerPortalService, CustomerReportBinding
from deployment.customer.tenants import TenantDenied, TenantRegistry
from deployment.release_candidate import continuous_lineage as lineage
from orion.contracts import EvidenceKind
from orion.discovery.pilot_read import PilotAuthorization, PilotRequest
from orion.discovery.read_window import ReviewedReadWindow
from orion_connector_sdk import ReviewedConnector, discover
from orion_connector_synthetic import SyntheticRecordConnector

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


def test_report_binding_denies_cross_tenant_source_even_with_valid_member(context):
    registry, login, key, _alpha, _beta = context
    beta_session, _ = _complete(login, key, "beta-admin", tenant="tenant-beta")

    class Source:
        tenant_id = "tenant-alpha"

        def read_view(self):
            raise AssertionError("cross-tenant source must not be read")

    from contextlib import nullcontext

    binding = CustomerReportBinding(
        Source(), "synthetic-grant", lambda _id: True, lambda _id: nullcontext(),
    )
    service = CustomerPortalService(
        login, registry, report_lookup=lambda _tenant: binding,
    )
    with pytest.raises(LoginDenied, match="tenant binding"):
        service.report(_secret(beta_session))


def test_verified_invited_user_can_complete_login_only_for_invited_tenant(context):
    registry, login, key, alpha, _beta = context
    registry.invite_member(
        alpha, tenant_id="tenant-alpha", issuer=ISSUER,
        subject="invited-analyst", role="analyst",
        expires_at=int(time.time()) + 120,
    )
    with pytest.raises(LoginDenied, match="membership"):
        _complete(login, key, "invited-analyst", tenant="tenant-beta")
    session, _ = _complete(login, key, "invited-analyst", tenant="tenant-alpha")
    assert login.authorize(_secret(session)).role == "analyst"
    registry.remove_member(
        alpha, tenant_id="tenant-alpha", issuer=ISSUER,
        subject="invited-analyst",
    )
    with pytest.raises(LoginDenied, match="membership"):
        login.authorize(_secret(session))


def test_portal_service_scopes_members_and_proposals_to_session(context):
    registry, login, key, _alpha, _beta = context
    service = CustomerPortalService(login, registry)
    admin_session, _ = _complete(login, key, "alpha-admin")
    analyst_session, _ = _complete(login, key, "alpha-analyst")
    admin_secret = _secret(admin_session)
    analyst_secret = _secret(analyst_session)
    overview = service.overview(analyst_secret)
    assert overview["tenant_id"] == "tenant-alpha"
    assert overview["role"] == "analyst"
    assert overview["authorization_status"] == "no_approved_grant_bound"
    assert overview["autonomous_study_proposals"] == []
    assert "credential_ref" not in json.dumps(overview)
    with pytest.raises(LoginDenied):
        service.invite(
            analyst_secret, csrf_token=analyst_session.csrf_token,
            issuer=ISSUER, subject="invited", role="analyst",
            expires_at=int(time.time()) + 120,
        )
    service.invite(
        admin_secret, csrf_token=admin_session.csrf_token,
        issuer=ISSUER, subject="invited", role="analyst",
        expires_at=int(time.time()) + 120,
    )
    proposal = AuthorizationProposal(
        tenant_id="tenant-alpha", companies=("Synthetic Alpha",),
        fields_by_doctype=(("Purchase Order", ("name", "company")),),
        sensitive_exclusions=("payroll", "personal records", "attachments"),
        historical_start=date(2025, 1, 1), historical_end=date(2025, 12, 31),
        max_requests=25, max_duration_seconds=1800, max_evidence_bytes=65536,
        retention_days=1, revocation_operator="Synthetic Operator",
    )
    with pytest.raises(LoginDenied):
        service.submit_authorization(
            analyst_secret, csrf_token=analyst_session.csrf_token, proposal=proposal,
        )
    proposal_id = service.submit_authorization(
        admin_secret, csrf_token=admin_session.csrf_token, proposal=proposal,
    )
    assert service.authorization_proposal(admin_secret, proposal_id=proposal_id)[
        "execution_authority"
    ] is False
    with pytest.raises(LoginDenied):
        service.authorization_proposal(analyst_secret, proposal_id=proposal_id)
    other = AuthorizationProposal(
        tenant_id="tenant-beta", companies=("Synthetic Beta",),
        fields_by_doctype=(("Purchase Order", ("name",)),),
        sensitive_exclusions=("payroll",), historical_start=date(2025, 1, 1),
        historical_end=date(2025, 12, 31), max_requests=1,
        max_duration_seconds=60, max_evidence_bytes=1024,
        retention_days=1, revocation_operator="Synthetic Operator",
    )
    with pytest.raises(LoginDenied, match="tenant differs"):
        service.submit_authorization(
            admin_secret, csrf_token=admin_session.csrf_token, proposal=other,
        )
    with registry._connection() as db:
        db.execute(
            "UPDATE authorization_proposals SET payload_json=? WHERE proposal_id=?",
            ('{"kind":"authorization_proposal","status":"pending_governed_validation",'
             '"execution_authority":false,"tenant_id":"tenant-alpha",'
             '"scope_sha256":"' + "0" * 64 + '"}', proposal_id),
        )
    with pytest.raises(LoginDenied, match="integrity"):
        service.authorization_proposal(admin_secret, proposal_id=proposal_id)
    login.logout(admin_secret, csrf_token=admin_session.csrf_token)
    with pytest.raises(LoginDenied):
        service.overview(admin_secret)


def _portal_request(app, path, *, method="GET", query="", cookie="", csrf="", body=None,
                    scheme="https", origin="https://command.example.invalid"):
    raw = b"" if body is None else json.dumps(body).encode()
    environ = {
        "wsgi.url_scheme": scheme, "HTTP_HOST": "command.example.invalid",
        "REQUEST_METHOD": method, "PATH_INFO": path, "QUERY_STRING": query,
        "HTTP_COOKIE": cookie, "HTTP_ORIGIN": origin,
        "HTTP_X_ORION_CSRF": csrf, "CONTENT_LENGTH": str(len(raw)),
        "CONTENT_TYPE": "application/json", "wsgi.input": io.BytesIO(raw),
    }
    recorded = {}

    def start(status, headers):
        recorded["status"] = status
        recorded["headers"] = headers

    recorded["body"] = b"".join(app(environ, start))
    return recorded


def test_https_portal_login_scope_logout_and_denial_clear_browser_state(context):
    registry, login, key, _alpha, _beta = context
    app = CustomerPortalHTTP(
        login=login, service=CustomerPortalService(login, registry),
        origin="https://command.example.invalid",
    )
    denied_http = _portal_request(app, "/customer/api/overview", scheme="http")
    assert denied_http["status"].startswith("403")
    start = _portal_request(app, "/customer/login", query="tenant=tenant-alpha")
    assert start["status"].startswith("302")
    auth_url = dict(start["headers"])["Location"]
    query = {key: value[0] for key, value in parse_qs(urlsplit(auth_url).query).items()}
    login._token_exchange.assertion = _assertion(key, "alpha-admin", query["nonce"])
    callback = _portal_request(
        app, "/callback", query=f"state={query['state']}&code=one-use",
    )
    assert callback["status"].startswith("303")
    cookies = [value for name, value in callback["headers"] if name == "Set-Cookie"]
    session_cookie = cookies[0].split(";", 1)[0]
    csrf_cookie = cookies[1].split(";", 1)[0]
    csrf = csrf_cookie.partition("=")[2]
    overview = _portal_request(app, "/customer/api/overview", cookie=session_cookie)
    assert overview["status"].startswith("200")
    assert json.loads(overview["body"])["tenant_id"] == "tenant-alpha"
    assert b"credential_ref" not in overview["body"]
    denied_switch = _portal_request(
        app, "/customer/api/switch", method="POST", cookie=session_cookie,
        csrf=csrf, body={"tenant_id": "tenant-beta"},
    )
    assert denied_switch["status"].startswith("401")
    assert b"tenant-alpha" not in denied_switch["body"]
    denied_enrollment = _portal_request(
        app, "/customer/api/erpnext/enroll", method="POST", cookie=session_cookie,
        csrf=csrf, body={"label": "Synthetic", "endpoint": "https://erp.example.invalid",
                         "company": "Synthetic Company", "api_key": "synthetic-key",
                         "api_secret": "synthetic-secret"},
    )
    assert denied_enrollment["status"].startswith("401")
    assert b"synthetic-secret" not in denied_enrollment["body"]
    logout = _portal_request(
        app, "/customer/api/logout", method="POST", cookie=session_cookie,
        csrf=csrf, body={},
    )
    assert logout["status"].startswith("200")
    assert _portal_request(app, "/customer/api/overview", cookie=session_cookie)[
        "status"
    ].startswith("401")


def test_https_portal_enrollment_response_never_exposes_secret_or_vault_reference(context):
    registry, login, key, _alpha, _beta = context
    session, _ = _complete(login, key, "alpha-admin")
    reviewed = ReviewedConnector(
        "erpnext", "originorion-connector-erpnext-discovery", "0.2.0rc4",
        "orion.discovery.erpnext_adapter:ERPNextDiscoveryAdapter",
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

    def store(tenant, connector, api_key, api_secret):
        reference = "vault:" + "v" * 28
        vault[reference] = (tenant, connector, api_key, api_secret)
        return reference

    app = CustomerPortalHTTP(
        login=login, service=CustomerPortalService(login, registry),
        origin="https://command.example.invalid", reviewed_connector=reviewed,
        schema_bytes=schema, schema_sha256=hashlib.sha256(schema).hexdigest(),
        approved_erp_origin="https://erp.example.com", vault_store=store,
        vault_revoke=lambda reference: vault.pop(reference, None),
    )
    response = _portal_request(
        app, "/customer/api/erpnext/enroll", method="POST",
        cookie=session.cookie.split(";", 1)[0], csrf=session.csrf_token,
        body={"label": "ERPNext", "endpoint": "https://erp.example.com",
              "company": "Synthetic Co", "api_key": "synthetic-key",
              "api_secret": "synthetic-secret"},
    )
    assert response["status"].startswith("202")
    assert b"synthetic-key" not in response["body"]
    assert b"synthetic-secret" not in response["body"]
    assert b"vault:" not in response["body"]
    assert json.loads(response["body"])["execution_authority"] is False
    overview = _portal_request(
        app, "/customer/api/overview", cookie=session.cookie.split(";", 1)[0],
    )
    assert json.loads(overview["body"])["connections"][0]["company"] == "Synthetic Co"
    assert b"vault:" not in overview["body"]
    assert b"synthetic-secret" not in registry._path.read_bytes()


def test_portal_read_denies_revocation_between_initial_and_locked_checks(context, monkeypatch):
    registry, login, key, _alpha, _beta = context
    session, _ = _complete(login, key, "alpha-admin")
    service = CustomerPortalService(login, registry)
    original = login._verified_session_identity

    def revoke_during_read(secret):
        identity = original(secret)
        registry.logout(identity)
        return identity

    monkeypatch.setattr(login, "_verified_session_identity", revoke_during_read)
    with pytest.raises(TenantDenied, match="membership"):
        service.overview(_secret(session))


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


def test_tenant_switch_requires_membership_and_csrf(context):
    registry, login, key, alpha, beta = context
    session, _ = _complete(login, key, "alpha-admin")
    secret = _secret(session)
    with pytest.raises(LoginDenied, match="CSRF"):
        login.switch_tenant(secret, tenant_id="tenant-beta", csrf_token="forged")
    with pytest.raises(LoginDenied, match="membership"):
        login.switch_tenant(secret, tenant_id="tenant-beta", csrf_token=session.csrf_token)
    assert login.authorize(secret).tenant_id == "tenant-alpha"
    registry.add_member(beta, tenant_id="tenant-beta", member=alpha, role="analyst")
    switched = login.switch_tenant(
        secret, tenant_id="tenant-beta", csrf_token=session.csrf_token,
    )
    assert switched.tenant_id == "tenant-beta"
    assert switched.role == "analyst"
    assert login.authorize(secret).tenant_id == "tenant-beta"
    with pytest.raises(LoginDenied, match="membership"):
        login.authorize(secret, required_role="administrator")
    login.logout(secret, csrf_token=session.csrf_token)
    with pytest.raises(LoginDenied, match="session"):
        login.switch_tenant(secret, tenant_id="tenant-alpha", csrf_token=session.csrf_token)


def test_erpnext_enrollment_stores_only_vault_ref_and_grants_no_authority(context):
    registry, login, key, _alpha, _beta = context
    session, _ = _complete(login, key, "alpha-admin")
    reviewed = ReviewedConnector(
        "erpnext", "originorion-connector-erpnext-discovery", "0.2.0rc4",
        "orion.discovery.erpnext_adapter:ERPNextDiscoveryAdapter",
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
    revoked = []

    def store(tenant, connector, api_key, api_secret):
        reference = "vault:" + "v" * 27 + str(len(vault) + len(revoked))
        vault[reference] = (tenant, connector, api_key, api_secret)
        return reference

    def revoke(reference):
        revoked.append(reference)
        vault.pop(reference, None)

    arguments = {
        "login": login, "registry": registry, "session_secret": _secret(session),
        "csrf_token": session.csrf_token, "reviewed": reviewed, "schema_bytes": schema,
        "schema_sha256": hashlib.sha256(schema).hexdigest(), "label": "Synthetic ERPNext",
        "endpoint": "https://erp.example.com", "approved_origin": "https://erp.example.com",
        "company": "Synthetic Co", "api_key": "synthetic-key",
        "api_secret": "synthetic-secret", "vault_store": store, "vault_revoke": revoke,
    }
    for bad in ("http://erp.example.com", "https://127.0.0.1", "https://169.254.169.254",
                "https://erp.example.com.evil.test", "https://erp.example.com/path"):
        with pytest.raises(EnrollmentDenied):
            enroll_erpnext(**(arguments | {"endpoint": bad}))
    assert not vault
    with pytest.raises(LoginDenied, match="CSRF"):
        enroll_erpnext(**(arguments | {"csrf_token": "forged"}))
    assert not vault
    analyst_session, _ = _complete(login, key, "alpha-analyst")
    with pytest.raises(LoginDenied, match="membership"):
        enroll_erpnext(**(arguments | {
            "session_secret": _secret(analyst_session),
            "csrf_token": analyst_session.csrf_token,
        }))
    with pytest.raises(ValueError, match="unreviewed connector schema"):
        enroll_erpnext(**(arguments | {"schema_bytes": schema + b" "}))
    assert not vault
    connection = enroll_erpnext(**arguments)
    assert connection.tenant_id == "tenant-alpha"
    assert connection.execution_allowed is False
    assert connection.credential_ref in vault
    assert "synthetic-secret" not in repr(connection)
    assert "synthetic-key" not in registry._path.read_bytes().decode("latin1")
    assert "synthetic-secret" not in registry._path.read_bytes().decode("latin1")
    with pytest.raises(EnrollmentDenied, match="reference reused"):
        enroll_erpnext(**(arguments | {
            "label": "Reused", "vault_store": lambda *_: connection.credential_ref,
        }))
    assert not revoked and connection.credential_ref in vault
    with pytest.raises(TenantDenied, match="already enrolled"):
        enroll_erpnext(**(arguments | {"label": "Synthetic ERPNext"}))
    assert len(revoked) == 1 and revoked[0] != connection.credential_ref
    assert connection.credential_ref in vault
    login.logout(_secret(session), csrf_token=session.csrf_token)
    with pytest.raises(LoginDenied):
        enroll_erpnext(**(arguments | {"label": "Other"}))


def test_customer_session_gates_one_real_synthetic_lineage(context, tmp_path, monkeypatch):
    _registry, login, key, _alpha, _beta = context
    session, _ = _complete(login, key, "alpha-admin")
    secret = _secret(session)
    now = datetime.now(UTC)
    date = now.date()
    tenant = "tenant-alpha"
    connector = SyntheticRecordConnector(
        tenant_id=tenant, source_id="synthetic-customer-source",
        dataset_id="synthetic-customer-dataset", records=(
            {"name": "SYN-CUSTOMER-1", "company": "Synthetic Co",
             "posting_date": date.isoformat(), "amount": 37},
        ),
    )
    fields = ("name", "company", "posting_date", "amount")
    window = ReviewedReadWindow(
        tenant_id=tenant, company="Synthetic Co", resource="Synthetic Invoice",
        fields=fields, date_field="posting_date", start=date, end=date,
        expires_at=now + timedelta(minutes=10),
    )
    grant = PilotAuthorization(
        authorization_id="synthetic-customer-read", source_id=connector.source_id,
        window=window, identity_field="name", company_field="company",
        provenance_source="synthetic-record-v1", evidence_kind=EvidenceKind.SOURCE,
        max_records=1,
    )
    request = PilotRequest(
        tenant_id=tenant, company="Synthetic Co", source_id=connector.source_id,
        resource="Synthetic Invoice", fields=fields, date_field="posting_date",
        start=date, end=date, max_records=1,
    )
    registration = discover(ReviewedConnector(
        "synthetic-records", "originorion-connector-synthetic", "0.2.0rc2",
        "orion_connector_synthetic:SyntheticRecordConnector",
    ))
    options = {
        "login": login, "session_secret": secret, "registration": registration,
        "connector": connector, "request": request,
        "authorization_id": grant.authorization_id,
        "lookup": lambda identity: grant if identity == grant.authorization_id else None,
        "run_id": uuid4(), "now": now,
    }
    wrong = tmp_path / "wrong"
    wrong.mkdir(mode=0o700)
    with pytest.raises(LoginDenied, match="tenant differs"):
        run_customer_synthetic_lineage(**(options | {
            "request": PilotRequest(
                tenant_id="tenant-beta", company="Synthetic Co",
                source_id=connector.source_id, resource="Synthetic Invoice",
                fields=fields, date_field="posting_date", start=date, end=date,
                max_records=1,
            ), "private_dir": wrong,
        }))
    assert not any(wrong.iterdir())
    actual = tmp_path / "actual"
    actual.mkdir(mode=0o700)
    result = run_customer_synthetic_lineage(**(options | {"private_dir": actual}))
    assert result.tenant_id == tenant
    assert result.view["observations_persisted"] == 1
    assert result.view["authority"]["execution_allowed"] is False
    assert (actual / "report.json").is_file()

    class RevokingConnector(SyntheticRecordConnector):
        def read(self, permit):
            observations = super().read(permit)
            login.logout(secret, csrf_token=session.csrf_token)
            return observations

    interrupted = tmp_path / "revoked-during-read"
    interrupted.mkdir(mode=0o700)
    revoking_connector = RevokingConnector(
        tenant_id=tenant, source_id=connector.source_id,
        dataset_id=connector.dataset_id, records=(
            {"name": "SYN-CUSTOMER-1", "company": "Synthetic Co",
             "posting_date": date.isoformat(), "amount": 37},
        ),
    )
    with pytest.raises(LoginDenied, match="session"):
        run_customer_synthetic_lineage(**(options | {
            "connector": revoking_connector, "private_dir": interrupted,
        }))
    assert not any(interrupted.iterdir())
    denied = tmp_path / "revoked"
    denied.mkdir(mode=0o700)
    with pytest.raises(LoginDenied, match="session"):
        run_customer_synthetic_lineage(**(options | {"private_dir": denied}))
    assert not any(denied.iterdir())

    original_write = lineage._write_private
    for stage in ("source.json", "evidence.json", "witness.json", "report.json"):
        fresh_session, _ = _complete(login, key, "alpha-admin")
        fresh_secret = _secret(fresh_session)
        staged = tmp_path / f"revoke-after-{stage}"
        staged.mkdir(mode=0o700)

        def revoke_after_write(path, body, *, checked_stage=stage,
                               checked_secret=fresh_secret,
                               checked_csrf=fresh_session.csrf_token):
            original_write(path, body)
            if path.name == checked_stage:
                login.logout(checked_secret, csrf_token=checked_csrf)

        monkeypatch.setattr(lineage, "_write_private", revoke_after_write)
        with pytest.raises(LoginDenied, match="session"):
            run_customer_synthetic_lineage(**(options | {
                "session_secret": fresh_secret, "private_dir": staged,
            }))
        assert not any(staged.iterdir()), stage
    monkeypatch.setattr(lineage, "_write_private", original_write)

    grant_session, _ = _complete(login, key, "alpha-admin")
    grant_secret = _secret(grant_session)
    active_grant = True
    grant_dir = tmp_path / "grant-revoked-after-source"
    grant_dir.mkdir(mode=0o700)

    def revoke_grant_after_source(path, body):
        nonlocal active_grant
        original_write(path, body)
        if path.name == "source.json":
            active_grant = False

    monkeypatch.setattr(lineage, "_write_private", revoke_grant_after_source)
    with pytest.raises(LoginDenied, match="authorization changed"):
        run_customer_synthetic_lineage(**(options | {
            "session_secret": grant_secret, "private_dir": grant_dir,
            "lookup": lambda identity: grant if active_grant and identity == grant.authorization_id
            else None,
        }))
    assert not any(grant_dir.iterdir())
    monkeypatch.setattr(lineage, "_write_private", original_write)

    # A fresh, authorized restart after a denied run uses a new empty run dir.
    restarted_session, _ = _complete(login, key, "alpha-admin")
    restarted = tmp_path / "authorized-restart"
    restarted.mkdir(mode=0o700)
    restarted_result = run_customer_synthetic_lineage(**(options | {
        "session_secret": _secret(restarted_session), "private_dir": restarted,
    }))
    assert restarted_result.binding_path.is_file()

    binding_entered = threading.Event()
    binding_release = threading.Event()
    race_session, _ = _complete(login, key, "alpha-admin")
    race_secret = _secret(race_session)
    race_dir = tmp_path / "commit-race"
    race_dir.mkdir(mode=0o700)

    def pause_binding(path, body):
        if path.name == "binding.json":
            binding_entered.set()
            assert binding_release.wait(5)
        original_write(path, body)

    monkeypatch.setattr(lineage, "_write_private", pause_binding)
    with ThreadPoolExecutor(max_workers=2) as pool:
        run = pool.submit(run_customer_synthetic_lineage, **(options | {
            "session_secret": race_secret, "private_dir": race_dir,
        }))
        assert binding_entered.wait(5)
        logout = pool.submit(login.logout, race_secret, csrf_token=race_session.csrf_token)
        time.sleep(0.05)
        assert not logout.done()
        binding_release.set()
        assert run.result(timeout=5).binding_path.is_file()
        assert "Max-Age=0" in logout.result(timeout=5)
    with pytest.raises(LoginDenied, match="session"):
        login.authorize(race_secret)
