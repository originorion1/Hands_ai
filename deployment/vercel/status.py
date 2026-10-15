"""Bounded Stage A status contract for Vercel's HTTP functions."""

from __future__ import annotations

import json
import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler
from urllib.parse import urlsplit

from .report import _upstream_url

STATUS = {
    "interface_version": "orion.deployment-status.v1",
    "stage": "A",
    "http_api": "healthy",
    "report_service": "not_connected",
    "governed_worker": "not_connected",
    "persistent_storage": "not_connected",
    "live_pilot_ready": False,
    "execution_allowed": False,
}


class StatusHandler(BaseHTTPRequestHandler):
    """Fixed status only; the persistent runtime is never imported here."""

    route = ""

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _reply(self, code: HTTPStatus, body: bytes, *, head: bool = False) -> None:
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _get(self, *, head: bool = False) -> None:
        target = urlsplit(self.path)
        if target.path != self.route or target.query or target.fragment:
            self._reply(HTTPStatus.NOT_FOUND, b'{"error":"not_found"}\n', head=head)
            return
        state = dict(STATUS)
        if _upstream_url(os.environ.get("ORION_REPORT_BACKEND_URL")) is not None:
            state["report_service"] = "requires_authentication"
        body = (json.dumps(state, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self._reply(HTTPStatus.OK, body, head=head)

    def do_GET(self) -> None:
        self._get()

    def do_HEAD(self) -> None:
        self._get(head=True)

    def do_POST(self) -> None:
        self._reply(HTTPStatus.METHOD_NOT_ALLOWED, b'{"error":"method_not_allowed"}\n')

    do_DELETE = do_POST
    do_PATCH = do_POST
    do_PUT = do_POST
    do_OPTIONS = do_POST
