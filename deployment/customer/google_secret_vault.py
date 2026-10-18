"""Private Google Secret Manager callback adapter for the customer portal.

The host supplies a short-lived, scoped OAuth access-token callback. No cloud
identity, billing account or external request is created at construction.
"""

from __future__ import annotations

import base64
import binascii
import json
import re
import secrets
import ssl
from collections.abc import Callable
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import HTTPSHandler, ProxyHandler, Request, build_opener

from .oidc_client import _NoRedirect
from .tenants import is_canonical_tenant_slug

_PROJECT = re.compile(r"[a-z][a-z0-9-]{4,28}[a-z0-9]\Z")
_CONNECTOR = re.compile(r"[a-z][a-z0-9_-]{1,63}\Z")
_REFERENCE = re.compile(r"vault:(conn|oidc)-[A-Za-z0-9_-]{24,80}\Z")
_ORIGIN = "https://secretmanager.googleapis.com"
_LIMIT = 16 * 1024


class VaultUnavailable(RuntimeError):
    """The reviewed secret store did not complete the requested operation."""


class _Conflict(Exception):
    pass


def _crc32c(data: bytes) -> int:
    value = 0xffffffff
    for byte in data:
        value ^= byte
        for _ in range(8):
            value = (value >> 1) ^ (0x82f63b78 if value & 1 else 0)
    return value ^ 0xffffffff


class GoogleSecretManagerVault:
    """Store one immutable version per opaque reference in an isolated project.

    The caller must supply an identity with only reviewed Secret Manager rights.
    The `vault:` suffix becomes a secret ID under a private project prefix; no
    separate reference mapping or credential value enters ORION registry state.
    """

    def __init__(
        self, *, project_id: str, access_token: Callable[[], str],
        opener: Callable | None = None,
    ) -> None:
        if (type(project_id) is not str or not _PROJECT.fullmatch(project_id)
                or not callable(access_token)):
            raise ValueError("reviewed Secret Manager project and identity required")
        self._project = project_id
        self._access_token = access_token
        self._opener = opener or build_opener(
            ProxyHandler({}), _NoRedirect(), HTTPSHandler(context=ssl.create_default_context()),
        ).open

    def _resource(self, reference: str, *, kind: str) -> str:
        if (type(reference) is not str or not _REFERENCE.fullmatch(reference)
                or not reference.startswith(f"vault:{kind}-")):
            raise VaultUnavailable("vault reference denied")
        return f"projects/{self._project}/secrets/orion-preview-{reference[6:]}"

    def _request(self, method: str, path: str, body: dict | None = None) -> dict:
        url = f"{_ORIGIN}/v1/{path}"
        try:
            token = self._access_token()
            if type(token) is not str or not 20 <= len(token) <= 8192 or any(
                char in token for char in "\r\n"
            ):
                raise VaultUnavailable("vault identity unavailable")
            request = Request(
                url, data=None if body is None else json.dumps(body, separators=(",", ":")).encode(),
                method=method,
                headers={"Authorization": f"Bearer {token}", "Accept": "application/json",
                         "Content-Type": "application/json"},
            )
            with self._opener(request, timeout=10) as response:
                if response.geturl() != url or response.status not in (200, 201):
                    raise VaultUnavailable("vault response denied")
                data = response.read(_LIMIT + 1)
                if len(data) > _LIMIT:
                    raise VaultUnavailable("vault response denied")
                if not data and method == "DELETE":
                    return {}
                if response.headers.get_content_type() != "application/json":
                    raise VaultUnavailable("vault response denied")
                result = json.loads(data)
                if type(result) is not dict:
                    raise VaultUnavailable("vault response denied")
                return result
        except HTTPError as exc:
            if exc.code == 409 and method == "POST" and "?secretId=" in path:
                raise _Conflict from None
            if exc.code == 404 and method == "DELETE":
                return {}
            raise VaultUnavailable("vault operation denied") from None
        except (VaultUnavailable, _Conflict):
            raise
        except Exception:  # noqa: BLE001 - token provider and transport may contain secrets
            raise VaultUnavailable("vault unavailable") from None

    def store_connector(self, tenant: str, connector: str, key: str, secret: str) -> str:
        if (not is_canonical_tenant_slug(tenant) or type(connector) is not str
                or not _CONNECTOR.fullmatch(connector) or type(key) is not str
                or type(secret) is not str or not 1 <= len(key) <= 256
                or not 1 <= len(secret) <= 256):
            raise VaultUnavailable("connector credential denied")
        payload = json.dumps({"tenant": tenant, "connector": connector,
                              "api_key": key, "api_secret": secret}, separators=(",", ":")).encode()
        for _ in range(3):
            reference = f"vault:conn-{secrets.token_urlsafe(24)}"
            resource = self._resource(reference, kind="conn")
            secret_id = resource.rsplit("/", 1)[1]
            try:
                created = self._request(
                    "POST", f"projects/{self._project}/secrets?secretId={quote(secret_id)}",
                    {"replication": {"automatic": {}}},
                )
            except _Conflict:
                continue
            if created.get("name") != resource:
                raise VaultUnavailable("vault create response denied")
            try:
                version = self._request("POST", f"{resource}:addVersion", {
                    "payload": {"data": base64.b64encode(payload).decode(),
                                "dataCrc32c": str(_crc32c(payload))},
                })
                if version.get("name") != f"{resource}/versions/1":
                    raise VaultUnavailable("vault version response denied")
                return reference
            except VaultUnavailable:
                try:
                    self._request("DELETE", resource)
                except VaultUnavailable:
                    raise VaultUnavailable("vault cleanup required") from None
                raise
        raise VaultUnavailable("vault reference collision")

    def _read_payload(self, reference: str, *, kind: str) -> bytes:
        resource = self._resource(reference, kind=kind)
        result = self._request("GET", f"{resource}/versions/1:access")
        payload = result.get("payload")
        if (result.get("name") != f"{resource}/versions/1" or type(payload) is not dict
                or type(payload.get("data")) is not str
                or type(payload.get("dataCrc32c")) is not str):
            raise VaultUnavailable("vault payload denied")
        try:
            data = base64.b64decode(payload["data"], validate=True)
            if len(data) > 2048 or str(_crc32c(data)) != payload["dataCrc32c"]:
                raise ValueError("payload integrity")
            return data
        except (ValueError, OverflowError, binascii.Error):
            raise VaultUnavailable("vault payload denied") from None

    def read_connector(self, reference: str, *, tenant: str, connector: str) -> tuple[str, str]:
        if (not is_canonical_tenant_slug(tenant) or type(connector) is not str
                or not _CONNECTOR.fullmatch(connector)):
            raise VaultUnavailable("vault credential scope denied")
        data = self._read_payload(reference, kind="conn")
        try:
            value = json.loads(data)
            if (type(value) is not dict or set(value) != {"tenant", "connector", "api_key", "api_secret"}
                    or value["tenant"] != tenant or value["connector"] != connector
                    or type(value["api_key"]) is not str or type(value["api_secret"]) is not str):
                raise ValueError("credential scope")
            return value["api_key"], value["api_secret"]
        except (UnicodeError, ValueError, TypeError):
            raise VaultUnavailable("vault credential denied") from None

    def read_client_secret(self, reference: str) -> str:
        data = self._read_payload(reference, kind="oidc")
        try:
            value = data.decode("utf-8")
            if not 1 <= len(value) <= 256 or any(char in value for char in "\r\n\x00"):
                raise ValueError("invalid client secret")
            return value
        except (UnicodeError, ValueError):
            raise VaultUnavailable("vault client credential denied") from None

    def revoke_connector(self, reference: str) -> None:
        self._request("DELETE", self._resource(reference, kind="conn"))
