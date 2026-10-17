"""Start, fault, restart and measure an installed synthetic report host.

Run from a clean venv containing hash-verified ORION wheels. No existing
service, customer network or public tunnel is touched.
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import http.client
import json
import os
import re
import secrets
import socket
import subprocess
import sys
import tempfile
import time
from datetime import UTC, datetime, timedelta
from importlib.metadata import version
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from orion import __version__
from orion.discovery.erpnext_live_session import LiveSessionRunReport, live_session_report_json
from orion.presentation.report_interface import REPORT_INTERFACE_VERSION
from orion_http import report_server


def _private(path: Path, data: bytes) -> None:
    path.write_bytes(data)
    path.chmod(0o600)


def _b64(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _token(key: Ed25519PrivateKey, *, tenant: str, expires_at: int) -> str:
    now = int(time.time())
    header = _b64(b'{"alg":"EdDSA","typ":"JWT"}')
    claims = {
        "iss": "https://identity.example.test",
        "aud": "orion-isolated-drill",
        "sub": "synthetic-reviewer",
        "tenant_id": tenant,
        "scope": "orion:report:read",
        "iat": now,
        "nbf": now,
        "exp": expires_at,
    }
    body = _b64(json.dumps(claims, sort_keys=True, separators=(",", ":")).encode())
    signing_input = f"{header}.{body}"
    return f"{signing_input}.{_b64(key.sign(signing_input.encode()))}"


def _report() -> bytes:
    start = datetime(2036, 1, 1, tzinfo=UTC)
    value = LiveSessionRunReport(
        session_started_at=start,
        session_ended_at=start + timedelta(minutes=5),
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
    return (live_session_report_json(value) + "\n").encode()


def _request(port: int, path: str, *, service: str | None, token: str | None = None):
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=2)
    headers = {}
    if service is not None:
        headers["X-ORION-Service-Token"] = service
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    try:
        connection.request("GET", path, headers=headers)
        response = connection.getresponse()
        return response.status, response.read()
    finally:
        connection.close()


def _free_port() -> int:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return listener.getsockname()[1]


def _start(private: Path, port: int) -> subprocess.Popen:
    environment = dict(os.environ)
    environment.pop("PYTHONPATH", None)
    process = subprocess.Popen(
        [
            sys.executable, "-m", "orion_http.report_server",
            "--binding", str(private / "binding.json"),
            "--access-policy", str(private / "policy.json"),
            "--service-token-file", str(private / "service-token"),
            "--port", str(port),
        ],
        cwd=private,
        env=environment,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise RuntimeError("isolated report process exited before readiness")
        try:
            if _request(port, "/ready", service=(private / "service-token").read_text())[0] == 200:
                return process
        except OSError:
            pass
        time.sleep(0.02)
    process.kill()
    process.wait(timeout=2)
    raise RuntimeError("isolated report process did not become ready")


def _expect(actual: int, expected: int, label: str) -> None:
    if actual != expected:
        raise RuntimeError(f"{label}: expected HTTP {expected}, received {actual}")


def run(manifest_path: Path) -> dict:
    manifest = json.loads(manifest_path.read_text())
    module_digest = hashlib.sha256(Path(report_server.__file__).read_bytes()).hexdigest()
    if (
        type(manifest.get("source_sha")) is not str
        or re.fullmatch(r"[0-9a-f]{40}", manifest["source_sha"]) is None
        or type(manifest.get("tree")) is not str
        or re.fullmatch(r"[0-9a-f]{40}", manifest["tree"]) is None
        or manifest.get("version") != __version__
        or manifest.get("report_host_distribution") != "originorion-core"
        or manifest.get("report_host_version") != version("originorion-core")
        or manifest.get("report_server_module_sha256") != module_digest
    ):
        raise RuntimeError("installed release identity differs from manifest")
    with tempfile.TemporaryDirectory(prefix="orion-rc-drill-") as directory:
        private = Path(directory)
        private.chmod(0o700)
        key = Ed25519PrivateKey.generate()
        service = secrets.token_urlsafe(32)
        report = _report()
        digest = hashlib.sha256(report).hexdigest()
        _private(private / "report.json", report)
        _private(private / "service-token", service.encode())
        _private(private / "binding.json", json.dumps({
            "binding_version": REPORT_INTERFACE_VERSION,
            "tenant_id": "synthetic-drill",
            "report_path": str(private / "report.json"),
            "report_sha256": digest,
        }).encode())
        policy = {
            "policy_version": "orion.report-access.v1",
            "issuer": "https://identity.example.test",
            "audience": "orion-isolated-drill",
            "tenant_id": "synthetic-drill",
            "allowed_subjects": ["synthetic-reviewer"],
            "ed25519_public_key": _b64(key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)),
        }
        _private(private / "policy.json", json.dumps(policy).encode())
        valid = _token(key, tenant="synthetic-drill", expires_at=int(time.time()) + 300)
        forged = _token(Ed25519PrivateKey.generate(), tenant="synthetic-drill", expires_at=int(time.time()) + 300)
        expired = _token(key, tenant="synthetic-drill", expires_at=int(time.time()) - 1)
        wrong_tenant = _token(key, tenant="other-synthetic", expires_at=int(time.time()) + 300)
        port = _free_port()
        process = _start(private, port)
        result = None
        try:
            _expect(_request(port, "/health", service=service)[0], 200, "health")
            _expect(_request(port, "/ready", service=service)[0], 200, "readiness")
            _expect(_request(port, "/ready", service=None)[0], 401, "missing service credential")
            for label, token in (
                ("missing report token", None),
                ("forged report token", forged),
                ("expired report token", expired),
                ("wrong-tenant report token", wrong_tenant),
            ):
                _expect(_request(port, "/api/v1/report", service=service, token=token)[0], 401, label)
            status, body = _request(port, "/api/v1/report", service=service, token=valid)
            _expect(status, 200, "authorized report")
            view = json.loads(body)
            if view["observations_persisted"] != 4 or view["authority"]["execution_allowed"] is not False:
                raise RuntimeError("synthetic report authority mismatch")
            policy["allowed_subjects"] = ["other-synthetic-reviewer"]
            _private(private / "policy.json", json.dumps(policy).encode())
            _expect(_request(port, "/api/v1/report", service=service, token=valid)[0], 401, "revoked subject")
            policy["allowed_subjects"] = ["synthetic-reviewer"]
            _private(private / "policy.json", json.dumps(policy).encode())
            _private(private / "report.json", b"tampered synthetic report")
            _expect(_request(port, "/ready", service=service)[0], 503, "tampered report")
            _private(private / "report.json", report)
            _expect(_request(port, "/ready", service=service)[0], 200, "recovered report")
            durations = []
            for _ in range(20):
                start = time.monotonic()
                _expect(_request(port, "/api/v1/report", service=service, token=valid)[0], 200, "latency sample")
                durations.append((time.monotonic() - start) * 1000)
            process.kill()
            process.wait(timeout=5)
            restart_at = time.monotonic()
            process = _start(private, port)
            recovery_ms = (time.monotonic() - restart_at) * 1000
            _expect(_request(port, "/api/v1/report", service=service, token=valid)[0], 200, "restart report")
            if hashlib.sha256((private / "report.json").read_bytes()).hexdigest() != digest:
                raise RuntimeError("report digest changed across restart")
            result = {
                "source_sha": manifest["source_sha"],
                "tree": manifest.get("tree"),
                "version": __version__,
                "report_host_distribution": manifest["report_host_distribution"],
                "report_host_version": manifest["report_host_version"],
                "service_bind": "127.0.0.1",
                "tests": "PASS",
                "observations": 4,
                "execution_allowed": False,
                "data_loss_bytes": 0,
                "recovery_ms": round(recovery_ms, 2),
                "p95_report_latency_ms": round(sorted(durations)[18], 2),
            }
        finally:
            if process.poll() is None:
                graceful_start = time.monotonic()
                process.terminate()
                process.wait(timeout=5)
                graceful_ms = (time.monotonic() - graceful_start) * 1000
                if result is not None:
                    if process.returncode != 0:
                        raise RuntimeError("isolated service did not shut down gracefully")
                    result["graceful_shutdown_ms"] = round(graceful_ms, 2)
        return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    args = parser.parse_args()
    print(json.dumps(run(args.manifest), sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
