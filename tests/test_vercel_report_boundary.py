"""Stage B public route must stay fail-closed while no report service is bound."""

from __future__ import annotations

import json
from http.server import HTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from deployment.vercel.report import ReportHandler


def test_public_report_is_explicitly_unavailable() -> None:
    server = HTTPServer(("127.0.0.1", 0), ReportHandler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/v1/report"
        for method, expected in [("GET", 503), ("HEAD", 503), ("POST", 405), ("PUT", 405), ("PATCH", 405), ("DELETE", 405), ("OPTIONS", 405)]:
            try:
                urlopen(Request(url, method=method), timeout=2)
                raise AssertionError("report must not return success")
            except HTTPError as exc:
                assert exc.code == expected
                assert exc.headers["Cache-Control"] == "no-store"\n                assert exc.headers["X-Content-Type-Options"] == "nosniff"
                if method == "GET":
                    body = json.loads(exc.read())
                    assert body == {
                        "error": "report_service_not_connected",
                        "execution_allowed": False,
                    }
        try:
            urlopen(url + "?tenant=demo", timeout=2)
            raise AssertionError("query variants must not serve report")
        except HTTPError as exc:
            assert exc.code == 404
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_forged_identity_headers_cannot_enable_report() -> None:
    server = HTTPServer(("127.0.0.1", 0), ReportHandler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        url = f"http://127.0.0.1:{server.server_port}/api/v1/report"
        request = Request(
            url,
            headers={
                "Authorization": "Bearer forged-token",
                "X-Forwarded-User": "admin",
                "X-Tenant-ID": "other-tenant",
            },
        )
        try:
            urlopen(request, timeout=2)
            raise AssertionError("untrusted identity headers must not enable the report")
        except HTTPError as exc:
            assert exc.code == 503
            assert b"other-tenant" not in exc.read()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
