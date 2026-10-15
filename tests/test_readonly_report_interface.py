from __future__ import annotations

import base64
import hashlib
import http.client
import json
import re
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from orion.discovery.erpnext_live_session import (
    LiveSessionError,
    LiveSessionRunReport,
    live_session_report_json,
    live_session_run_report_from_json,
)
from orion.presentation.report_interface import (
    REPORT_INTERFACE_VERSION,
    BoundReportSource,
    ReportInterfaceError,
)
from orion.presentation.server import create_server


def _report(**changes: object) -> LiveSessionRunReport:
    values: dict[str, object] = {
        "session_started_at": datetime(2036, 1, 1, tzinfo=UTC),
        "session_ended_at": datetime(2036, 1, 1, tzinfo=UTC) + timedelta(minutes=5),
        "metadata_preflight_completed": True,
        "metadata_get_budget": 7,
        "metadata_gets": 3,
        "study_get_budget": 12,
        "study_gets": 4,
        "max_live_gets": 19,
        "total_live_gets": 7,
        "company_scope_count": 1,
        "reviewed_entity_count": 3,
        "reviewed_field_count": 5,
        "cycles_attempted": 4,
        "cycles_completed": 4,
        "observations_persisted": 9,
        "evidence_batches_appended": 4,
        "supported_proposal_count": 0,
        "unsupported_proposal_count": 4,
        "failure_category_counts": (),
        "distinct_entities_studied": 2,
        "distinct_companies_attempted": 1,
        "stop_reason": "cycle_limit",
    }
    values.update(changes)
    return LiveSessionRunReport(**values)  # type: ignore[arg-type]


def _write_private(path: Path, body: bytes) -> None:
    path.write_bytes(body)
    path.chmod(0o600)


def _bound_source(tmp_path: Path) -> tuple[BoundReportSource, Path, Path]:
    report_path = tmp_path / "report.json"
    report_body = (live_session_report_json(_report()) + "\n").encode()
    _write_private(report_path, report_body)
    binding_path = tmp_path / "binding.json"
    binding = {
        "binding_version": REPORT_INTERFACE_VERSION,
        "tenant_id": "private-tenant-reference",
        "report_path": str(report_path),
        "report_sha256": hashlib.sha256(report_body).hexdigest(),
    }
    _write_private(binding_path, json.dumps(binding).encode())
    return BoundReportSource.from_private_binding(binding_path), report_path, binding_path


def test_report_parser_reuses_canonical_invariants() -> None:
    body = live_session_report_json(_report()).encode()

    parsed = live_session_run_report_from_json(body)

    assert parsed.total_live_gets == 7
    unsafe = json.loads(body)
    unsafe["execution_allowed"] = True
    with pytest.raises(LiveSessionError, match="downstream authority"):
        live_session_run_report_from_json(json.dumps(unsafe).encode())
    duplicate = body[:-1] + b',"erp_writes":0}'
    with pytest.raises(LiveSessionError, match="keys must be unique"):
        live_session_run_report_from_json(duplicate)


def test_bound_source_projects_safe_view_without_tenant_or_path(tmp_path: Path) -> None:
    source, _, _ = _bound_source(tmp_path)

    view = source.read_view()

    assert view["interface_version"] == REPORT_INTERFACE_VERSION
    assert view["authority"] == {
        "erp_writes": 0,
        "execution_allowed": False,
        "promotion_allowed": False,
        "recommendation_allowed": False,
    }
    serialized = json.dumps(view)
    assert source.tenant_id not in serialized
    assert str(source.report_path) not in serialized


def test_bound_source_rejects_mismatch_mode_and_links(tmp_path: Path) -> None:
    source, report_path, binding_path = _bound_source(tmp_path)
    report_path.write_bytes(report_path.read_bytes() + b" ")
    report_path.chmod(0o600)
    with pytest.raises(ReportInterfaceError, match="digest does not match"):
        source.read_view()

    binding_path.chmod(0o644)
    with pytest.raises(ReportInterfaceError, match="binding is invalid"):
        BoundReportSource.from_private_binding(binding_path)

    binding_path.chmod(0o600)
    link = tmp_path / "binding-link.json"
    link.symlink_to(binding_path)
    with pytest.raises(ReportInterfaceError, match="symbolic links"):
        BoundReportSource.from_private_binding(link)


def _request(
    server_port: int,
    method: str,
    path: str,
    *,
    headers: dict[str, str] | None = None,
) -> tuple[int, dict[str, str], bytes]:
    connection = http.client.HTTPConnection("127.0.0.1", server_port, timeout=2)
    connection.request(method, path, headers=headers or {})
    response = connection.getresponse()
    body = response.read()
    result = response.status, {key.lower(): value for key, value in response.getheaders()}, body
    connection.close()
    return result


def test_loopback_server_exposes_only_static_assets_and_readonly_view(tmp_path: Path) -> None:
    source, report_path, _ = _bound_source(tmp_path)
    server = create_server(source, port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        status, headers, body = _request(server.server_port, "GET", "/api/v1/report")
        assert status == 200
        assert headers["cache-control"] == "no-store"
        assert headers["x-frame-options"] == "DENY"
        assert headers["server"] == "ORION "
        payload = json.loads(body)
        assert payload["interface_version"] == REPORT_INTERFACE_VERSION
        assert source.tenant_id.encode() not in body
        assert str(report_path).encode() not in body

        assert _request(server.server_port, "GET", "/")[0] == 200
        assert _request(server.server_port, "GET", "/report-link.js")[0] == 200
        assert _request(server.server_port, "GET", "/missing")[0] == 404
        assert _request(server.server_port, "POST", "/api/v1/report")[0] == 405
        assert _request(server.server_port, "PUT", "/api/v1/report")[0] == 405
        assert _request(server.server_port, "GET", "/api/v1/report?tenant=other")[0] == 404
        assert _request(
            server.server_port,
            "GET",
            "/api/v1/report",
            headers={"Host": "hostile.example"},
        )[0] == 403
        assert _request(
            server.server_port,
            "GET",
            "/api/v1/report",
            headers={"Sec-Fetch-Site": "cross-site"},
        )[0] == 403

        _write_private(report_path, report_path.read_bytes() + b" ")
        status, _, body = _request(server.server_port, "GET", "/api/v1/report")
        assert status == 503
        assert body == b'{"error":"bound report unavailable"}\n'
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()


def test_binding_parser_rejects_duplicate_keys_before_report_io(tmp_path: Path) -> None:
    binding_path = tmp_path / "binding.json"
    _write_private(
        binding_path,
        b'{"binding_version":"orion.readonly-report.v1",'
        b'"binding_version":"orion.readonly-report.v1",'
        b'"tenant_id":"t","report_path":"/not/read","report_sha256":"'
        + (b"0" * 64)
        + b'"}',
    )

    with pytest.raises(ReportInterfaceError, match="keys must be unique"):
        BoundReportSource.from_private_binding(binding_path)


def test_semantically_invalid_exact_report_fails_closed(tmp_path: Path) -> None:
    report_path = tmp_path / "report.json"
    unsafe = json.loads(live_session_report_json(_report()))
    unsafe["execution_allowed"] = True
    report_body = json.dumps(unsafe).encode()
    _write_private(report_path, report_body)
    binding_path = tmp_path / "binding.json"
    _write_private(
        binding_path,
        json.dumps(
            {
                "binding_version": REPORT_INTERFACE_VERSION,
                "tenant_id": "tenant-a",
                "report_path": str(report_path),
                "report_sha256": hashlib.sha256(report_body).hexdigest(),
            }
        ).encode(),
    )

    source = BoundReportSource.from_private_binding(binding_path)
    with pytest.raises(ReportInterfaceError, match="bound report is invalid"):
        source.read_view()


def test_inline_style_hash_matches_server_policy(tmp_path: Path) -> None:
    source, _, _ = _bound_source(tmp_path)
    server = create_server(source, port=0)
    worker = threading.Thread(target=server.serve_forever)
    worker.start()
    try:
        status, headers, body = _request(server.server_port, "GET", "/")
        assert status == 200
        match = re.search(rb"<style>(.*?)</style>", body, flags=re.DOTALL)
        assert match is not None
        digest = hashlib.sha256(match.group(1)).digest()
        encoded = base64.b64encode(digest).decode()
        assert f"'sha256-{encoded}'" in headers["content-security-policy"]
    finally:
        server.shutdown()
        worker.join(timeout=2)
        server.server_close()


def test_binding_path_must_be_absolute() -> None:
    with pytest.raises(ReportInterfaceError, match="absolute path"):
        BoundReportSource.from_private_binding(Path("relative.json"))
