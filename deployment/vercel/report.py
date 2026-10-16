"""Constrained same-origin proxy to an independently authenticated report host.

No private report, ERP credential, tenant binding, or worker runs in Vercel.
The host verifies caller tokens and private report custody on every request.
"""

from __future__ import annotations

import http.client
import json
import os
import re
import ssl
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

_MAX_REPORT_BYTES = 64 * 1024
_MAX_TOKEN_BYTES = 4096
_SAFE_CATEGORY = re.compile(r"[a-z_]{1,64}\Z")
_SAFE_TIMESTAMP = re.compile(r"[0-9T:+.\-Z]{1,64}\Z")


def _unique_object(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate report field")
        result[key] = value
    return result


def _counter(value: object) -> bool:
    return type(value) is int and value >= 0


def _validated_view(body: bytes) -> bytes:
    """Validate the public transport envelope before returning upstream bytes."""
    value = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
    if type(value) is not dict or set(value) != {
        "authority", "cycles", "failures", "interface_version", "metadata",
        "observations_persisted", "report_kind", "session_ended_at",
        "session_started_at", "stop_reason", "studies",
    }:
        raise ValueError("invalid report envelope")
    if (
        value["interface_version"] != "orion.readonly-report.v1"
        or value["report_kind"] != "completed_aggregate"
        or type(value["authority"]) is not dict
        or set(value["authority"]) != {
            "erp_writes", "execution_allowed", "promotion_allowed", "recommendation_allowed"
        }
        or type(value["authority"]["erp_writes"]) is not int
        or value["authority"]["erp_writes"] != 0
        or any(value["authority"][name] is not False for name in (
            "execution_allowed", "promotion_allowed", "recommendation_allowed"
        ))
        or not _counter(value["observations_persisted"])
        or type(value["stop_reason"]) is not str
        or _SAFE_CATEGORY.fullmatch(value["stop_reason"]) is None
    ):
        raise ValueError("invalid report authority")
    for name, keys in (
        ("cycles", {"attempted", "completed"}),
        ("metadata", {"budget", "used"}),
        ("studies", {"budget", "used"}),
    ):
        item = value[name]
        if type(item) is not dict or set(item) != keys or not all(_counter(v) for v in item.values()):
            raise ValueError("invalid report counters")
    if (
        value["cycles"]["completed"] > value["cycles"]["attempted"]
        or value["metadata"]["used"] > value["metadata"]["budget"]
        or value["studies"]["used"] > value["studies"]["budget"]
    ):
        raise ValueError("inconsistent report counters")
    for name in ("session_started_at", "session_ended_at"):
        if type(value[name]) is not str or _SAFE_TIMESTAMP.fullmatch(value[name]) is None:
            raise ValueError("invalid report timestamp")
    failures = value["failures"]
    if type(failures) is not list or len(failures) > 64:
        raise ValueError("invalid report failures")
    for item in failures:
        if (
            type(item) is not dict
            or set(item) != {"category", "count"}
            or type(item["category"]) is not str
            or _SAFE_CATEGORY.fullmatch(item["category"]) is None
            or not _counter(item["count"])
        ):
            raise ValueError("invalid report failure category")
    return body


def _upstream_url(value: str | None) -> tuple[str, int] | None:
    if not value:
        return None
    try:
        target = urlsplit(value)
        port = target.port
    except ValueError:
        return None
    if (
        target.scheme != "https"
        or not target.hostname
        or target.username is not None
        or target.password is not None
        or target.query
        or target.fragment
        or target.path != "/api/v1/report"
        or port == 0
    ):
        return None
    return target.hostname, port or 443


def fetch_report(
    authorization: str,
    *,
    backend_url: str | None,
    connection_factory: type[http.client.HTTPSConnection] = http.client.HTTPSConnection,
) -> tuple[HTTPStatus, bytes]:
    upstream = _upstream_url(backend_url)
    if upstream is None:
        return HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"report_service_not_connected"}\n'
    host, port = upstream
    try:
        connection = connection_factory(host, port=port, timeout=5, context=ssl.create_default_context())
        try:
            connection.request(
                "GET", "/api/v1/report",
                headers={"Authorization": authorization, "Accept": "application/json"},
            )
            response = connection.getresponse()
            status = response.status
            body = response.read(_MAX_REPORT_BYTES + 1)
        finally:
            connection.close()
    except (OSError, ssl.SSLError, http.client.HTTPException, TimeoutError):
        return HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"report_service_unavailable"}\n'
    if status in {HTTPStatus.UNAUTHORIZED, HTTPStatus.FORBIDDEN}:
        return HTTPStatus.UNAUTHORIZED, b'{"error":"access_denied"}\n'
    if status != HTTPStatus.OK or len(body) > _MAX_REPORT_BYTES:
        return HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"report_service_unavailable"}\n'
    try:
        return HTTPStatus.OK, _validated_view(body)
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError, ValueError):
        return HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"report_service_invalid"}\n'


def probe_backend(
    backend_url: str | None,
    *,
    connection_factory: type[http.client.HTTPSConnection] = http.client.HTTPSConnection,
) -> bool:
    """Check the separate report host without credentials or report contents."""
    upstream = _upstream_url(backend_url)
    if upstream is None:
        return False
    host, port = upstream
    try:
        connection = connection_factory(host, port=port, timeout=3, context=ssl.create_default_context())
        try:
            connection.request("GET", "/ready", headers={"Accept": "application/json"})
            response = connection.getresponse()
            body = response.read(257)
            return (
                response.status == HTTPStatus.OK
                and len(body) <= 256
                and json.loads(body.decode("utf-8"), object_pairs_hook=_unique_object)
                == {"ready": True, "execution_allowed": False}
            )
        finally:
            connection.close()
    except (OSError, ssl.SSLError, http.client.HTTPException, TimeoutError,
            UnicodeDecodeError, json.JSONDecodeError, ValueError):
        return False


class ReportHandler(BaseHTTPRequestHandler):
    """Read-only route; caller token is forwarded only to the fixed HTTPS host."""

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _reply(self, code: HTTPStatus, body: bytes, *, head: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _get(self, *, head: bool = False) -> None:
        target = urlsplit(self.path)
        if target.path != "/api/v1/report" or target.query or target.fragment:
            self._reply(HTTPStatus.NOT_FOUND, b'{"error":"not_found"}\n', head=head)
            return
        values = self.headers.get_all("Authorization") or []
        authorization = values[0] if len(values) == 1 else None
        if (
            type(authorization) is not str
            or not authorization.startswith("Bearer ")
            or not 7 < len(authorization) <= _MAX_TOKEN_BYTES + 7
        ):
            self._reply(HTTPStatus.UNAUTHORIZED, b'{"error":"access_denied"}\n', head=head)
            return
        status, body = fetch_report(
            authorization, backend_url=os.environ.get("ORION_REPORT_BACKEND_URL")
        )
        self._reply(status, body, head=head)

    def do_GET(self) -> None:
        self._get()

    def do_HEAD(self) -> None:
        self._get(head=True)

    def do_POST(self) -> None:
        self._reply(HTTPStatus.METHOD_NOT_ALLOWED, b'{"error":"method_not_allowed"}\n')

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST
    do_OPTIONS = do_POST
