"""Fixed credential-use gateway; native HTTPS exists ONLY in its confined process.

This is not a general URL opener or relay. Custody redeems a durable one-use
reservation before the gateway creates curl. The ordinary source sees a normal
GET and source credential, never ORION receipts, grant tokens or control IPC.
"""

import hashlib
import json
import re
import subprocess
from urllib.parse import quote, urlencode

from .broker_contract import (
    INSTRUMENT_OPERATIONS,
    MAX_FRAME,
    digest,
    exact,
    grant_from,
    is_erpnext_candidate,
    metadata_grant_from,
    metadata_request_from,
    private_bytes,
    request_from,
    transition_policy_from,
    transition_record_config,
)
from .isolation import LAB_PATH, PORT, V4_APPROVED, V6_APPROVED
from .journal import JournalDenied

GATEWAY_PATHS = {"metadata": "/metadata", "read": "/records"} | {
    operation: "/instrument/" + str(n) for n, operation in enumerate(INSTRUMENT_OPERATIONS)
}
ERPNext_PROTOCOLS = {"metadata": "erpnext_metadata_v1", "read": "erpnext_records_v1"}


def erpnext_source_request(config, descriptor):
    """Encode one closed GET solely from a canonical config and exact request."""
    if not is_erpnext_candidate(config) or config.get("operation") not in ERPNext_PROTOCOLS:
        raise JournalDenied("ERPNext candidate scope denied")
    operation = config["operation"]
    if config.get("protocol") != ERPNext_PROTOCOLS[operation]:
        raise JournalDenied("ERPNext candidate protocol denied")
    if operation == "metadata":
        exact(descriptor, ("request", "target"))
        request = metadata_request_from(descriptor["request"])
        grant = metadata_grant_from(config["grant"])
        if request != grant.request:
            raise JournalDenied("ERPNext metadata scope denied")
        target = descriptor["target"]
        if target is None:
            query = urlencode({
                "fields": '["name"]',
                "limit_start": 0,
                "limit_page_length": grant.max_catalog_entries + 1,
                "order_by": "name asc",
            })
            return "/api/resource/DocType?" + query
        if (
            type(target) is not str
            or target in grant.excluded_resources
            or not target
            or len(target) > 256
            or any(character in target for character in "/?#")
        ):
            raise JournalDenied("ERPNext schema target denied")
        return "/api/method/frappe.desk.form.load.getdoctype?" + urlencode(
            {"doctype": target}
        )

    exact(descriptor, ("request",))
    request = request_from(descriptor["request"])
    grant = grant_from(config["grant"])
    window = grant.window
    if (
        request.source_id != grant.source_id
        or request.tenant_id != window.tenant_id
        or request.company != window.company
        or request.resource != window.resource
        or request.fields != window.fields
        or request.date_field != window.date_field
        or request.start != window.start
        or request.end != window.end
        or request.max_records > grant.max_records
    ):
        raise JournalDenied("ERPNext record scope denied")
    query = urlencode({
        "fields": json.dumps(list(request.fields), separators=(",", ":")),
        "filters": json.dumps(
            [[grant.company_field, "=", request.company], ["docstatus", "=", 1]]
            + window.filters(),
            separators=(",", ":"),
        ),
        "order_by": f"{request.date_field} desc, {grant.identity_field} desc",
        "limit_start": 0,
        "limit_page_length": request.max_records,
    })
    return "/api/resource/" + quote(request.resource, safe="") + "?" + query


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

    def __init__(self, configs, client, credential, certificate, certificate_sha256, host,
                 *, transition=None):
        if host not in (V4_APPROVED, V6_APPROVED):
            raise ValueError("explicit synthetic destination required")
        candidate = all(is_erpnext_candidate(config) for config in configs)
        legacy = all(not is_erpnext_candidate(config) for config in configs)
        if not (candidate or legacy):
            raise ValueError("one versioned gateway mode required")
        if (
            type(credential) is not str
            or not credential.isascii()
            or not 16 <= len(credential) <= 256
            or (
                candidate
                and not re.fullmatch(r"[A-Za-z0-9_-]{8,128}:[A-Za-z0-9_-]{8,128}", credential)
            )
            or (legacy and not credential.isalnum())
        ):
            raise ValueError("bounded credential required")

        self.transition = (
            transition_policy_from(transition) if transition is not None else None
        )
        operations = [c["operation"] for c in configs]
        expected = ["metadata"] if self.transition is not None and len(configs) == 1 else [
            "metadata", "read"
        ]
        if (
            not len(expected) <= len(configs) <= len(GATEWAY_PATHS)
            or operations[:len(expected)] != expected
            or len(set(operations)) != len(operations)
            or any(operation not in GATEWAY_PATHS for operation in operations)
        ):
            raise ValueError("separate exact scopes required")
        self.configs = {digest(c): c for c in configs}
        self.routes = {
            digest(c): (None if candidate else GATEWAY_PATHS[c["operation"]]) for c in configs
        }
        self.response_limits = {
            digest(c): min(MAX_FRAME // 2, c["limits"]["response_bytes"] // 2) for c in configs
        }
        if len(self.routes) != len(configs):
            raise ValueError("separate exact scopes required")
        self.client, self.credential = client, credential
        self.candidate = candidate
        self.certificate, self.certificate_sha256, self.host = certificate, certificate_sha256, host
        self.check_certificate()

    def provision(self, config):
        transition_record_config(config, self.transition)
        binding = digest(config)
        if binding in self.routes or any(
            existing["operation"] == "read" for existing in self.configs.values()
        ):
            raise JournalDenied("duplicate gateway grant transition denied")
        self.configs[binding] = config
        self.routes[binding] = None
        self.response_limits[binding] = min(
            MAX_FRAME // 2, config["limits"]["response_bytes"] // 2
        )
        return {"status": "gateway_committed", "binding": binding}

    def check_certificate(self):
        if hashlib.sha256(private_bytes(self.certificate)).hexdigest() != self.certificate_sha256:
            raise JournalDenied("gateway trust changed")

    def dispatch(self, role, action, value):
        if role == "owner" and action == "provision":
            return self.provision(value)
        if role != "acquisition" or action != "acquire":
            raise JournalDenied("credential gateway caller denied")
        keys = ("receipt", "binding", "source_request") if self.candidate else (
            "receipt", "binding"
        )
        exact(value, keys)
        if value["binding"] not in self.routes:
            raise JournalDenied("credential gateway scope denied")
        self.check_certificate()
        path = (
            erpnext_source_request(self.configs[value["binding"]], value["source_request"])
            if self.candidate
            else self.routes[value["binding"]]
        )
        redemption = {"receipt": value["receipt"], "binding": value["binding"]}
        if self.candidate:
            redemption["source_request_sha256"] = digest(value["source_request"])
        approval = self.client("redeem", redemption)
        if approval != {"authorized": True, "binding": value["binding"]}:
            raise JournalDenied("credential use unauthorized")
        raw = self.read(path, limit=self.response_limits[value["binding"]])
        return {"body": raw.hex()}

    def read(self, path, *, limit=MAX_FRAME // 2):
        if (
            type(path) is not str
            or not path.startswith("/")
            or "#" in path
            or (not self.candidate and path not in self.routes.values())
        ):
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
        scheme = "token " if self.candidate else "Bearer "
        configuration = ('header = "Authorization: ' + scheme + self.credential + '"\n').encode()
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
