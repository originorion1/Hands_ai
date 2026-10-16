"""Isolated local router for exercising the Vercel UI and HTTP handlers together.

This development runner serves the built static files and the same API handler
methods used by Vercel. It has no ERP connector and binds loopback only.
"""

from __future__ import annotations

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from .vercel.build_static import ASSETS, OUTPUT
from .vercel.readiness import ReadinessHandler
from .vercel.report import ReportHandler
from .vercel.status import StatusHandler

_TYPES = {
    ".html": "text/html; charset=utf-8",
    ".css": "text/css; charset=utf-8",
    ".js": "text/javascript; charset=utf-8",
}


class LocalApplicationHandler(BaseHTTPRequestHandler):
    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _reply(self, status: HTTPStatus, body: bytes, *, head: bool = False) -> None:
        StatusHandler._reply(self, status, body, head=head)

    def _static(self, *, head: bool = False) -> None:
        target = urlsplit(self.path)
        name = "index.html" if target.path == "/" else target.path.removeprefix("/")
        if target.query or target.fragment or name not in ASSETS:
            self._reply(HTTPStatus.NOT_FOUND, b'{"error":"not_found"}\n', head=head)
            return
        body = (OUTPUT / name).read_bytes()
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", _TYPES[Path(name).suffix])
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if not head:
            self.wfile.write(body)

    def _dispatch(self, *, head: bool = False) -> None:
        path = urlsplit(self.path).path
        if path in {"/api/health", "/api/v1/status"}:
            self.route = path
            StatusHandler._get(self, head=head)
        elif path == "/api/ready":
            ReadinessHandler._get(self, head=head)
        elif path == "/api/v1/report":
            ReportHandler._get(self, head=head)
        else:
            self._static(head=head)

    def do_GET(self) -> None:
        self._dispatch()

    def do_HEAD(self) -> None:
        self._dispatch(head=True)

    def do_POST(self) -> None:
        self._reply(HTTPStatus.METHOD_NOT_ALLOWED, b'{"error":"method_not_allowed"}\n')

    do_PUT = do_POST
    do_PATCH = do_POST
    do_DELETE = do_POST
    do_OPTIONS = do_POST


def create_server(*, port: int) -> ThreadingHTTPServer:
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("port must be between 0 and 65535")
    for name in ASSETS:
        asset = OUTPUT / name
        if not asset.is_file() or asset.is_symlink():
            raise ValueError("built Command Center assets are unavailable")
    return ThreadingHTTPServer(("127.0.0.1", port), LocalApplicationHandler)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run the isolated ORION HTTP preview")
    parser.add_argument("--port", required=True, type=int)
    args = parser.parse_args(argv)
    server = create_server(port=args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
