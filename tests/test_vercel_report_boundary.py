"""The public route denies anonymous requests and only proxies a safe view."""

from __future__ import annotations

import json
from http import HTTPStatus
from http.server import HTTPServer
from threading import Thread
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from deployment.vercel.report import ReportHandler, _upstream_url, fetch_report, probe_backend

_SERVICE_TOKEN = "A" * 43


def _request(path: str, *, method: str = "GET", headers: dict[str, str] | None = None):
    server = HTTPServer(("127.0.0.1", 0), ReportHandler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        request = Request(
            f"http://127.0.0.1:{server.server_port}{path}",
            method=method,
            headers=headers or {},
        )
        try:
            with urlopen(request, timeout=2) as response:
                return response.status, response.headers, response.read()
        except HTTPError as exc:
            return exc.code, exc.headers, exc.read()
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_public_route_denies_anonymous_and_unsupported_requests(monkeypatch) -> None:
    monkeypatch.delenv("ORION_REPORT_BACKEND_URL", raising=False)
    for method in ("GET", "HEAD"):
        status, headers, body = _request("/api/v1/report", method=method)
        assert status == 401
        assert headers["Cache-Control"] == "no-store"
        assert headers["X-Content-Type-Options"] == "nosniff"
        assert body == (b"" if method == "HEAD" else b'{"error":"access_denied"}\n')
    for method in ("POST", "PUT", "PATCH", "DELETE", "OPTIONS"):
        assert _request("/api/v1/report", method=method)[0] == 405
    assert _request("/api/v1/report?tenant=other")[0] == 404
    assert _request("/api/v1/report?utm_source=chatgpt.com")[0] == 401
    assert _request("/api/v1/report?utm_source=chatgpt.com&tenant=other")[0] == 404
    status, _, body = _request(
        "/api/v1/report",
        headers={"Authorization": "Bearer forged", "X-Tenant-ID": "other-tenant"},
    )
    assert status == 503
    assert b"other-tenant" not in body


class _Response:
    def __init__(self, status: int, body: bytes) -> None:
        self.status = status
        self.body = body

    def read(self, size: int) -> bytes:
        return self.body[:size]


class _Connection:
    response = _Response(HTTPStatus.OK, b"{}")
    last_request = None

    def __init__(self, host: str, **kwargs: object) -> None:
        assert host == "reports.example.test"
        assert kwargs["port"] == 443
        assert kwargs["timeout"] in (3, 5)

    def request(self, method: str, path: str, *, headers: dict[str, str]) -> None:
        type(self).last_request = method, path, headers

    def getresponse(self) -> _Response:
        return type(self).response

    def close(self) -> None:
        pass


def _view() -> bytes:
    return json.dumps({
        "authority": {
            "erp_writes": 0, "execution_allowed": False,
            "promotion_allowed": False, "recommendation_allowed": False,
        },
        "cycles": {"attempted": 2, "completed": 2},
        "failures": [],
        "interface_version": "orion.readonly-report.v1",
        "metadata": {"budget": 7, "used": 3},
        "observations_persisted": 4,
        "report_kind": "completed_aggregate",
        "session_ended_at": "2036-01-01T00:05:00+00:00",
        "session_started_at": "2036-01-01T00:00:00+00:00",
        "stop_reason": "cycle_limit",
        "studies": {"budget": 12, "used": 2},
    }).encode()


def test_proxy_forwards_only_authorization_to_fixed_https_host() -> None:
    _Connection.response = _Response(HTTPStatus.OK, _view())
    status, body = fetch_report(
        "Bearer fixture-token",
        backend_url="https://reports.example.test/api/v1/report",
        service_token=_SERVICE_TOKEN,
        connection_factory=_Connection,
    )
    assert status == 200
    assert json.loads(body)["observations_persisted"] == 4
    assert _Connection.last_request == (
        "GET", "/api/v1/report",
        {"Authorization": "Bearer fixture-token", "Accept": "application/json",
         "X-ORION-Service-Token": _SERVICE_TOKEN},
    )


def test_proxy_rejects_invalid_source_and_unsafe_envelope() -> None:
    for url in (
        None, "http://reports.example.test/api/v1/report",
        "https://reports.example.test:bad/api/v1/report",
        "https://reports.example.test/api/v1/report?tenant=x",
    ):
        assert _upstream_url(url) is None
        assert fetch_report("Bearer fixture", backend_url=url)[0] == 503
    _Connection.response = _Response(HTTPStatus.OK, _view().replace(b'"erp_writes": 0', b'"erp_writes": true'))
    assert fetch_report(
        "Bearer fixture", backend_url="https://reports.example.test/api/v1/report",
        service_token=_SERVICE_TOKEN,
        connection_factory=_Connection,
    )[0] == 503
    _Connection.response = _Response(HTTPStatus.OK, _view() + b" " * (64 * 1024))
    assert fetch_report(
        "Bearer fixture", backend_url="https://reports.example.test/api/v1/report",
        service_token=_SERVICE_TOKEN,
        connection_factory=_Connection,
    )[0] == 503


def test_remote_backend_requires_distinct_service_credential() -> None:
    _Connection.last_request = None
    for token in (None, "short", "A" * 42):
        assert fetch_report(
            "Bearer fixture", backend_url="https://reports.example.test/api/v1/report",
            service_token=token, connection_factory=_Connection,
        )[0] == 503
        assert probe_backend(
            "https://reports.example.test/api/v1/report",
            service_token=token, connection_factory=_Connection,
        ) is False
    assert _Connection.last_request is None


def test_readiness_probes_only_the_separate_host() -> None:
    _Connection.response = _Response(
        HTTPStatus.OK, b'{"ready":true,"execution_allowed":false}\n'
    )
    assert probe_backend(
        "https://reports.example.test/api/v1/report", service_token=_SERVICE_TOKEN,
        connection_factory=_Connection,
    ) is True
    assert _Connection.last_request == (
        "GET", "/ready", {"Accept": "application/json",
                          "X-ORION-Service-Token": _SERVICE_TOKEN},
    )
    _Connection.response = _Response(HTTPStatus.SERVICE_UNAVAILABLE, b'{"ready":false}')
    assert probe_backend(
        "https://reports.example.test/api/v1/report", service_token=_SERVICE_TOKEN,
        connection_factory=_Connection,
    ) is False
