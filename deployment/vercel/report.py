"""Fail-closed public report boundary until a reviewed authenticated service exists.

Stage B scaffold: a public Vercel route must not re-use the loopback-only
private-file report server. Never ship report values or tenant identifiers here.
"""

from __future__ import annotations

from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit


class ReportHandler(BaseHTTPRequestHandler):
    """Read-only unavailable response; no imports of runtime or file-backed reports."""

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _reply(self, code: HTTPStatus, *, head: bool = False) -> None:
        body = b'{"error":"report_service_not_connected","execution_allowed":false}\n'
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
            self._reply(HTTPStatus.NOT_FOUND, head=head)
            return
        self._reply(HTTPStatus.SERVICE_UNAVAILABLE, head=head)

    def do_GET(self) -> None:
        self._get()

    def do_HEAD(self) -> None:
        self._get(head=True)

    def do_POST(self) -> None:
        self._reply(HTTPStatus.METHOD_NOT_ALLOWED)

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST
    do_OPTIONS = do_POST
