"""Synthetic end-to-end custody and access checks for the persistent report host."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import os
import ssl
import threading
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from cryptography.x509.oid import NameOID

from deployment.identity.issue_report_token import IssuanceError, issue_token
from deployment.identity.issue_report_token import main as issue_token_main
from deployment.local_preview import create_server as create_application_server
from deployment.vercel.build_static import main as build_static
from deployment.vercel.report import fetch_report, probe_backend
from orion.discovery.erpnext_live_session import LiveSessionRunReport, live_session_report_json
from orion.presentation.report_auth import ReportAccessPolicy, ReportAuthenticationError
from orion.presentation.report_interface import REPORT_INTERFACE_VERSION, BoundReportSource
from orion.stores.private_files import PrivateFileError
from orion_http.report_server import create_server


def _private(path: Path, body: bytes) -> None:
    path.write_bytes(body)
    path.chmod(0o600)


def _b64(body: bytes) -> str:
    return base64.urlsafe_b64encode(body).rstrip(b"=").decode("ascii")


def _report() -> LiveSessionRunReport:
    return LiveSessionRunReport(
        session_started_at=datetime(2036, 1, 1, tzinfo=UTC),
        session_ended_at=datetime(2036, 1, 1, tzinfo=UTC) + timedelta(minutes=5),
        metadata_preflight_completed=True,
        metadata_get_budget=7,
        metadata_gets=3,
        study_get_budget=12,
        study_gets=2,
        max_live_gets=19,
        total_live_gets=5,
        company_scope_count=1,
        reviewed_entity_count=3,
        reviewed_field_count=5,
        cycles_attempted=2,
        cycles_completed=2,
        observations_persisted=4,
        evidence_batches_appended=2,
        supported_proposal_count=0,
        unsupported_proposal_count=2,
        failure_category_counts=(),
        distinct_entities_studied=2,
        distinct_companies_attempted=1,
        stop_reason="cycle_limit",
    )


def _fixture(tmp_path: Path):
    report_path = tmp_path / "report.json"
    report_body = (live_session_report_json(_report()) + "\n").encode()
    _private(report_path, report_body)
    binding_path = tmp_path / "binding.json"
    _private(binding_path, json.dumps({
        "binding_version": REPORT_INTERFACE_VERSION,
        "tenant_id": "fixture-tenant",
        "report_path": str(report_path),
        "report_sha256": hashlib.sha256(report_body).hexdigest(),
    }).encode())
    private_key = Ed25519PrivateKey.generate()
    public_bytes = private_key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    policy_path = tmp_path / "policy.json"
    _private(policy_path, json.dumps({
        "policy_version": "orion.report-access.v1",
        "issuer": "https://identity.example.test",
        "audience": "orion-report-fixture",
        "tenant_id": "fixture-tenant",
        "allowed_subjects": ["fixture-reader"],
        "ed25519_public_key": _b64(public_bytes),
    }).encode())
    return (
        BoundReportSource.from_private_binding(binding_path),
        ReportAccessPolicy.from_private_file(policy_path),
        private_key,
        report_path,
    )


def _token(private_key: Ed25519PrivateKey, **overrides: object) -> str:
    now = int(time.time())
    claims = {
        "iss": "https://identity.example.test",
        "aud": "orion-report-fixture",
        "sub": "fixture-reader",
        "tenant_id": "fixture-tenant",
        "scope": "orion:report:read",
        "iat": now,
        "nbf": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    header = _b64(b'{"alg":"EdDSA","typ":"JWT"}')
    payload = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    signing_input = f"{header}.{payload}".encode()
    return f"{header}.{payload}.{_b64(private_key.sign(signing_input))}"


def _tls_certificate(tmp_path: Path) -> tuple[Path, Path]:
    tls_key = Ed25519PrivateKey.generate()
    subject = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(tls_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(datetime.now(UTC) - timedelta(minutes=1))
        .not_valid_after(datetime.now(UTC) + timedelta(days=1))
        .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]), critical=False)
        .sign(tls_key, algorithm=None)
    )
    cert_path = tmp_path / "localhost.pem"
    key_path = tmp_path / "localhost-key.pem"
    _private(cert_path, certificate.public_bytes(Encoding.PEM))
    _private(key_path, tls_key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    return cert_path, key_path


def _request(
    port: int, method: str, path: str, token: str | None = None,
    *, service_token: str | None = None,
):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    if service_token is not None:
        headers["X-ORION-Service-Token"] = service_token
    connection.request(method, path, headers=headers)
    response = connection.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    connection.close()
    return result


def test_bound_report_requires_independent_identity_and_recovers_after_restart(tmp_path: Path) -> None:
    source, policy, key, report_path = _fixture(tmp_path)
    valid = _token(key)
    for _ in range(2):
        server = create_server(source, policy, port=0)
        worker = threading.Thread(target=server.serve_forever, daemon=True)
        worker.start()
        try:
            port = server.server_port
            assert _request(port, "GET", "/health")[0] == 200
            assert _request(port, "GET", "/ready")[0] == 200
            assert _request(port, "GET", "/api/v1/report")[0] == 401
            assert _request(port, "GET", "/api/v1/report", _token(key, tenant_id="other"))[0] == 401
            assert _request(port, "GET", "/api/v1/report", _token(key, sub="other"))[0] == 401
            assert _request(port, "GET", "/api/v1/report", _token(key, scope="other"))[0] == 401
            assert _request(port, "GET", "/api/v1/report", _token(key, exp=1))[0] == 401
            assert _request(port, "GET", "/api/v1/report", _token(Ed25519PrivateKey.generate()))[0] == 401
            assert _request(port, "POST", "/api/v1/report", valid)[0] == 405
            assert _request(port, "GET", "/api/v1/report?tenant=other", valid)[0] == 404
            duplicate = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
            duplicate.putrequest("GET", "/api/v1/report")
            duplicate.putheader("Authorization", f"Bearer {valid}")
            duplicate.putheader("Authorization", f"Bearer {valid}")
            duplicate.endheaders()
            assert duplicate.getresponse().status == 401
            duplicate.close()
            status, headers, body = _request(port, "GET", "/api/v1/report", valid)
            assert status == 200
            assert headers["Cache-Control"] == "no-store"
            assert json.loads(body)["observations_persisted"] == 4
            assert b"fixture-tenant" not in body
            assert str(report_path).encode() not in body
        finally:
            server.shutdown()
            server.server_close()
            worker.join(timeout=2)


def test_service_credential_is_separate_rotatable_and_fail_closed(tmp_path: Path) -> None:
    source, policy, key, _ = _fixture(tmp_path)
    credential = tmp_path / "service-token"
    first, second = "A" * 43, "B" * 43
    _private(credential, first.encode())
    credential.chmod(0o644)
    with pytest.raises(PrivateFileError):
        create_server(source, policy, port=0, service_token_path=credential)
    credential.chmod(0o600)
    link = tmp_path / "service-token-link"
    link.symlink_to(credential)
    with pytest.raises(PrivateFileError):
        create_server(source, policy, port=0, service_token_path=link)
    server = create_server(source, policy, port=0, service_token_path=credential)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        port = server.server_port
        token = _token(key)
        assert _request(port, "GET", "/ready")[0] == 401
        assert _request(port, "GET", "/ready", service_token="C" * 43)[0] == 401
        assert _request(port, "GET", "/ready", service_token=first)[0] == 200
        assert _request(port, "GET", "/api/v1/report", token)[0] == 401
        assert _request(port, "GET", "/api/v1/report", service_token=first)[0] == 401
        assert _request(port, "GET", "/api/v1/report", token, service_token=first)[0] == 200

        replacement = tmp_path / "service-token-replacement"
        _private(replacement, second.encode())
        os.replace(replacement, credential)
        assert _request(port, "GET", "/api/v1/report", token, service_token=first)[0] == 401
        assert _request(port, "GET", "/api/v1/report", token, service_token=second)[0] == 200
        credential.unlink()
        assert _request(port, "GET", "/ready", service_token=second)[0] == 503
        assert _request(port, "GET", "/api/v1/report", token, service_token=second)[0] == 503
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_digest_change_fails_closed_and_readiness_reports_unavailable(tmp_path: Path) -> None:
    source, policy, key, report_path = _fixture(tmp_path)
    with pytest.raises(ValueError, match="policy unavailable"):
        create_server(source, replace(policy, tenant_id="other"), port=0)
    server = create_server(source, policy, port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        report_path.write_bytes(report_path.read_bytes() + b" ")
        report_path.chmod(0o600)
        assert _request(server.server_port, "GET", "/ready")[0] == 503
        assert _request(server.server_port, "GET", "/api/v1/report", _token(key))[0] == 503
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)
    with pytest.raises(ValueError, match="digest does not match"):
        create_server(source, policy, port=0)


def test_report_policy_revocation_and_rotation_apply_without_restart(tmp_path: Path) -> None:
    source, _, old_key, _ = _fixture(tmp_path)
    policy_path = tmp_path / "policy.json"
    old_token = _token(old_key)
    new_key = Ed25519PrivateKey.generate()

    def replace_policy(*, subjects: list[str], key: Ed25519PrivateKey) -> None:
        policy = json.loads(policy_path.read_text())
        policy["allowed_subjects"] = subjects
        policy["ed25519_public_key"] = _b64(
            key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        )
        replacement = tmp_path / "policy-replacement.json"
        _private(replacement, json.dumps(policy).encode())
        os.replace(replacement, policy_path)

    server = create_server(source, policy_path, port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        port = server.server_port
        assert _request(port, "GET", "/api/v1/report", old_token)[0] == 200
        assert _request(port, "GET", "/api/v1/report", _token(old_key, aud="wrong"))[0] == 401

        replace_policy(subjects=["revoked-reader"], key=old_key)
        assert _request(port, "GET", "/ready")[0] == 200
        assert _request(port, "GET", "/api/v1/report", old_token)[0] == 401

        replace_policy(subjects=["fixture-reader"], key=new_key)
        assert _request(port, "GET", "/api/v1/report", old_token)[0] == 401
        assert _request(port, "GET", "/api/v1/report", _token(new_key))[0] == 200

        policy_path.unlink()
        assert _request(port, "GET", "/ready")[0] == 503
        assert _request(port, "GET", "/api/v1/report", _token(new_key))[0] == 503
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_separate_issuer_writes_private_short_lived_token(tmp_path: Path) -> None:
    source, policy, key, _ = _fixture(tmp_path)
    private_dir = tmp_path / "issuance"
    private_dir.mkdir(mode=0o700)
    key_path = private_dir / "signing-key.pem"
    _private(
        key_path,
        key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(b"fixture-passphrase")),
    )
    output_path = private_dir / "one-token"
    now = int(time.time())
    issue_token(
        policy_path=tmp_path / "policy.json",
        key_path=key_path,
        subject="fixture-reader",
        passphrase=b"fixture-passphrase",
        output_path=output_path,
        now=now,
    )
    assert output_path.stat().st_mode & 0o777 == 0o600
    issued = f"Bearer {output_path.read_text().strip()}"
    policy.authorize(issued, report_tenant=source.tenant_id, now=now)
    with pytest.raises(ReportAuthenticationError):
        policy.authorize(issued, report_tenant=source.tenant_id, now=now + 300)
    with pytest.raises(IssuanceError):
        issue_token(
            policy_path=tmp_path / "policy.json",
            key_path=key_path,
            subject="fixture-reader",
            passphrase=b"fixture-passphrase",
            output_path=output_path,
            now=now,
        )
    with pytest.raises(IssuanceError):
        issue_token(
            policy_path=tmp_path / "policy.json",
            key_path=key_path,
            subject="unauthorized-reader",
            passphrase=b"fixture-passphrase",
            output_path=private_dir / "denied-token",
            now=now,
        )
    assert not (private_dir / "denied-token").exists()
    with pytest.raises(IssuanceError):
        issue_token(
            policy_path=tmp_path / "policy.json",
            key_path=key_path,
            subject="fixture-reader",
            passphrase=b"wrong-passphrase",
            output_path=private_dir / "wrong-passphrase-token",
            now=now,
        )
    assert not (private_dir / "wrong-passphrase-token").exists()


def test_issuer_cli_uses_private_descriptors_without_printing_token(tmp_path: Path, capsys) -> None:
    source, policy, key, _ = _fixture(tmp_path)
    private_dir = tmp_path / "issuance"
    private_dir.mkdir(mode=0o700)
    key_path = private_dir / "signing-key.pem"
    _private(
        key_path,
        key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(b"fixture-passphrase")),
    )
    subject_read, subject_write = os.pipe()
    passphrase_read, passphrase_write = os.pipe()
    try:
        os.write(subject_write, b"fixture-reader")
        os.write(passphrase_write, b"fixture-passphrase")
        os.close(subject_write)
        os.close(passphrase_write)
        subject_write = passphrase_write = -1
        output_path = private_dir / "cli-token"
        assert issue_token_main([
            "--policy", str(tmp_path / "policy.json"),
            "--key", str(key_path),
            "--subject-fd", str(subject_read),
            "--passphrase-fd", str(passphrase_read),
            "--output", str(output_path),
        ]) == 0
        assert capsys.readouterr() == ("", "")
        policy.authorize(
            f"Bearer {output_path.read_text().strip()}", report_tenant=source.tenant_id
        )
    finally:
        for descriptor in (subject_read, subject_write, passphrase_read, passphrase_write):
            if descriptor >= 0:
                os.close(descriptor)


def test_ui_api_tls_host_and_persistent_report_chain(tmp_path: Path, monkeypatch) -> None:
    source, policy, key, report_path = _fixture(tmp_path)
    cert_path, key_path = _tls_certificate(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_path))
    build_static()
    for _ in range(2):
        host = create_server(source, policy, port=0)
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(str(cert_path), str(key_path))
        host.socket = tls.wrap_socket(host.socket, server_side=True)
        monkeypatch.setenv(
            "ORION_REPORT_BACKEND_URL", f"https://localhost:{host.server_port}/api/v1/report"
        )
        app = create_application_server(port=0)
        host_thread = threading.Thread(target=host.serve_forever, daemon=True)
        app_thread = threading.Thread(target=app.serve_forever, daemon=True)
        host_thread.start()
        app_thread.start()
        try:
            port = app.server_port
            assert _request(port, "GET", "/")[0] == 200
            assert b"ORION" in _request(port, "GET", "/")[2]
            assert _request(port, "GET", "/api/health")[0] == 200
            assert _request(port, "GET", "/api/ready")[0] == 200
            assert _request(port, "GET", "/api/v1/report")[0] == 401
            assert _request(port, "GET", "/api/v1/report", _token(key, tenant_id="other"))[0] == 401
            status, headers, body = _request(port, "GET", "/api/v1/report", _token(key))
            assert status == 200
            assert headers["Cache-Control"] == "no-store"
            assert json.loads(body)["observations_persisted"] == 4
            assert str(report_path).encode() not in body
        finally:
            app.shutdown()
            host.shutdown()
            app.server_close()
            host.server_close()
            app_thread.join(timeout=2)
            host_thread.join(timeout=2)


def test_https_proxy_and_report_host_require_both_credentials(tmp_path: Path, monkeypatch) -> None:
    source, policy, key, _ = _fixture(tmp_path)
    cert_path, key_path = _tls_certificate(tmp_path)
    monkeypatch.setenv("SSL_CERT_FILE", str(cert_path))
    credential = tmp_path / "service-token"
    first, second = "A" * 43, "B" * 43
    _private(credential, first.encode())
    host = create_server(source, policy, port=0, service_token_path=credential)
    tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    tls.load_cert_chain(str(cert_path), str(key_path))
    host.socket = tls.wrap_socket(host.socket, server_side=True)
    worker = threading.Thread(target=host.serve_forever, daemon=True)
    worker.start()
    url = f"https://localhost:{host.server_port}/api/v1/report"
    try:
        assert probe_backend(url, service_token=first)
        assert not probe_backend(url)
        assert fetch_report(f"Bearer {_token(key)}", backend_url=url, service_token=first)[0] == 200
        assert fetch_report(f"Bearer {_token(key, exp=1)}", backend_url=url,
                            service_token=first)[0] == 401
        assert fetch_report(f"Bearer {_token(key, tenant_id='other')}", backend_url=url,
                            service_token=first)[0] == 401
        assert fetch_report(f"Bearer {_token(Ed25519PrivateKey.generate())}", backend_url=url,
                            service_token=first)[0] == 401
        replacement = tmp_path / "service-token-replacement"
        _private(replacement, second.encode())
        os.replace(replacement, credential)
        assert not probe_backend(url, service_token=first)
        assert probe_backend(url, service_token=second)
        assert fetch_report(f"Bearer {_token(key)}", backend_url=url,
                            service_token=second)[0] == 200
    finally:
        host.shutdown()
        host.server_close()
        worker.join(timeout=2)
