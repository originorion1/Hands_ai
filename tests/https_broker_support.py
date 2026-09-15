"""Reusable synthetic HTTPS broker fixtures; no production transport selector."""

import hashlib
import hmac
import http.client
import ipaddress
import socket
import ssl
import subprocess
import tempfile
import threading
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from orion.pilot.broker import Broker
from orion.pilot.broker_contract import (
    MAX_FRAME,
    authenticate,
    decode,
    exact,
    private_bytes,
)

HOST = "127.0.0.1"
TARGET = "/fixture"


def certificates(root, *, hostname=HOST):
    """Ephemeral local test certificate; no committed key or external CA."""
    cert, key = root / "cert.pem", root / "key.pem"
    subprocess.run(
        [
            "openssl",
            "req",
            "-x509",
            "-newkey",
            "rsa:2048",
            "-nodes",
            "-keyout",
            str(key),
            "-out",
            str(cert),
            "-days",
            "1",
            "-subj",
            "/CN=local-fixture",
            "-addext",
            "subjectAltName=" + _certificate_name(hostname),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    cert.chmod(0o600)
    key.chmod(0o600)
    return cert, key


def _certificate_name(hostname):
    try:
        ipaddress.ip_address(hostname)
    except ValueError:
        return "DNS:" + hostname
    return "IP:" + hostname


@dataclass(frozen=True)
class LocalPolicy:
    port: int
    certificate: str
    certificate_sha256: str
    host: str = HOST

    def context(self):
        if type(self.host) is not str or "%" in self.host:
            raise ValueError("numeric fixture host required")
        ipaddress.ip_address(self.host)  # Signed numeric fixture route, never DNS.
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError("invalid fixture port")
        pem = private_bytes(self.certificate)
        if hashlib.sha256(pem).hexdigest() != self.certificate_sha256:
            raise ValueError("fixture trust changed")
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_verify_locations(cadata=pem.decode("ascii"))
        return context

    def authorize_wire(self, *, host, port, method, target, body):
        if (
            host != self.host
            or type(port) is not int
            or port != self.port
            or method != "GET"
            or target != TARGET
            or body is not None
        ):
            raise ValueError("fixed read-only loopback route required")


def bounded_body(response, limit):
    """No redirects, transfer coding, compression or ambiguous response framing."""
    if type(limit) is not int or not 1 <= limit <= MAX_FRAME // 2:
        raise ValueError("bounded local response required")
    headers = response.getheaders()
    lengths = [v for k, v in headers if k.lower() == "content-length"]
    if (
        response.status != 200
        or len(lengths) != 1
        or not lengths[0].isascii()
        or not lengths[0].isdecimal()
        or len(lengths[0]) > 6
        or any(
            k.lower() in ("transfer-encoding", "content-encoding", "location") for k, _ in headers
        )
    ):
        raise ValueError("response policy denied")
    size = int(lengths[0])
    if not 1 <= size <= limit:
        raise ValueError("response size denied")
    data = response.read(size + 1)
    if type(data) is not bytes or len(data) != size:
        raise ValueError("partial or oversized response")
    return data


def https_read(policy, secret, limit):
    # No URL parsing, DNS name, proxy environment, redirects, caller headers or body.
    policy.authorize_wire(host=policy.host, port=policy.port, method="GET", target=TARGET, body=None)
    context = policy.context()
    if type(secret) is not str or not secret.isascii() or not secret.isalnum():
        raise ValueError("bounded fixture credential required")
    with closing(
        http.client.HTTPSConnection(policy.host, policy.port, timeout=1, context=context)
    ) as connection:
        connection.request(
            "GET",
            TARGET,
            headers={
                "Authorization": "Bearer " + secret,
                "Accept": "application/json",
                "Connection": "close",
            },
        )
        response = connection.getresponse()
        return bounded_body(response, limit)


class HTTPSBroker(Broker):
    """Test-only fixed supervisor. Existing application API stays unchanged."""

    def __init__(self, config, directory, **kwargs):
        super().__init__(config, directory, **kwargs)
        if self.operation != "read":
            raise ValueError("record fixture required")
        self.profile_path = Path(self.config["source_path"]).parent / "tls-profile.json"
        self.profile_bytes = private_bytes(self.profile_path)
        envelope = exact(decode(self.profile_bytes), ("profile", "mac"))
        fields = ("binding", "port", "certificate", "certificate_sha256")
        if type(envelope["profile"]) is dict and "host" in envelope["profile"]:
            fields += ("host",)
        profile = exact(envelope["profile"], fields)
        if (
            profile["binding"] != self.binding
            or type(envelope["mac"]) is not str
            or not hmac.compare_digest(
                envelope["mac"], authenticate(self.key, "local_https_fixture", profile)
            )
        ):
            raise ValueError("fixture supervisor route denied")
        self.policy = LocalPolicy(
            **{k: profile[k] for k in fields if k != "binding"}
        )
        self.policy.context()

    def _worker(self, bootstrap, *, timeout=5):
        # Broker's canonical permit and durable reservation precede this method.
        if (
            not self.armed
            or self.stopped()
            or not self.journal.inspect()["pending"]
            or private_bytes(self.profile_path) != self.profile_bytes
        ):
            raise ValueError("fixture acquisition denied")
        raw = https_read(
            self.policy, self.secret, min(MAX_FRAME // 2, self.limits.response_bytes // 2)
        )
        if hashlib.sha256(raw).hexdigest() != self.config["source_digest"]:
            raise ValueError("fixture content changed")
        # Normalize the exact received bytes through the unchanged sealed worker,
        # never substitute the owner's pre-existing source file for the response.
        with tempfile.TemporaryDirectory(prefix="orion-tls-received-") as temporary:
            path = Path(temporary) / "received.json"
            path.write_bytes(raw)
            path.chmod(0o600)
            result = super()._worker(dict(bootstrap, path=str(path)), timeout=timeout)
        if len(raw) + len(result.stdout) > self.limits.response_bytes:
            raise ValueError("combined response reservation exceeded")
        return result


class FixtureServer:
    def __init__(self, cert, key, body, secret, behavior="ok", *, port=0, host=HOST,
                 redirect="https://attacker.invalid/"):
        if type(port) is not int or not 0 <= port <= 65535:
            raise ValueError("invalid fixture listener")
        address = ipaddress.ip_address(host)
        if not redirect.isascii() or "\r" in redirect or "\n" in redirect:
            raise ValueError("invalid synthetic redirect")
        self.body, self.secret, self.behavior = body, secret, behavior
        self.redirect = redirect
        self.requests = []
        self.listener = socket.socket(socket.AF_INET if address.version == 4 else socket.AF_INET6,
                                      socket.SOCK_STREAM)
        self.listener.bind((host, port))
        self.listener.listen(4)
        self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.load_cert_chain(str(cert), str(key))
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                connection.settimeout(1)
                with self.context.wrap_socket(connection, server_side=True) as peer:
                    request = b""
                    while b"\r\n\r\n" not in request and len(request) <= 4096:
                        part = peer.recv(1024)
                        if not part:
                            break
                        request += part
                    authorized = (
                        b"Authorization: Bearer " + self.secret.encode() + b"\r\n" in request
                    )
                    self.requests.append(
                        {
                            "get": request.startswith(b"GET /fixture HTTP/1.1\r\n"),
                            "authorized": authorized,
                        }
                    )  # Never retain the credential.
                    if not authorized or not self.requests[-1]["get"]:
                        peer.sendall(b"HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n")
                    elif self.behavior == "redirect":
                        peer.sendall(
                            b"HTTP/1.1 302 Found\r\nLocation: " + self.redirect.encode("ascii")
                            + b"\r\nContent-Length: 0\r\n\r\n"
                        )
                    elif self.behavior == "oversized":
                        peer.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 999999\r\n\r\n")
                    elif self.behavior == "partial":
                        peer.sendall(b"HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{}")
                    elif self.behavior == "timeout":
                        self.stop.wait(1.2)
                    else:
                        peer.sendall(
                            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: "
                            + str(len(self.body)).encode()
                            + b"\r\n\r\n"
                            + self.body
                        )
            except (OSError, ssl.SSLError):
                connection.close()

    def close(self):
        self.stop.set()
        self.listener.close()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise ValueError("fixture server shutdown failed")
