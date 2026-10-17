"""One governed synthetic source, investigation, private report and API lineage."""

from __future__ import annotations

import base64
import http.client
import json
import threading
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from deployment.release_candidate import continuous_lineage as lineage
from deployment.vercel.report import _validated_view
from orion.contracts import EvidenceKind
from orion.discovery.pilot_read import PilotAuthorization, PilotRequest
from orion.discovery.read_window import ReviewedReadWindow
from orion.presentation.report_interface import BoundReportSource, ReportInterfaceError
from orion_connector_sdk import ReviewedConnector, discover, registry
from orion_connector_synthetic import SyntheticRecordConnector
from orion_http.report_server import create_server


def _registration(monkeypatch):
    entry = SimpleNamespace(
        name="synthetic-records",
        value="orion_connector_synthetic:SyntheticRecordConnector",
        dist=SimpleNamespace(
            metadata={"Name": "originorion-connector-synthetic"}, version="0.2.0rc2"
        ),
    )
    monkeypatch.setattr(registry.metadata, "entry_points", lambda *, group: [entry])
    registration = discover(ReviewedConnector(
        name="synthetic-records", distribution="originorion-connector-synthetic",
        version="0.2.0rc2",
        entry_point="orion_connector_synthetic:SyntheticRecordConnector",
    ))
    assert registration.execution_allowed is False
    return registration


def _scope(now, *, tenant="lineage-tenant"):
    records = (
        {"name": "SYN-001", "company": "Synthetic Co", "posting_date": "2036-01-02", "amount": 37},
        {"name": "SYN-002", "company": "Synthetic Co", "posting_date": "2036-01-03", "amount": 50},
    )
    connector = SyntheticRecordConnector(
        tenant_id=tenant, source_id="synthetic-source-1", dataset_id="dataset-267-alpha",
        records=records,
    )
    fields = ("name", "company", "posting_date", "amount")
    window = ReviewedReadWindow(
        tenant_id=tenant, company="Synthetic Co", resource="Synthetic Invoice",
        fields=fields, date_field="posting_date", start=date(2036, 1, 1),
        end=date(2036, 1, 31), expires_at=now + timedelta(minutes=30),
    )
    grant = PilotAuthorization(
        authorization_id="synthetic-read-267", source_id=connector.source_id,
        window=window, identity_field="name", company_field="company",
        provenance_source="synthetic-record-v1", evidence_kind=EvidenceKind.SOURCE,
        max_records=2,
    )
    request = PilotRequest(
        tenant_id=tenant, company="Synthetic Co", source_id=connector.source_id,
        resource="Synthetic Invoice", fields=fields, date_field="posting_date",
        start=window.start, end=window.end, max_records=2,
    )
    return connector, request, grant


def _run(tmp_path, monkeypatch, *, connector=None, request=None, grant=None, lookup=None):
    now = datetime(2036, 1, 3, 12, tzinfo=UTC)
    base_connector, base_request, base_grant = _scope(now)
    connector = connector or base_connector
    request = request or base_request
    grant = grant or base_grant
    run_dir = tmp_path / "run"
    run_dir.mkdir(mode=0o700, parents=True)
    result = lineage.run_synthetic_lineage(
        registration=_registration(monkeypatch), connector=connector, request=request,
        authorization_id=grant.authorization_id,
        lookup=lookup or (lambda identity: grant if identity == grant.authorization_id else None),
        run_id=uuid4(), private_dir=run_dir, now=now,
    )
    return result, run_dir


def _private(path: Path, body: bytes) -> None:
    path.write_bytes(body)
    path.chmod(0o600)


def _b64(body: bytes) -> str:
    return base64.urlsafe_b64encode(body).rstrip(b"=").decode("ascii")


def _token(key, *, tenant="lineage-tenant", subject="synthetic-reader"):
    now = int(time.time())
    header = _b64(b'{"alg":"EdDSA","typ":"JWT"}')
    claims = _b64(json.dumps({
        "iss": "https://identity.synthetic.invalid", "aud": "orion-lineage",
        "sub": subject, "tenant_id": tenant, "scope": "orion:report:read",
        "iat": now, "nbf": now, "exp": now + 300,
    }, sort_keys=True, separators=(",", ":")).encode())
    body = f"{header}.{claims}"
    return f"{body}.{_b64(key.sign(body.encode()))}"


def _request(port, path, token=None, method="GET"):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    headers = {} if token is None else {"Authorization": f"Bearer {token}"}
    connection.request(method, path, headers=headers)
    response = connection.getresponse()
    result = response.status, response.read()
    connection.close()
    return result


def test_one_source_is_preserved_and_exact_view_is_authenticated(tmp_path, monkeypatch):
    result, run_dir = _run(tmp_path, monkeypatch)
    source = BoundReportSource.from_private_binding(result.binding_path)
    assert source.read_view() == result.view
    assert result.view["lineage"]["source_sha256"] == result.source_sha256
    assert result.view["lineage"]["run_id"] == str(result.run_id)
    assert len(result.evidence_ids) == 2
    assert result.view["authority"]["execution_allowed"] is False
    original = json.loads((run_dir / "source.json").read_text())
    admitted = json.loads((run_dir / "evidence.json").read_text())
    assert original["run_id"] == admitted["run_id"] == str(result.run_id)
    assert original["source_sha256"] == admitted["source_sha256"]
    assert [item["record"] for item in admitted["evidence"]] == original["source"]
    assert all(item["authorization_id"] == "synthetic-read-267"
               for item in admitted["evidence"])
    assert all(item["source_sha256"] == result.source_sha256
               and item["mode"] == "read_only"
               and item["provenance"]["authorization_id"] == "synthetic-read-267"
               and len(item["provenance"]["scope_sha256"]) == 64
               and item["provenance"]["upstream_evidence_id"]
               and item["provenance"]["upstream_observation_id"]
               for item in admitted["evidence"])
    assert not hasattr(SyntheticRecordConnector, "write")
    assert not hasattr(SyntheticRecordConnector, "source_records")
    expected = (json.dumps(result.view, sort_keys=True, separators=(",", ":")) + "\n").encode()
    assert _validated_view(expected) == expected
    for field, unsafe in (("erp_writes", False), ("execution_allowed", 0)):
        substituted = json.loads(expected)
        substituted["authority"][field] = unsafe
        with pytest.raises(ValueError):
            _validated_view(json.dumps(substituted).encode())

    key = Ed25519PrivateKey.generate()
    policy_path = run_dir / "policy.json"
    policy = {
        "policy_version": "orion.report-access.v1",
        "issuer": "https://identity.synthetic.invalid", "audience": "orion-lineage",
        "tenant_id": result.tenant_id, "allowed_subjects": ["synthetic-reader"],
        "ed25519_public_key": _b64(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)),
    }
    _private(policy_path, json.dumps(policy).encode())
    server = create_server(source, policy_path, port=0)
    worker = threading.Thread(target=server.serve_forever, daemon=True)
    worker.start()
    try:
        port = server.server_port
        assert _request(port, "/ready")[0] == 200
        assert _request(port, "/api/v1/report")[0] == 401
        assert _request(port, "/api/v1/report", _token(key, tenant="other"))[0] == 401
        assert _request(port, "/api/v1/report", _token(key, subject="revoked"))[0] == 401
        assert _request(port, "/api/v1/report", _token(Ed25519PrivateKey.generate()))[0] == 401
        assert _request(port, "/api/v1/report", _token(key), "POST")[0] == 405
        assert _request(port, "/api/v1/report", _token(key)) == (200, expected)
        policy["allowed_subjects"] = ["replacement-reader"]
        _private(policy_path, json.dumps(policy).encode())
        assert _request(port, "/api/v1/report", _token(key))[0] == 401
        assert _request(port, "/api/v1/report", _token(key, subject="replacement-reader")) == (
            200, expected
        )
        evidence_path = run_dir / "evidence.json"
        preserved = evidence_path.read_bytes()
        _private(evidence_path, b"tampered")
        assert _request(port, "/ready")[0] == 503
        assert _request(port, "/api/v1/report", _token(key, subject="replacement-reader"))[0] == 503
        _private(evidence_path, preserved)
        assert _request(port, "/ready")[0] == 200
    finally:
        server.shutdown()
        server.server_close()
        worker.join(timeout=2)


def test_absent_grant_tenant_mismatch_stale_evidence_and_interruption_denied(
        tmp_path, monkeypatch):
    now = datetime(2036, 1, 3, 12, tzinfo=UTC)
    connector, request, grant = _scope(now)
    with pytest.raises(ValueError, match="authorization missing"):
        _run(tmp_path / "absent", monkeypatch, connector=connector, request=request,
             grant=grant, lookup=lambda _: None)
    assert not (tmp_path / "absent" / "run" / "evidence.json").exists()
    assert not (tmp_path / "absent" / "run" / "source.json").exists()
    other, _, _ = _scope(now, tenant="other-tenant")
    with pytest.raises(ValueError, match="synthetic run scope mismatch"):
        _run(tmp_path / "tenant", monkeypatch, connector=other, request=request, grant=grant)
    stale = SyntheticRecordConnector(
        tenant_id=connector.tenant_id, source_id=connector.source_id,
        dataset_id=connector.dataset_id, records=(
            {"name": "SYN-001", "company": "Synthetic Co", "posting_date": "2036-01-02", "amount": 37},
            {"name": "SYN-002", "company": "Synthetic Co", "posting_date": "2036-01-03", "amount": 50},
        ),
        observed_at=now - timedelta(minutes=6),
    )
    with pytest.raises(ValueError, match="stale"):
        _run(tmp_path / "stale", monkeypatch, connector=stale, request=request, grant=grant)
    assert not (tmp_path / "stale" / "run" / "report.json").exists()
    monkeypatch.setattr(lineage, "_investigate", lambda _: (_ for _ in ()).throw(RuntimeError("stop")))
    with pytest.raises(RuntimeError, match="stop"):
        _run(tmp_path / "interrupted", monkeypatch, connector=connector,
             request=request, grant=grant)
    assert (tmp_path / "interrupted" / "run" / "source.json").exists()
    assert (tmp_path / "interrupted" / "run" / "evidence.json").exists()
    assert not (tmp_path / "interrupted" / "run" / "report.json").exists()
    assert not (tmp_path / "interrupted" / "run" / "binding.json").exists()


def test_report_rejects_private_source_or_witness_substitution(tmp_path, monkeypatch):
    result, run_dir = _run(tmp_path, monkeypatch)
    source = BoundReportSource.from_private_binding(result.binding_path)
    for name in ("source.json", "witness.json", "report.json"):
        path = run_dir / name
        original = path.read_bytes()
        _private(path, original + b" ")
        with pytest.raises(ReportInterfaceError):
            source.read_view()
        _private(path, original)
    assert source.read_view() == result.view
