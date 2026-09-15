"""Fixed credential-use gateway; native HTTPS exists ONLY in its confined process.

This is not a general URL opener or relay. Custody redeems a durable one-use
reservation before the gateway creates curl. The ordinary source sees a normal
GET and source credential, never ORION receipts, grant tokens or control IPC.
"""

import hashlib
import subprocess

from .broker_contract import MAX_FRAME, exact, private_bytes
from .isolation import LAB_PATH, PORT, V4_APPROVED, V6_APPROVED
from .journal import JournalDenied


def response_length(status, headers, limit):
    """One authoritative strict response-framing policy, shared by TLS fixtures."""
    lengths = [v for k, v in headers if k.lower() == "content-length"]
    if (
        type(limit) is not int
        or not 1 <= limit <= MAX_FRAME // 2
        or status != 200
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
    return size


class CredentialGateway:
    """Trusted gateway owner; construct only after independent kernel confinement."""

    def __init__(self, configs, client, credential, certificate, certificate_sha256, host):
        if host not in (V4_APPROVED, V6_APPROVED):
            raise ValueError("explicit synthetic destination required")
        if type(credential) is not str or not credential.isascii() or not credential.isalnum():
            raise ValueError("bounded credential required")
        if not 16 <= len(credential) <= 256:
            raise ValueError("bounded credential required")
        from .broker_contract import digest

        self.routes = {
            digest(c): "/metadata" if c["operation"] == "metadata" else "/records" for c in configs
        }
        self.response_limits = {
            digest(c): min(MAX_FRAME // 2, c["limits"]["response_bytes"] // 2) for c in configs
        }
        if len(self.routes) != 2 or {c["operation"] for c in configs} != {"metadata", "read"}:
            raise ValueError("separate exact scopes required")
        self.client, self.credential = client, credential
        self.certificate, self.certificate_sha256, self.host = certificate, certificate_sha256, host
        self.check_certificate()

    def check_certificate(self):
        if hashlib.sha256(private_bytes(self.certificate)).hexdigest() != self.certificate_sha256:
            raise JournalDenied("gateway trust changed")

    def dispatch(self, role, action, value):
        if role != "acquisition" or action != "acquire":
            raise JournalDenied("credential gateway caller denied")
        exact(value, ("receipt", "binding"))
        if value["binding"] not in self.routes:
            raise JournalDenied("credential gateway scope denied")
        self.check_certificate()
        approval = self.client("redeem", value)
        if approval != {"authorized": True, "binding": value["binding"]}:
            raise JournalDenied("credential use unauthorized")
        raw = self.read(self.routes[value["binding"]], limit=self.response_limits[value["binding"]])
        return {"body": raw.hex()}

    def read(self, path, *, limit=MAX_FRAME // 2):
        if path not in ("/metadata", "/records"):
            raise JournalDenied("fixed gateway path required")
        if type(limit) is not int or not 1 <= limit <= MAX_FRAME // 2:
            raise JournalDenied("fixed gateway response budget required")
        host = "[" + self.host + "]" if ":" in self.host else self.host
        # --disable is FIRST: no curlrc. No redirects, DNS, proxies, caller headers,
        # writes or body. Credential travels only on anonymous stdin, never argv.
        argv = [
            "/usr/bin/curl",
            "--disable",
            "--config",
            "-",
            "--silent",
            "--http1.1",
            "--proto",
            "=https",
            "--proto-redir",
            "=https",
            "--max-redirs",
            "0",
            "--noproxy",
            "*",
            "--proxy",
            "",
            "--connect-timeout",
            "1",
            "--max-time",
            "1",
            "--max-filesize",
            str(limit),
            "--tlsv1.2",
            "--cacert",
            self.certificate,
            "--request",
            "GET",
            "--include",
            "--header",
            "Accept: application/json",
            "--header",
            "Connection: close",
            f"https://{host}:{PORT}{path}",
        ]
        configuration = ('header = "Authorization: Bearer ' + self.credential + '"\n').encode()
        process = subprocess.Popen(
            argv,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            close_fds=True,
            env={"PATH": LAB_PATH, "LANG": "C.UTF-8"},
        )
        try:
            process.stdin.write(configuration)
            process.stdin.close()
            # Independently bound even a source that lies about Content-Length.
            wire = process.stdout.read(limit + 16385)
            if len(wire) > limit + 16384:
                raise JournalDenied("gateway response oversized")
            if process.wait(timeout=2) != 0:
                raise JournalDenied("gateway HTTPS denied")
        finally:
            if process.poll() is None:
                process.kill()
            process.wait(timeout=2)
            process.stdout.close()
            if not process.stdin.closed:
                process.stdin.close()
        header, separator, body = wire.partition(b"\r\n\r\n")
        if not separator or len(header) > 16384:
            raise JournalDenied("gateway framing denied")
        lines = header.decode("ascii").split("\r\n")
        if not lines[0].startswith(("HTTP/1.1 200 ", "HTTP/1.0 200 ")):
            raise JournalDenied("gateway status denied")
        headers = []
        for line in lines[1:]:
            name, colon, value = line.partition(":")
            if not colon or not name or line[:1].isspace():
                raise JournalDenied("gateway headers denied")
            headers.append((name, value.strip()))
        size = response_length(200, headers, limit)
        if len(body) != size:
            raise JournalDenied("gateway body denied")
        return body
