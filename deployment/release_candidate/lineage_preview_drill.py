"""Serve one completed synthetic lineage through the real local Vercel routes.

All listeners bind loopback. This drill creates an ephemeral identity key and
TLS certificate, and removes credentials on exit. It never contacts an ERP or
the protected public Preview. Invoke only with --authorize-synthetic-read.
"""

from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import signal
import ssl
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)
from cryptography.x509.oid import NameOID

from deployment import local_preview
from deployment.release_candidate.continuous_lineage import _write_private, run_synthetic_lineage
from deployment.vercel import build_static
from orion.contracts import EvidenceKind
from orion.discovery.pilot_read import PilotAuthorization, PilotRequest
from orion.discovery.read_window import ReviewedReadWindow
from orion.presentation.report_interface import BoundReportSource
from orion_connector_sdk import ReviewedConnector, discover
from orion_connector_synthetic import SyntheticRecordConnector
from orion_http.report_server import create_server


def _b64(body: bytes) -> str:
    return base64.urlsafe_b64encode(body).rstrip(b"=").decode("ascii")


def _token(key: Ed25519PrivateKey, tenant: str, now: datetime) -> str:
    current = int(now.timestamp())
    header = _b64(b'{"alg":"EdDSA","typ":"JWT"}')
    payload = _b64(json.dumps({
        "iss": "https://identity.synthetic.invalid", "aud": "orion-lineage",
        "sub": "synthetic-reader", "tenant_id": tenant, "scope": "orion:report:read",
        "iat": current, "nbf": current, "exp": current + 300,
    }, sort_keys=True, separators=(",", ":")).encode())
    body = f"{header}.{payload}"
    return f"{body}.{_b64(key.sign(body.encode()))}"


def _certificate(private_dir: Path, now: datetime) -> tuple[Path, Path]:
    key = Ed25519PrivateKey.generate()
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    certificate = (x509.CertificateBuilder()
                   .subject_name(name).issuer_name(name)
                   .public_key(key.public_key()).serial_number(x509.random_serial_number())
                   .not_valid_before(now - timedelta(minutes=1))
                   .not_valid_after(now + timedelta(days=1))
                   .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]),
                                  critical=False)
                   .sign(key, algorithm=None))
    cert_path, key_path = private_dir / "local-cert.pem", private_dir / "local-key.pem"
    _write_private(cert_path, certificate.public_bytes(Encoding.PEM))
    _write_private(key_path, key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8,
                                               NoEncryption()))
    return cert_path, key_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run one offline synthetic lineage Preview")
    parser.add_argument("--private-dir", required=True, type=Path)
    parser.add_argument("--authorize-synthetic-read", action="store_true")
    args = parser.parse_args(argv)
    if not args.authorize_synthetic_read:
        parser.error("explicit synthetic read authorization is required")
    private_dir = args.private_dir
    if not private_dir.is_absolute() or private_dir.exists():
        parser.error("an unused absolute private directory is required")
    private_dir.mkdir(mode=0o700)
    now = datetime.now(UTC)
    run_id = uuid4()
    short = run_id.hex[:10]
    tenant = f"synthetic-tenant-{short}"
    source_id = f"synthetic-source-{short}"
    date = now.date()
    records = (
        {"name": f"SYN-{short}-1", "company": "Synthetic Co",
         "posting_date": date.isoformat(), "amount": 37},
        {"name": f"SYN-{short}-2", "company": "Synthetic Co",
         "posting_date": date.isoformat(), "amount": 50},
    )
    connector = SyntheticRecordConnector(
        tenant_id=tenant, source_id=source_id, dataset_id=f"dataset-{short}",
        records=records,
    )
    fields = ("name", "company", "posting_date", "amount")
    window = ReviewedReadWindow(
        tenant_id=tenant, company="Synthetic Co", resource="Synthetic Invoice",
        fields=fields, date_field="posting_date", start=date, end=date,
        expires_at=now + timedelta(minutes=10),
    )
    authorization_id = f"synthetic-read-{short}"
    grant = PilotAuthorization(
        authorization_id=authorization_id, source_id=source_id, window=window,
        identity_field="name", company_field="company",
        provenance_source="synthetic-record-v1", evidence_kind=EvidenceKind.SOURCE,
        max_records=2,
    )
    request = PilotRequest(
        tenant_id=tenant, company="Synthetic Co", source_id=source_id,
        resource="Synthetic Invoice", fields=fields, date_field="posting_date",
        start=date, end=date, max_records=2,
    )
    registration = discover(ReviewedConnector(
        name="synthetic-records", distribution="originorion-connector-synthetic",
        version="0.2.0rc2",
        entry_point="orion_connector_synthetic:SyntheticRecordConnector",
    ))
    result = run_synthetic_lineage(
        registration=registration, connector=connector, request=request,
        authorization_id=authorization_id,
        lookup=lambda identity: grant if identity == authorization_id else None,
        run_id=run_id, private_dir=private_dir, now=now,
    )
    identity_key = Ed25519PrivateKey.generate()
    _write_private(private_dir / "identity-key.pem", identity_key.private_bytes(
        Encoding.PEM, PrivateFormat.PKCS8, NoEncryption()))
    _write_private(private_dir / "token", (_token(identity_key, tenant, now) + "\n").encode())
    _write_private(private_dir / "policy.json", json.dumps({
        "policy_version": "orion.report-access.v1",
        "issuer": "https://identity.synthetic.invalid", "audience": "orion-lineage",
        "tenant_id": tenant, "allowed_subjects": ["synthetic-reader"],
        "ed25519_public_key": _b64(identity_key.public_key().public_bytes(
            Encoding.Raw, PublicFormat.Raw)),
    }).encode())
    service_token = secrets.token_urlsafe(32)
    _write_private(private_dir / "service-token", service_token.encode())
    cert_path, key_path = _certificate(private_dir, now)
    os.environ["SSL_CERT_FILE"] = str(cert_path)
    source = BoundReportSource.from_private_binding(result.binding_path)
    report_server = create_server(
        source, private_dir / "policy.json", port=0,
        service_token_path=private_dir / "service-token",
    )
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=cert_path, keyfile=key_path)
    report_server.socket = context.wrap_socket(report_server.socket, server_side=True)
    os.environ["ORION_REPORT_BACKEND_URL"] = (
        f"https://localhost:{report_server.server_port}/api/v1/report"
    )
    os.environ["ORION_REPORT_SERVICE_TOKEN"] = service_token
    build_static.main()
    preview = local_preview.create_server(port=0)
    report_thread = threading.Thread(target=report_server.serve_forever, daemon=True)
    preview_thread = threading.Thread(target=preview.serve_forever, daemon=True)
    report_thread.start()
    preview_thread.start()
    stop = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    print(json.dumps({
        "url": f"http://127.0.0.1:{preview.server_port}/",
        "token_path": str(private_dir / "token"),
        "tenant_id": tenant, "run_id": str(run_id), "dataset_id": result.dataset_id,
        "source_sha256": result.source_sha256, "report_sha256": result.report_sha256,
        "witness_sha256": result.witness_sha256,
        "evidence_ids": list(result.evidence_ids),
        "local_tls": True, "execution_allowed": False,
    }), flush=True)
    try:
        stop.wait()
    finally:
        preview.shutdown()
        report_server.shutdown()
        preview.server_close()
        report_server.server_close()
        preview_thread.join(timeout=2)
        report_thread.join(timeout=2)
        for name in ("token", "identity-key.pem", "local-key.pem", "service-token"):
            (private_dir / name).unlink(missing_ok=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
