"""Stage A HTTP contract and deployment source boundary."""

import json
from http.server import HTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from api.health import handler as HealthHandler
from api.v1.status import handler as StatusHandler


def _request(handler, path, *, method="GET"):
    server = HTTPServer(("127.0.0.1", 0), handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        request = Request(f"http://127.0.0.1:{server.server_port}{path}", method=method)
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, response.headers, response.read()
        except HTTPError as error:
            return error.code, error.headers, error.read()
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()


def test_stage_a_status_reports_only_http_health_and_denied_authority():
    for endpoint, handler in (
        ("/api/health", HealthHandler),
        ("/api/v1/status", StatusHandler),
    ):
        status, headers, body = _request(handler, endpoint)
        assert status == 200
        assert headers["Cache-Control"] == "no-store"
        value = json.loads(body)
        assert value["http_api"] == "healthy"
        assert value["report_service"] == "not_connected"
        assert value["governed_worker"] == "not_connected"
        assert value["persistent_storage"] == "not_connected"
        assert value["live_pilot_ready"] is False
        assert value["execution_allowed"] is False
        assert "tenant" not in body.decode()
        assert "credential" not in body.decode()
        assert _request(handler, endpoint, method="POST")[0] == 405
        assert _request(handler, endpoint + "?tenant=other")[0] == 404


def test_vercel_routes_only_named_application_assets_and_http_functions():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "vercel.json").read_text())
    assert config["framework"] is None
    assert config["outputDirectory"] == "public"
    assert config["rewrites"] == [{"source": "/", "destination": "/index.html"}]
    assert "services" not in config
    assert not (root / "public").is_symlink()
