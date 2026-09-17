"""Installed fixed process roles for the bounded synthetic read-only deployment.

No fixture imports, plugins, public child selectors or source networking in the
authorization/audit/archive/acquisition/reasoning roles. Only the credential
gateway has a preconfigured private kernel HTTPS tuple.
"""

import hashlib
import os
import sys
from pathlib import Path

from ..understanding.role_checkpoint import _json
from .broker_contract import MAX_FRAME, decode, digest, private_bytes
from .custody import AuditCustody, AuthorizationCustody, RemoteJournal, RuntimeCustody
from .evidence_custody import EvidenceCustody
from .gateway import CredentialGateway
from .ipc import Endpoint, rpc
from .isolation import net_child
from .journal import JournalDenied
from .progress_witness import ProgressWitness, stream_for
from .runtime import SupervisedReadOnlyRuntime
from .semantic_runtime import RuntimeSemanticCustody


def frame():
    raw = sys.stdin.buffer.readline(MAX_FRAME + 1)
    if len(raw) > MAX_FRAME or not raw.endswith(b"\n"):
        raise ValueError("private bootstrap framing denied")
    return decode(raw)


def capabilities(path):
    data = decode(private_bytes(path))
    if type(data) is not dict or any(
        type(k) is not str or type(v) is not str for k, v in data.items()
    ):
        raise ValueError("private capabilities denied")
    return {k: v.encode() for k, v in data.items()}


def confinement(parent, *, expected_net=None):
    status = dict(
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text().splitlines()
        if ":" in line
    )
    net = os.readlink("/proc/self/ns/net")
    checks = {
        kind + "_separated": os.readlink("/proc/self/ns/" + kind) != parent[kind]
        for kind in ("user", "pid", "mnt")
    }
    checks.update(
        capabilities_dropped=int(status["CapEff"], 16) == 0,
        network_confined=net != parent["host_net"]
        and (net == expected_net if expected_net else net != parent["net"]),
        environment_cleared=not any("proxy" in k.lower() for k in os.environ),
    )
    if not all(checks.values()):
        raise JournalDenied("process confinement unavailable")
    return checks


def normalize_unmodified(raw, secret):
    """Trusted adapter after original source digest verification, never source auth."""
    data = decode(raw)
    if type(data) is not dict or "credential_digest" in data:
        raise ValueError("ordinary source JSON required")
    if set(data) not in ({"schemas"}, {"resource", "rows"}, {"resource", "columns", "values"}):
        raise ValueError("fixed ordinary JSON encoding required")
    return _json(dict(data, credential_digest=hashlib.sha256(secret.encode()).hexdigest())).encode()


class PersistedRuntimeCustody(RuntimeCustody):
    """Admission archive is mandatory before output and before new credential use."""

    def __init__(self, runtime, archive):
        super().__init__(runtime)
        self.archive = archive
        self.request_reference = None
        self.request_sha256 = None

    def _before_begin(self, owner, value):
        self.request_reference = digest((owner.config["caller"], value.get("request_id")))
        self.request_sha256 = digest(value)
        self.archive(
            "availability",
            {"binding": owner.binding, "arguments": {"request_reference": self.request_reference}},
        )

    def _before_redeem(self, owner, value):
        self.archive(
            "availability",
            {"binding": owner.binding, "arguments": {"request_reference": self.request_reference}},
        )

    def _accepted(self, owner, result):
        if type(result) is dict and result.get("status") == "admitted":
            checkpoint = self.archive(
                "append",
                {
                    "binding": owner.binding,
                    "arguments": {
                        "observations": result["observations"],
                        "journal_head": result["head"],
                        "request_reference": self.request_reference,
                        "request_sha256": self.request_sha256,
                    },
                },
            )
            result = dict(result, checkpoint=checkpoint)
        # Discovery becomes admitted this start ONLY after protected persistence.
        return self.runtime.accept(owner, result)


def serve(value):
    checks = confinement(value["parent"], expected_net=value.get("expected_net"))
    role = value["role"]
    configs = decode(private_bytes("/private/configs"))
    keys = capabilities("/private/capabilities")
    if role == "witness":
        owner = ProgressWitness(
            "/state", private_bytes("/private/signing-key"), value["witness_contract"]
        )
        dispatch = owner.dispatch
    elif role == "audit":
        def witness_client(action, data):
            return rpc(
                "/witness/service", "audit", keys["witness"], action, data
            )

        owners = {
            digest(c): AuditCustody(
                Path("/state") / c["operation"],
                private_bytes("/private/signing-key"),
                c,
                witness=witness_client,
                witness_stream=stream_for(configs, "audit", digest(c)),
            )
            for c in configs
        }

        def dispatch(caller, action, data):
            if type(data) is not dict or data.get("binding") not in owners:
                raise JournalDenied("audit binding denied")
            return owners[data["binding"]].dispatch(caller, action, data)
    elif role == "evidence":
        def witness_client(action, data):
            return rpc(
                "/witness/service", "evidence", keys["witness"], action, data
            )

        owner = EvidenceCustody(
            "/state", private_bytes("/private/signing-key"), configs, policy=value["policy"],
            semantic_limit=100 if value.get("semantic", {}).get("version") == 2 else 1,
            witness=witness_client,
            witness_stream=stream_for(configs, "evidence"),
        )
        if "semantic" in value:
            semantic_owner = RuntimeSemanticCustody(owner, value["semantic"])

            def dispatch(role, action, arguments):
                if action == "semantic":
                    return semantic_owner.dispatch(role, action, arguments)
                return owner.dispatch(role, action, arguments)
        else:
            dispatch = owner.dispatch
    elif role == "authorization":
        os.environ["BROKER_AUTH_KEY"] = private_bytes("/private/issuer").decode()
        os.environ["BROKER_SOURCE_SECRET"] = private_bytes("/private/worker-secret").decode()
        audit_key = private_bytes("/private/audit-capability")
        evidence_key = private_bytes("/private/evidence-capability")

        def audit_client(action, data):
            return rpc("/audit/service", "supervisor", audit_key, action, data)

        def archive_client(action, data):
            return rpc("/evidence/service", "supervisor", evidence_key, action, data)

        owners = [
            AuthorizationCustody(
                c,
                RemoteJournal(audit_client, digest(c)),
                received_transform=normalize_unmodified,
                installed_worker=True,
            )
            for c in configs
        ]
        dispatch = PersistedRuntimeCustody(
            SupervisedReadOnlyRuntime(*owners), archive_client
        ).dispatch
    elif role == "gateway":
        key = private_bytes("/private/authorization-capability")

        def source_client(action, data):
            return rpc("/authorization/service", "source", key, action, data)

        owner = CredentialGateway(
            configs,
            source_client,
            private_bytes("/private/credential").decode(),
            "/private/certificate",
            value["certificate_sha256"],
            value["host"],
        )
        dispatch = owner.dispatch
    elif role == "acquisition":
        key = private_bytes("/private/authorization-capability")
        gateway_key = private_bytes("/private/gateway-capability")
        bindings = {c["operation"]: digest(c) for c in configs}

        def dispatch(caller, action, message):
            if caller != "reasoner" or action != "read" or type(message) is not dict:
                raise JournalDenied("acquisition caller denied")
            reply = rpc("/authorization/service", "broker", key, "begin", message)
            while reply.get("status") == "offered":
                receipt = reply["receipt"]
                received = rpc(
                    "/gateway/service",
                    "acquisition",
                    gateway_key,
                    "acquire",
                    {"receipt": receipt, "binding": bindings[message["operation"]]},
                )
                reply = rpc(
                    "/authorization/service",
                    "broker",
                    key,
                    "complete",
                    {"receipt": receipt, "body": received["body"]},
                )
            return reply
    else:
        raise JournalDenied("fixed service role required")
    endpoint = Endpoint("/endpoint/service", keys, dispatch)
    try:
        print(_json({"ready": True, "checks": checks}), flush=True)
        if frame() != {"command": "shutdown"}:
            raise JournalDenied("private service control denied")
    finally:
        endpoint.close()


def reason(value):
    checks = confinement(value["parent"])
    # Concrete negative mount probes; these control keys/storage must not exist.
    for name in ("issuer", "signing-key", "credential", "worker-secret"):
        checks[name + "_inaccessible"] = not Path("/private/" + name).exists()
    checks["custody_storage_inaccessible"] = not Path("/state").exists()
    if not all(checks.values()):
        raise JournalDenied("reasoning custody isolation failed")
    key = private_bytes("/private/reasoning-capability")
    response = rpc("/acquisition/service", "reasoner", key, "read", value["message"])
    print(_json({"response": response, "reasoner_checks": checks}), flush=True)


def main():
    if sys.argv[1:] == ["--net-child"]:
        net_child()
        return 0
    if sys.argv[1:] != ["--child"]:
        return 2
    try:
        value = frame()
        if value["role"] == "reasoner":
            reason(value)
        else:
            serve(value)
        return 0
    except Exception:  # noqa: BLE001 - never emit key/config/endpoint exception details
        print(_json({"ready": False, "status": "BLOCKED", "execution_allowed": False}), flush=True)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
