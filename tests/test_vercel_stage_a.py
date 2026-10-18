"""Stage A HTTP contract and deployment source boundary."""

import json
from http.server import HTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from api.health import handler as HealthHandler
from api.ready import handler as ReadinessHandler
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


def test_stage_a_status_reports_only_http_health_and_denied_authority(monkeypatch):
    monkeypatch.delenv("ORION_REPORT_BACKEND_URL", raising=False)
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
        tracked_status, _, tracked_body = _request(
            handler, endpoint + "?utm_source=chatgpt.com"
        )
        assert tracked_status == 200
        assert tracked_body == body
        assert _request(handler, endpoint + "?tenant=other")[0] == 404
        assert _request(handler, endpoint + "?utm_source=chatgpt.com&tenant=other")[0] == 404


def test_vercel_routes_only_named_application_assets_and_http_functions():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    config = json.loads((root / "vercel.json").read_text())
    assert config["framework"] is None
    assert config["outputDirectory"] == "public"
    assert config["rewrites"] == [{"source": "/", "destination": "/index.html"}]
    assert "services" not in config
    assert not (root / "public").is_symlink()


def test_report_host_readiness_is_fail_closed_without_configuration(monkeypatch):
    monkeypatch.delenv("ORION_REPORT_BACKEND_URL", raising=False)
    status, headers, body = _request(ReadinessHandler, "/api/ready")
    assert status == 503
    assert headers["Cache-Control"] == "no-store"
    assert json.loads(body) == {"ready": False, "execution_allowed": False}
    assert _request(ReadinessHandler, "/api/ready", method="POST")[0] == 405
    tracked_status, _, tracked_body = _request(
        ReadinessHandler, "/api/ready?utm_source=chatgpt.com"
    )
    assert tracked_status == 503
    assert tracked_body == body
    assert _request(ReadinessHandler, "/api/ready?tenant=other")[0] == 404


def test_tracked_readiness_reports_real_backend_state(monkeypatch):
    monkeypatch.setattr("deployment.vercel.readiness.probe_backend", lambda *args, **kwargs: True)
    status, _, body = _request(
        ReadinessHandler, "/api/ready?utm_source=chatgpt.com"
    )
    assert status == 200
    assert json.loads(body) == {"ready": True, "execution_allowed": False}


def test_status_reports_configuration_without_claiming_backend_health(monkeypatch):
    monkeypatch.setenv(
        "ORION_REPORT_BACKEND_URL", "https://reports.example.test/api/v1/report"
    )
    monkeypatch.setenv("ORION_REPORT_SERVICE_TOKEN", "A" * 43)
    status, _, body = _request(StatusHandler, "/api/v1/status")
    assert status == 200
    value = json.loads(body)
    assert value["release_version"] == "0.2.0rc2"
    assert value["report_service"] == "requires_authentication"
    assert value["persistent_storage"] == "not_connected"
    assert value["execution_allowed"] is False
