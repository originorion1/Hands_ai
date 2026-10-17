"""Offline OIDC code-flow and tenant-bound browser session boundary.

The caller owns the reviewed HTTPS token exchange. No provider HTTP traffic or
customer endpoint is started by this module.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from base64 import urlsafe_b64encode
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlencode, urlsplit

from .oidc import OIDCVerifier, VerifiedIdentity
from .tenants import TenantAccess, TenantDenied, TenantRegistry

_LOGIN_SECONDS = 300
_SESSION_SECONDS = 300
_MAX_PENDING = 1000


class LoginDenied(ValueError):
    """OIDC attempt, session or CSRF authority is absent."""


@dataclass(frozen=True, slots=True)
class LoginStart:
    authorization_url: str
    expires_at: int


@dataclass(frozen=True, slots=True)
class BrowserSession:
    cookie: str
    csrf_token: str
    expires_at: int
    tenant_id: str


def _digest(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def _secure_url(value: str) -> bool:
    parts = urlsplit(value)
    return (parts.scheme == "https" and bool(parts.hostname)
            and parts.username is None and parts.password is None
            and not parts.query and not parts.fragment)


class CustomerLogin:
    def __init__(self, registry: TenantRegistry, verifier: OIDCVerifier, *,
                 authorization_endpoint: str, client_id: str, redirect_uri: str,
                 token_exchange: Callable[[str, str, str], str]):
        if (not isinstance(registry, TenantRegistry)
                or not isinstance(verifier, OIDCVerifier)
                or not _secure_url(authorization_endpoint)
                or not _secure_url(redirect_uri)
                or type(client_id) is not str or not client_id.strip()
                or len(client_id) > 128 or not callable(token_exchange)):
            raise ValueError("reviewed OIDC client configuration required")
        provider = urlsplit(verifier._issuer)
        endpoint = urlsplit(authorization_endpoint)
        if endpoint.netloc != provider.netloc:
            raise ValueError("authorization endpoint must match pinned issuer origin")
        self._registry = registry
        self._verifier = verifier
        self._authorization_endpoint = authorization_endpoint
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._token_exchange = token_exchange
        with registry._connection() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS login_attempts (
                    state_sha256 TEXT PRIMARY KEY, tenant_id TEXT NOT NULL,
                    nonce TEXT NOT NULL, pkce_verifier TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS browser_sessions (
                    session_sha256 TEXT PRIMARY KEY, csrf_sha256 TEXT NOT NULL,
                    tenant_id TEXT NOT NULL, issuer TEXT NOT NULL,
                    subject TEXT NOT NULL, idp_session_id TEXT NOT NULL,
                    expires_at INTEGER NOT NULL
                );
                """
            )

    def start(self, *, tenant_id: str) -> LoginStart:
        """Start a browser redirect; tenant is checked after IdP verification."""
        if type(tenant_id) is not str or not tenant_id or len(tenant_id) > 64:
            raise LoginDenied("invalid requested tenant")
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        pkce_verifier = secrets.token_urlsafe(32)
        challenge = urlsafe_b64encode(hashlib.sha256(pkce_verifier.encode()).digest()).rstrip(b"=").decode()
        expires_at = int(time.time()) + _LOGIN_SECONDS
        with self._registry._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM login_attempts WHERE expires_at<=?", (int(time.time()),))
            pending = db.execute("SELECT COUNT(*) FROM login_attempts").fetchone()[0]
            if pending >= _MAX_PENDING:
                raise LoginDenied("login attempt capacity reached")
            db.execute(
                "INSERT INTO login_attempts VALUES (?,?,?,?,?)",
                (_digest(state), tenant_id, nonce, pkce_verifier, expires_at),
            )
        query = urlencode({
            "response_type": "code", "client_id": self._client_id,
            "redirect_uri": self._redirect_uri, "scope": "openid",
            "state": state, "nonce": nonce, "code_challenge": challenge,
            "code_challenge_method": "S256",
        })
        return LoginStart(f"{self._authorization_endpoint}?{query}", expires_at)

    def finish(self, *, state: str, code: str) -> BrowserSession:
        """Consume state once, exchange code server-side and bind verified membership."""
        if (type(state) is not str or not 30 <= len(state) <= 128
                or type(code) is not str or not 1 <= len(code) <= 512
                or any(char in code for char in "\r\n")):
            raise LoginDenied("invalid code flow callback")
        with self._registry._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT tenant_id,nonce,pkce_verifier,expires_at "
                "FROM login_attempts WHERE state_sha256=?", (_digest(state),),
            ).fetchone()
            if row is None or row[3] <= time.time():
                raise LoginDenied("login state missing or expired")
            db.execute("DELETE FROM login_attempts WHERE state_sha256=?", (_digest(state),))
        tenant_id, nonce, pkce_verifier, _expires = row
        try:
            assertion = self._token_exchange(code, pkce_verifier, self._redirect_uri)
            identity = self._verifier.verify(assertion, expected_nonce=nonce)
            self._registry.authorize(identity, tenant_id=tenant_id)
        except (TenantDenied, ValueError, TypeError, OSError, TimeoutError) as exc:
            raise LoginDenied("identity or tenant membership denied") from exc
        secret = secrets.token_urlsafe(32)
        csrf = secrets.token_urlsafe(32)
        expires_at = min(identity.expires_at, int(time.time()) + _SESSION_SECONDS)
        if expires_at <= time.time():
            raise LoginDenied("identity session expired")
        with self._registry._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute(
                "INSERT INTO browser_sessions VALUES (?,?,?,?,?,?,?)",
                (_digest(secret), _digest(csrf), tenant_id, identity.issuer,
                 identity.subject, identity.session_id, expires_at),
            )
        return BrowserSession(
            cookie=(f"__Host-orion_session={secret}; Path=/; Secure; HttpOnly; "
                    f"SameSite=Lax; Max-Age={max(0, expires_at-int(time.time()))}"),
            csrf_token=csrf, expires_at=expires_at, tenant_id=tenant_id,
        )

    def authorize(self, session_secret: str, *, required_role: str = "analyst",
                  mutating: bool = False, csrf_token: str | None = None) -> TenantAccess:
        if (type(session_secret) is not str or not 30 <= len(session_secret) <= 128
                or type(mutating) is not bool):
            raise LoginDenied("authenticated session required")
        with self._registry._connection() as db:
            row = db.execute(
                "SELECT csrf_sha256,tenant_id,issuer,subject,idp_session_id,expires_at "
                "FROM browser_sessions WHERE session_sha256=?", (_digest(session_secret),),
            ).fetchone()
        if row is None or row[5] <= time.time():
            raise LoginDenied("authenticated session required")
        if mutating and (type(csrf_token) is not str or not 30 <= len(csrf_token) <= 128
                         or not hmac.compare_digest(
            _digest(csrf_token), row[0]
        )):
            raise LoginDenied("CSRF token required")
        identity = VerifiedIdentity(row[2], row[3], row[4], row[5])
        try:
            return self._registry.authorize(identity, tenant_id=row[1],
                                            required_role=required_role)
        except TenantDenied as exc:
            raise LoginDenied("authenticated membership denied") from exc

    def logout(self, session_secret: str, *, csrf_token: str) -> str:
        if type(session_secret) is not str or not 30 <= len(session_secret) <= 128:
            raise LoginDenied("authenticated session required")
        if type(csrf_token) is not str or not 30 <= len(csrf_token) <= 128:
            raise LoginDenied("CSRF token required")
        with self._registry._connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT issuer,idp_session_id,csrf_sha256 FROM browser_sessions "
                "WHERE session_sha256=?",
                (_digest(session_secret),),
            ).fetchone()
            if row is None:
                raise LoginDenied("authenticated session required")
            if not hmac.compare_digest(_digest(csrf_token), row[2]):
                raise LoginDenied("CSRF token required")
            db.execute("DELETE FROM browser_sessions WHERE session_sha256=?", (_digest(session_secret),))
            db.execute("INSERT OR IGNORE INTO revoked_sessions VALUES (?,?)", row[:2])
        return "__Host-orion_session=; Path=/; Secure; HttpOnly; SameSite=Lax; Max-Age=0"
