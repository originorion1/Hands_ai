"""Loopback-only HTTP adapter for the read-only report interface."""

from __future__ import annotations

import argparse
import json
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, HTTPServer
from importlib.resources import files
from pathlib import Path
from urllib.parse import urlsplit

from .report_interface import BoundReportSource, ReportInterfaceError

_ASSETS = {
    "/": ("index.html", "text/html; charset=utf-8"),
    "/2036.css": ("2036.css", "text/css; charset=utf-8"),
    "/app.js": ("app.js", "text/javascript; charset=utf-8"),
    "/motion.css": ("motion.css", "text/css; charset=utf-8"),
    "/report-link.js": ("report-link.js", "text/javascript; charset=utf-8"),
}


class ReadOnlyReportServer(HTTPServer):
    source: BoundReportSource


class ReadOnlyReportHandler(BaseHTTPRequestHandler):
    server: ReadOnlyReportServer
    server_version = "ORION"
    sys_version = ""

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _security_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; "
            "style-src 'self' 'sha256-6t/nuACIMNl9PqmQ8gwevnFiM9NXP1B2XanbnUseGmg='; "
            "object-src 'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'",
        )
        self.send_header("Referrer-Policy", "no-referrer")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")

    def _reply(self, status: HTTPStatus, body: bytes, content_type: str) -> None:
        self.send_response(status)
        self._security_headers()
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _request_is_local_page(self) -> bool:
        port = self.server.server_port
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        return (
            self.headers.get("Host") in allowed_hosts
            and self.headers.get("Sec-Fetch-Site", "same-origin")
            in {"same-origin", "same-site", "none"}
        )

    def do_GET(self) -> None:
        if not self._request_is_local_page():
            self._reply(HTTPStatus.FORBIDDEN, b"request denied\n", "text/plain; charset=utf-8")
            return
        target = urlsplit(self.path)
        if target.query or target.fragment:
            self._reply(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")
            return
        if target.path == "/api/v1/report":
            try:
                payload = self.server.source.read_view()
            except ReportInterfaceError:
                self._reply(
                    HTTPStatus.SERVICE_UNAVAILABLE,
                    b'{"error":"bound report unavailable"}\n',
                    "application/json; charset=utf-8",
                )
                return
            body = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
            self._reply(HTTPStatus.OK, body, "application/json; charset=utf-8")
            return
        asset = _ASSETS.get(target.path)
        if asset is None:
            self._reply(HTTPStatus.NOT_FOUND, b"not found\n", "text/plain; charset=utf-8")
            return
        name, content_type = asset
        body = files("orion.presentation").joinpath("static", name).read_bytes()
        self._reply(HTTPStatus.OK, body, content_type)

    def do_HEAD(self) -> None:
        self._reply(HTTPStatus.METHOD_NOT_ALLOWED, b"", "text/plain; charset=utf-8")

    def do_POST(self) -> None:
        self._reply(HTTPStatus.METHOD_NOT_ALLOWED, b"method not allowed\n", "text/plain; charset=utf-8")

    do_DELETE = do_POST
    do_OPTIONS = do_POST
    do_PATCH = do_POST
    do_PUT = do_POST


def create_server(source: BoundReportSource, *, port: int) -> ReadOnlyReportServer:
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    server = ReadOnlyReportServer(("127.0.0.1", port), ReadOnlyReportHandler)
    server.source = source
    return server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Serve one bound aggregate report locally")
    parser.add_argument("--binding", required=True, type=Path)
    parser.add_argument("--port", default=8765, type=int)
    arguments = parser.parse_args(argv)
    source = BoundReportSource.from_private_binding(arguments.binding)
    server = create_server(source, port=arguments.port)
    print(f"ORION read-only report UI: http://127.0.0.1:{server.server_port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
