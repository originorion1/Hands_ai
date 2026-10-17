"""Deployment readiness distinguishes HTTP health from report-host availability."""

from __future__ import annotations

import os
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler

from .report import probe_backend
from .request_target import is_api_target


class ReadinessHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _reply(self, status: HTTPStatus, body: bytes, *, head: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _get(self, *, head: bool = False) -> None:
        if not is_api_target(self.path, "/api/ready"):
            self._reply(HTTPStatus.NOT_FOUND, b'{"error":"not_found"}\n', head=head)
            return
        ready = probe_backend(
            os.environ.get("ORION_REPORT_BACKEND_URL"),
            service_token=os.environ.get("ORION_REPORT_SERVICE_TOKEN"),
        )
        status = HTTPStatus.OK if ready else HTTPStatus.SERVICE_UNAVAILABLE
        body = b'{"ready":true,"execution_allowed":false}\n' if ready else (
            b'{"ready":false,"execution_allowed":false}\n'
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
