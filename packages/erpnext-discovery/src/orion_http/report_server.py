"""Loopback report service for a separately managed HTTPS reverse proxy.

This service reads one private, digest-bound aggregate. It has no ERP connector,
write route, token issuer, or public bind option. The reverse proxy supplies TLS;
the independently issued token is verified again on every report request.
"""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from orion.presentation.report_interface import BoundReportSource, ReportInterfaceError

from orion.presentation.report_auth import ReportAccessPolicy, ReportAuthenticationError


class AuthenticatedReportServer(HTTPServer):
    source: BoundReportSource
    policy: ReportAccessPolicy
    policy_path: Path | None

    def current_policy(self) -> ReportAccessPolicy:
        if self.policy_path is None:
            return self.policy
        policy = ReportAccessPolicy.from_private_file(self.policy_path)
        if policy.tenant_id != self.source.tenant_id:
            raise ReportAuthenticationError("report access policy unavailable")
        return policy


class AuthenticatedReportHandler(BaseHTTPRequestHandler):
    server: AuthenticatedReportServer
    server_version = "ORION"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _reply(self, status: HTTPStatus, body: bytes, *, head: bool = False) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _get(self, *, head: bool = False) -> None:
        port = self.server.server_port
        hosts = self.headers.get_all("Host") or []
        if len(hosts) != 1 or hosts[0] not in {f"127.0.0.1:{port}", f"localhost:{port}"}:
            self._reply(HTTPStatus.FORBIDDEN, b'{"error":"access_denied"}\n', head=head)
            return
        target = urlsplit(self.path)
        if target.query or target.fragment:
            self._reply(HTTPStatus.NOT_FOUND, b'{"error":"not_found"}\n', head=head)
            return
        if target.path in {"/health", "/ready"}:
            if target.path == "/ready":
                try:
                    self.server.source.read_view()
                    self.server.current_policy()
                except (ReportInterfaceError, ReportAuthenticationError):
                    self._reply(HTTPStatus.SERVICE_UNAVAILABLE, b'{"ready":false}\n', head=head)
                    return
            self._reply(HTTPStatus.OK, b'{"ready":true,"execution_allowed":false}\n', head=head)
            return
        if target.path != "/api/v1/report":
            self._reply(HTTPStatus.NOT_FOUND, b'{"error":"not_found"}\n', head=head)
            return
        try:
            authorizations = self.headers.get_all("Authorization") or []
            policy = self.server.current_policy()
        except ReportAuthenticationError:
            self._reply(
                HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"report_unavailable"}\n', head=head
            )
            return
        try:
            policy.authorize(
                authorizations[0] if len(authorizations) == 1 else None,
                report_tenant=self.server.source.tenant_id,
            )
        except ReportAuthenticationError:
            self._reply(HTTPStatus.UNAUTHORIZED, b'{"error":"access_denied"}\n', head=head)
            return
        try:
            view = self.server.source.read_view()
        except ReportInterfaceError:
            self._reply(
                HTTPStatus.SERVICE_UNAVAILABLE, b'{"error":"report_unavailable"}\n', head=head
            )
            return
        body = (json.dumps(view, sort_keys=True, separators=(",", ":")) + "\n").encode()
        self._reply(HTTPStatus.OK, body, head=head)

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


def create_server(
    source: BoundReportSource, policy: ReportAccessPolicy | Path, *, port: int
) -> AuthenticatedReportServer:
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    policy_path = policy if isinstance(policy, Path) else None
    active_policy = ReportAccessPolicy.from_private_file(policy) if policy_path else policy
    if source.tenant_id != active_policy.tenant_id:
        raise ReportAuthenticationError("report access policy unavailable")
    source.read_view()
    server = AuthenticatedReportServer(("127.0.0.1", port), AuthenticatedReportHandler)
    server.source = source
    server.policy = active_policy
    server.policy_path = policy_path
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve one authenticated aggregate report on loopback")
    parser.add_argument("--binding", required=True, type=Path)
    parser.add_argument("--access-policy", required=True, type=Path)
    parser.add_argument("--port", required=True, type=int)
    arguments = parser.parse_args(argv)
    source = BoundReportSource.from_private_binding(arguments.binding)
    server = create_server(source, arguments.access_policy, port=arguments.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
