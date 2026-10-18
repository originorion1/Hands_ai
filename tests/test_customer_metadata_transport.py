"""A synthetic TLS endpoint verifies the qualified metadata transport."""

from __future__ import annotations

import ssl
import threading
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import ClassVar
from urllib.request import Request

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric.rsa import generate_private_key
from cryptography.hazmat.primitives.serialization import Encoding, NoEncryption, PrivateFormat
from cryptography.x509.oid import NameOID

from deployment.customer.bounded_metadata import MetadataSessionDenied
from deployment.customer.metadata_transport import ReviewedMetadataTransport


class Handler(BaseHTTPRequestHandler):
    seen: ClassVar[list[str]] = []

    def do_GET(self):
        self.seen.append(self.path)
        if "Redirect" in self.path:
            self.send_response(302)
            self.send_header("Location", "/forbidden")
            self.end_headers()
            return
        self.send_response(200)
        self.end_headers()
        self.wfile.write(b'{"message":{"docs":[]}}')

    def log_message(self, *_):
        pass


@pytest.fixture
def synthetic_https(tmp_path):
    key = generate_private_key(public_exponent=65537, key_size=2048)
    name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "localhost")])
    now = datetime.now(UTC)
    cert = (x509.CertificateBuilder().subject_name(name).issuer_name(name)
            .public_key(key.public_key()).serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(minutes=1))
            .not_valid_after(now + timedelta(minutes=10))
            .add_extension(x509.SubjectAlternativeName([x509.DNSName("localhost")]),
                           critical=False)
            .sign(key, hashes.SHA256()))
    cert_path, key_path = tmp_path / "cert.pem", tmp_path / "key.pem"
    cert_path.write_bytes(cert.public_bytes(Encoding.PEM))
    key_path.write_bytes(key.private_bytes(Encoding.PEM, PrivateFormat.PKCS8,
                                           NoEncryption()))
    Handler.seen = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(str(cert_path), str(key_path))
    server.socket = context.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"https://localhost:{server.server_port}", cert_path
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def _request(origin: str, doctype: str = "Invoice") -> Request:
    return Request(origin + "/api/method/frappe.desk.form.load.getdoctype?doctype="
                   + doctype, method="GET")


def test_direct_tls_and_no_redirect_before_second_request(synthetic_https, monkeypatch):
    origin, cert = synthetic_https
    monkeypatch.setenv("https_proxy", "http://127.0.0.1:1")
    transport = ReviewedMetadataTransport(origin, cert)
    with transport(_request(origin), timeout=20) as response:
        assert response.geturl() == _request(origin).full_url
        assert response.read(128) == b'{"message":{"docs":[]}}'
    assert len(Handler.seen) == 1
    with pytest.raises(MetadataSessionDenied, match="transport failed"):
        transport(_request(origin, "Redirect"), timeout=20)
    assert len(Handler.seen) == 2
    assert all("forbidden" not in path for path in Handler.seen)


def test_unreviewed_destination_method_and_tls_are_denied(synthetic_https):
    origin, cert = synthetic_https
    transport = ReviewedMetadataTransport(origin, cert)
    with pytest.raises(MetadataSessionDenied, match="destination"):
        transport(_request("https://other.invalid"), timeout=20)
    with pytest.raises(MetadataSessionDenied, match="bounded GET"):
        transport(Request(_request(origin).full_url, method="POST"), timeout=20)
    with pytest.raises(MetadataSessionDenied, match="query"):
        transport(Request(origin + "/api/method/frappe.desk.form.load.getdoctype?all=1"),
                  timeout=20)
    with pytest.raises(MetadataSessionDenied, match="transport failed"):
        ReviewedMetadataTransport(origin)(_request(origin), timeout=20)
    assert Handler.seen == []
