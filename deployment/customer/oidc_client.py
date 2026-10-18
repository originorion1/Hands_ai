"""Reviewed OIDC authorization-code exchange for the customer session boundary.

An operator pins the issuer, token endpoint and client identity after provider
review. Construction performs no network access; only a one-use login callback
invokes the exact HTTPS endpoint. PKCE remains mandatory in CustomerLogin.
"""

from __future__ import annotations

import json
import ssl
from collections.abc import Callable
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener


class OIDCTokenExchangeDenied(ValueError):
    """The reviewed token exchange could not return one valid ID token."""


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, fp, code, msg, headers, newurl):
        return None


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise OIDCTokenExchangeDenied("duplicate token response field")
        result[key] = value
    return result


class ReviewedOIDCTokenExchange:
    def __init__(
        self, *, issuer: str, token_endpoint: str, client_id: str,
        redirect_uri: str, client_secret_reference: str | None = None,
        resolve_secret: Callable[[str], str] | None = None,
        opener: Callable | None = None,
    ) -> None:
        provider = urlsplit(issuer)
        endpoint = urlsplit(token_endpoint)
        callback = urlsplit(redirect_uri)
        if (provider.scheme != "https" or not provider.hostname
                or provider.username or provider.password or provider.query or provider.fragment
                or endpoint.scheme != "https" or endpoint.netloc != provider.netloc
                or not endpoint.path.startswith("/") or endpoint.path == "/"
                or endpoint.query or endpoint.fragment
                or callback.scheme != "https" or not callback.hostname
                or callback.username or callback.password or callback.query or callback.fragment
                or type(client_id) is not str or not 1 <= len(client_id) <= 128
                or bool(client_secret_reference) != bool(resolve_secret)
                or (client_secret_reference is not None and
                    (type(client_secret_reference) is not str
                     or not client_secret_reference.startswith("vault:")
                     or len(client_secret_reference) > 256))):
            raise ValueError("reviewed OIDC provider configuration required")
        self._endpoint = token_endpoint
        self._client_id = client_id
        self._redirect_uri = redirect_uri
        self._secret_reference = client_secret_reference
        self._resolve_secret = resolve_secret
        self._opener = opener or build_opener(
            _NoRedirect(), HTTPSHandler(context=ssl.create_default_context()),
        ).open

    def __call__(self, code: str, verifier: str, redirect_uri: str) -> str:
        if (type(code) is not str or not 1 <= len(code) <= 512
                or any(char in code for char in "\r\n")
                or type(verifier) is not str or not 43 <= len(verifier) <= 128
                or redirect_uri != self._redirect_uri):
            raise OIDCTokenExchangeDenied("invalid one-use code exchange")
        fields = {
            "grant_type": "authorization_code", "code": code,
            "code_verifier": verifier, "redirect_uri": self._redirect_uri,
            "client_id": self._client_id,
        }
        if self._secret_reference is not None:
            secret = self._resolve_secret(self._secret_reference)
            if type(secret) is not str or not secret:
                raise OIDCTokenExchangeDenied("client credential unavailable")
            fields["client_secret"] = secret
        request = Request(
            self._endpoint, data=urlencode(fields).encode("utf-8"), method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded",
                     "Accept": "application/json"},
        )
        try:
            with self._opener(request, timeout=10) as response:
                if (response.geturl() != self._endpoint or response.status != 200
                        or response.headers.get_content_type() != "application/json"):
                    raise OIDCTokenExchangeDenied("token endpoint response denied")
                body = response.read(16 * 1024 + 1)
        except (HTTPError, URLError, OSError, TimeoutError) as exc:
            raise OIDCTokenExchangeDenied("token endpoint unavailable") from exc
        if len(body) > 16 * 1024:
            raise OIDCTokenExchangeDenied("token response too large")
        try:
            payload = json.loads(body, object_pairs_hook=_unique_object)
        except (TypeError, ValueError, UnicodeError) as exc:
            raise OIDCTokenExchangeDenied("invalid token response") from exc
        assertion = payload.get("id_token") if type(payload) is dict else None
        if type(assertion) is not str or not 30 <= len(assertion) <= 16 * 1024:
            raise OIDCTokenExchangeDenied("ID token unavailable")
        return assertion
