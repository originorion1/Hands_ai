"""Development TLS renewal keeps a complete localhost pair under private custody."""

from __future__ import annotations

import os
import socket
import ssl
import threading
from datetime import UTC, datetime

import pytest
from cryptography import x509
from cryptography.x509 import DNSName, IPAddress

from deployment.release_candidate import renew_loopback_tls as renewal


def _root(tmp_path):
    root = tmp_path / "private"
    root.mkdir(mode=0o700)
    return root


def test_pair_is_atomic_private_and_hostname_scoped(tmp_path):
    root = _root(tmp_path)
    first = renewal.prepare(root, now=datetime(2026, 10, 17, tzinfo=UTC))
    old_target = (root / "current").resolve()
    assert first["service_restarted"] is False
    assert old_target.is_dir()
    assert old_target.stat().st_mode & 0o077 == 0
    cert = old_target / "cert.pem"
    key = old_target / "key.pem"
    assert cert.stat().st_mode & 0o077 == 0
    assert key.stat().st_mode & 0o077 == 0
    parsed = x509.load_pem_x509_certificate(cert.read_bytes())
    names = parsed.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
    assert names.get_values_for_type(DNSName) == ["localhost"]
    assert str(names.get_values_for_type(IPAddress)[0]) == "127.0.0.1"
    assert "BEGIN PRIVATE KEY" not in cert.read_text()
    second = renewal.prepare(root, now=datetime(2026, 10, 18, tzinfo=UTC))
    assert first["certificate_sha256"] != second["certificate_sha256"]
    assert (root / "current").resolve() != old_target
    assert old_target.joinpath("cert.pem").read_bytes() == cert.read_bytes()


def test_tls_verifies_localhost_and_denies_wrong_hostname(tmp_path):
    root = _root(tmp_path)
    renewal.prepare(root)
    target = root / "current"
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    listener.listen(2)
    port = listener.getsockname()[1]
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(target / "cert.pem", target / "key.pem")
    errors = []

    def server():
        for _ in range(2):
            conn, _peer = listener.accept()
            try:
                with server_context.wrap_socket(conn, server_side=True):
                    pass
            except ssl.SSLError as exc:
                errors.append(type(exc).__name__)
        listener.close()

    thread = threading.Thread(target=server)
    thread.start()
    client_context = ssl.create_default_context(cafile=str(target / "cert.pem"))
    with (socket.create_connection(("127.0.0.1", port)) as raw,
          client_context.wrap_socket(raw, server_hostname="localhost") as verified):
        assert verified.version() in {"TLSv1.2", "TLSv1.3"}
    with (socket.create_connection(("127.0.0.1", port)) as raw,
          pytest.raises(ssl.SSLCertVerificationError)):
        client_context.wrap_socket(raw, server_hostname="wrong.invalid")
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert errors


def test_unsafe_root_or_current_symlink_denied(tmp_path):
    root = _root(tmp_path)
    root.chmod(0o755)
    with pytest.raises(renewal.TLSCustodyDenied, match="permissions"):
        renewal.prepare(root)
    root.chmod(0o700)
    alias = tmp_path / "alias"
    alias.symlink_to(root)
    with pytest.raises(renewal.TLSCustodyDenied, match="root"):
        renewal.prepare(alias)
    (root / "current").symlink_to("/tmp/elsewhere")
    with pytest.raises(renewal.TLSCustodyDenied, match="escapes"):
        renewal.prepare(root)


def test_failed_pair_write_preserves_current_pair(tmp_path, monkeypatch):
    root = _root(tmp_path)
    renewal.prepare(root)
    original = os.readlink(root / "current")
    real_write = renewal._write_private

    def interrupted(path, data):
        if path.name == "key.pem":
            raise OSError("synthetic interrupted key write")
        real_write(path, data)

    monkeypatch.setattr(renewal, "_write_private", interrupted)
    with pytest.raises(OSError, match="interrupted"):
        renewal.prepare(root)
    assert os.readlink(root / "current") == original
    assert not list(root.glob("stage-*"))
