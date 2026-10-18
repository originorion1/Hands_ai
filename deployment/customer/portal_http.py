"""Review-gated HTTPS WSGI adapter for the customer portal.

Construction opens no listener. Operators must supply a reviewed IdP client,
vault callbacks, connector catalog and TLS route before mounting this app.
"""

from __future__ import annotations

import json
import sqlite3
import time
from collections.abc import Callable
from datetime import date
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from orion_connector_sdk import ReviewedConnector

from .catalog import _unique_object
from .enrollment import EnrollmentDenied, enroll_erpnext
from .login import CustomerLogin, LoginDenied
from .portal import AuthorizationProposal, PortalProposalDenied
from .service import CustomerPortalService
from .tenants import TenantDenied

_MAX_BODY = 16 * 1024
_COOKIE_NAME = "__Host-orion_session"
_CSRF_COOKIE = "__Host-orion_csrf"
_HEADERS = [
    ("Cache-Control", "no-store"), ("Pragma", "no-cache"),
    ("Referrer-Policy", "no-referrer"), ("X-Content-Type-Options", "nosniff"),
    ("X-Frame-Options", "DENY"),
    ("Content-Security-Policy", "default-src 'none'; script-src 'self'; style-src 'self'; connect-src 'self'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"),
]


class CustomerPortalHTTP:
    def __init__(
        self, *, login: CustomerLogin, service: CustomerPortalService,
        origin: str, reviewed_connector: ReviewedConnector | None = None,
        schema_bytes: bytes | None = None, schema_sha256: str | None = None,
        approved_erp_origin: str | None = None,
        vault_store: Callable | None = None, vault_revoke: Callable | None = None,
    ):
        target = urlsplit(origin)
        callback = urlsplit(login._redirect_uri)
        if (
            target.scheme != "https" or not target.hostname or target.path
            or target.query or target.fragment or target.username or target.password
            or target.port not in (None, 443)
            or callback.scheme != "https" or callback.netloc != target.netloc
            or not callback.path.startswith("/") or callback.query or callback.fragment
            or service.login is not login
        ):
            raise ValueError("reviewed HTTPS portal origin required")
        self.login = login
        self.service = service
        self.origin = origin
        self.callback_path = callback.path
        self._enrollment = (
            reviewed_connector, schema_bytes, schema_sha256,
            approved_erp_origin, vault_store, vault_revoke,
        )

    def __call__(self, environ, start_response):
        if (environ.get("wsgi.url_scheme") != "https"
                or environ.get("HTTP_HOST") != urlsplit(self.origin).netloc):
            return self._reply(start_response, "403 Forbidden", {"error": "portal origin denied"})
        path = environ.get("PATH_INFO", "")
        method = environ.get("REQUEST_METHOD", "")
        try:
            if environ.get("QUERY_STRING") and path not in {"/customer/login", self.callback_path}:
                raise LoginDenied("unexpected query denied")
            if path == "/customer/" and method == "GET":
                return self._asset(start_response, "portal.html", "text/html; charset=utf-8")
            if path == "/customer/portal.js" and method == "GET":
                return self._asset(start_response, "portal.js", "text/javascript; charset=utf-8")
            if path == "/customer/portal.css" and method == "GET":
                return self._asset(start_response, "portal.css", "text/css; charset=utf-8")
            if path == "/customer/login" and method == "GET":
                query = self._query(environ)
                if set(query) != {"tenant"}:
                    raise LoginDenied("requested tenant required")
                start = self.login.start(tenant_id=query["tenant"])
                return self._reply(start_response, "302 Found", b"", [("Location", start.authorization_url)])
            if path == self.callback_path and method == "GET":
                query = self._query(environ)
                if set(query) != {"state", "code"}:
                    raise LoginDenied("invalid OIDC callback")
                session = self.login.finish(state=query["state"], code=query["code"])
                return self._reply(start_response, "303 See Other", b"", [
                    ("Location", "/customer/"), ("Set-Cookie", session.cookie),
                    ("Set-Cookie", f"{_CSRF_COOKIE}={session.csrf_token}; Path=/; Secure; SameSite=Strict; Max-Age={max(0, session.expires_at - int(time.time()))}"),
                ])
            secret = self._cookie(environ)
            if path == "/customer/api/overview" and method == "GET":
                return self._reply(start_response, "200 OK", self.service.overview(secret))
            if method != "POST" or path not in {
                "/customer/api/logout", "/customer/api/switch",
                "/customer/api/invite", "/customer/api/remove",
                "/customer/api/authorization-proposals", "/customer/api/erpnext/enroll",
            }:
                return self._reply(start_response, "404 Not Found", {"error": "route unavailable"})
            if environ.get("HTTP_ORIGIN") != self.origin:
                raise LoginDenied("request origin denied")
            csrf = environ.get("HTTP_X_ORION_CSRF", "")
            body = self._body(environ)
            if path == "/customer/api/logout":
                if body:
                    raise LoginDenied("logout body denied")
                cleared = self.login.logout(secret, csrf_token=csrf)
                return self._reply(start_response, "200 OK", {"status": "logged_out"},
                                   [("Set-Cookie", cleared), ("Set-Cookie", self._clear_csrf())])
            if path == "/customer/api/switch":
                if set(body) != {"tenant_id"}:
                    raise LoginDenied("invalid tenant switch")
                access = self.login.switch_tenant(
                    secret, tenant_id=body["tenant_id"], csrf_token=csrf,
                )
                return self._reply(start_response, "200 OK", {
                    "tenant_id": access.tenant_id, "role": access.role,
                })
            if path == "/customer/api/invite":
                if set(body) != {"issuer", "subject", "role", "expires_at"}:
                    raise LoginDenied("invalid invitation")
                self.service.invite(secret, csrf_token=csrf, **body)
                return self._reply(start_response, "202 Accepted", {"status": "pending_invitee_login"})
            if path == "/customer/api/remove":
                if set(body) != {"issuer", "subject"}:
                    raise LoginDenied("invalid member removal")
                self.service.remove(secret, csrf_token=csrf, **body)
                return self._reply(start_response, "200 OK", {"status": "member_removed"})
            if path == "/customer/api/authorization-proposals":
                proposal = self._proposal(body)
                proposal_id = self.service.submit_authorization(
                    secret, csrf_token=csrf, proposal=proposal,
                )
                return self._reply(start_response, "202 Accepted", {
                    "proposal_id": proposal_id, "status": "pending_governed_validation",
                    "execution_authority": False,
                })
            if set(body) != {"label", "endpoint", "company", "api_key", "api_secret"}:
                raise EnrollmentDenied("invalid connector enrollment")
            if any(value is None for value in self._enrollment):
                raise EnrollmentDenied("reviewed connector enrollment unavailable")
            reviewed, schema, digest, approved, vault_store, vault_revoke = self._enrollment
            connection = enroll_erpnext(
                login=self.login, registry=self.service.registry,
                session_secret=secret, csrf_token=csrf, reviewed=reviewed,
                schema_bytes=schema, schema_sha256=digest,
                approved_origin=approved, vault_store=vault_store,
                vault_revoke=vault_revoke, **body,
            )
            return self._reply(start_response, "202 Accepted", {
                "tenant_id": connection.tenant_id, "connector": connection.connector,
                "status": "enrolled_pending_grant", "execution_authority": False,
            })
        except (LoginDenied, TenantDenied, EnrollmentDenied, PortalProposalDenied,
                TypeError, ValueError, KeyError):
            return self._reply(start_response, "401 Unauthorized", {
                "error": "request denied",
            }, [("Set-Cookie", f"{_COOKIE_NAME}=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0"),
                ("Set-Cookie", self._clear_csrf())])
        except (OSError, RuntimeError, sqlite3.DatabaseError):
            # Vault and adapter failures stay server-side; never interpolate
            # submitted credential values into an HTTP response or error log.
            return self._reply(start_response, "503 Service Unavailable", {
                "error": "service unavailable",
            })

    @staticmethod
    def _query(environ) -> dict[str, str]:
        raw = parse_qs(environ.get("QUERY_STRING", ""), keep_blank_values=True, strict_parsing=True)
        if any(len(values) != 1 for values in raw.values()):
            raise LoginDenied("ambiguous query denied")
        return {key: values[0] for key, values in raw.items()}

    @staticmethod
    def _cookie(environ) -> str:
        values = []
        for part in environ.get("HTTP_COOKIE", "").split(";"):
            name, separator, value = part.strip().partition("=")
            if separator and name == _COOKIE_NAME:
                values.append(value)
        if len(values) != 1:
            raise LoginDenied("authenticated cookie required")
        return values[0]

    @staticmethod
    def _clear_csrf() -> str:
        return f"{_CSRF_COOKIE}=; Path=/; Secure; SameSite=Strict; Max-Age=0"

    @staticmethod
    def _body(environ) -> dict:
        try:
            length = int(environ.get("CONTENT_LENGTH", "0"))
        except ValueError as exc:
            raise LoginDenied("invalid body length") from exc
        if length < 0 or length > _MAX_BODY:
            raise LoginDenied("body limit exceeded")
        if length == 0:
            return {}
        if environ.get("CONTENT_TYPE", "").split(";", 1)[0] != "application/json":
            raise LoginDenied("JSON request required")
        data = environ["wsgi.input"].read(length)
        if len(data) != length:
            raise LoginDenied("incomplete request")
        value = json.loads(data, object_pairs_hook=_unique_object)
        if type(value) is not dict:
            raise LoginDenied("JSON object required")
        return value

    @staticmethod
    def _proposal(body: dict) -> AuthorizationProposal:
        expected = {
            "tenant_id", "companies", "fields_by_doctype", "sensitive_exclusions",
            "historical_start", "historical_end", "max_requests",
            "max_duration_seconds", "max_evidence_bytes", "retention_days",
            "revocation_operator", "read_only",
        }
        if set(body) != expected:
            raise PortalProposalDenied("invalid authorization proposal")
        if (
            type(body["companies"]) is not list
            or type(body["fields_by_doctype"]) is not list
            or type(body["sensitive_exclusions"]) is not list
            or any(type(row) is not list or len(row) != 2 or type(row[1]) is not list
                   for row in body["fields_by_doctype"])
        ):
            raise PortalProposalDenied("invalid authorization proposal shape")
        return AuthorizationProposal(
            tenant_id=body["tenant_id"], companies=tuple(body["companies"]),
            fields_by_doctype=tuple(
                (name, tuple(fields)) for name, fields in body["fields_by_doctype"]
            ),
            sensitive_exclusions=tuple(body["sensitive_exclusions"]),
            historical_start=date.fromisoformat(body["historical_start"]),
            historical_end=date.fromisoformat(body["historical_end"]),
            max_requests=body["max_requests"],
            max_duration_seconds=body["max_duration_seconds"],
            max_evidence_bytes=body["max_evidence_bytes"],
            retention_days=body["retention_days"],
            revocation_operator=body["revocation_operator"],
            read_only=body["read_only"],
        )

    @staticmethod
    def _reply(start_response, status: str, body: dict | bytes,
               extra: list[tuple[str, str]] | None = None):
        payload = body if isinstance(body, bytes) else json.dumps(body, sort_keys=True).encode()
        content_type = "application/octet-stream" if isinstance(body, bytes) else "application/json; charset=utf-8"
        start_response(status, [*_HEADERS, ("Content-Type", content_type),
                                ("Content-Length", str(len(payload))), *(extra or [])])
        return [payload]

    @staticmethod
    def _asset(start_response, name: str, content_type: str):
        payload = Path(__file__).with_name(name).read_bytes()
        start_response("200 OK", [*_HEADERS, ("Content-Type", content_type),
                                  ("Content-Length", str(len(payload)))])
        return [payload]
