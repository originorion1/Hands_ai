"""Synthetic acceptance deployment of the single runtime, not a live connector.

Reuses PR160's fabric, kernel policy, TLS source and zero-network reasoning jail.
Authorization and audit run in separate zero-network, capability-dropped jails.
No process joins host networking; no control service performs HTTPS acquisition.
"""

import hashlib
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
import traceback
from contextlib import ExitStack
from pathlib import Path

if __name__ == "__main__":
    if sys.argv[1:] == ["--child"]:
        sys.path[:0] = ["/app/src", "/work"]
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from destination_network_lab import (
    LAB_PATH,
    PORT,
    V4_APPROVED,
    V4_UNAPPROVED,
    V6_APPROVED,
    V6_UNAPPROVED,
    Fabric,
    KernelUnavailable,
    counter_for,
    jail_command,
    net_child,
    prerequisites,
    targets,
)
from https_broker_support import FixtureServer, LocalPolicy, certificates, https_read
from isolated_broker_lab import Frames
from isolation_lab import connectable, namespace_command, readable, result
from kernel_destination_https_lab import CanaryServer, frame
from protected_custody_support import (
    AuditCustody,
    AuthorizationCustody,
    Endpoint,
    RemoteJournal,
    RuntimeCustody,
    capability_keys,
    rpc,
)

from orion.pilot.broker_contract import authenticate, digest, private_bytes
from orion.pilot.journal import JournalDenied
from orion.pilot.runtime import SupervisedReadOnlyRuntime
from orion.understanding.role_checkpoint import _json

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = Path(__file__).resolve()


def confinement(parent):
    status = dict(
        line.split(":", 1)
        for line in Path("/proc/self/status").read_text().splitlines()
        if ":" in line
    )
    return {
        **{
            kind + "_separated": os.readlink("/proc/self/ns/" + kind) != parent[kind]
            for kind in ("net", "user", "pid", "mnt")
        },
        "capabilities_dropped": int(status["CapEff"], 16) == 0,
        "environment_cleared": not any("proxy" in k.lower() for k in os.environ),
    }


def service_child(value):
    keys = capability_keys("/private/capabilities.json")
    configs = decode_configs("/private/configs.json")
    if value["role"] == "audit":
        owners = {
            digest(c): AuditCustody(
                Path("/state") / c["operation"], private_bytes("/private/audit-key"), c
            )
            for c in configs
        }

        def dispatch(role, action, data):
            if role != "supervisor" or type(data) is not dict or data.get("binding") not in owners:
                raise JournalDenied("fixed audit scope required")
            return owners[data["binding"]].dispatch(role, action, data)
    else:
        os.environ["BROKER_AUTH_KEY"] = private_bytes("/private/issuer-key").decode()
        os.environ["BROKER_SOURCE_SECRET"] = private_bytes("/private/source-secret").decode()
        audit_key = private_bytes("/private/audit-capability")

        def audit_client(action, arguments):
            return rpc("/audit/service", "supervisor", audit_key, action, arguments)

        owners = [AuthorizationCustody(c, RemoteJournal(audit_client, digest(c))) for c in configs]
        runtime = SupervisedReadOnlyRuntime(owners[0], owners[1])
        dispatch = RuntimeCustody(runtime).dispatch
    checks = confinement(value["parent"])
    if not all(checks.values()):
        raise ValueError("control custody confinement unavailable")
    endpoint = Endpoint("/endpoint/service", keys, dispatch)
    try:
        print(_json({"ready": True, "checks": checks}), flush=True)
        while True:
            command = frame()
            if command == {"fixture": "stop"}:
                return 0
            raise ValueError("private service command denied")
    finally:
        endpoint.close()


def decode_configs(path):
    from orion.pilot.broker_contract import decode

    return decode(private_bytes(path))


def source_child(value):
    source_key = private_bytes("/private/source-capability")
    io = {"metadata": 0, "read": 0}
    bindings = value["bindings"]

    def authorize(receipt):
        if type(receipt) is not str or len(receipt) != 128 or receipt[:64] not in bindings:
            raise ValueError("source receipt denied")
        approval = rpc(
            "/authorization/service",
            "source",
            source_key,
            "redeem",
            {"receipt": receipt, "binding": receipt[:64]},
        )
        if approval != {"authorized": True, "binding": receipt[:64]}:
            raise ValueError("source custody denied")
        kind = bindings[receipt[:64]]
        # The fixture is opened only AFTER independent authorization/audit checks.
        raw = private_bytes("/fixture/" + kind + ".json")
        io[kind] += 1
        return raw

    with ExitStack() as stack:
        families = [(V4_APPROVED, V4_UNAPPROVED)]
        if value["ipv6"]:
            families.append((V6_APPROVED, V6_UNAPPROVED))
        servers, canaries = [], []
        for approved, unapproved in families:
            server = FixtureServer(
                Path("/fixture/cert.pem"),
                Path("/fixture/key.pem"),
                None,
                "",
                host=approved,
                port=PORT,
                source_authorizer=authorize,
            )
            servers.append(server)
            stack.callback(server.close)
            for host, port in ((unapproved, PORT), (approved, PORT + 1), (unapproved, PORT + 2)):
                canary = CanaryServer(host, port)
                canaries.append(canary)
                stack.callback(canary.close)
        print(_json({"ready": True, "net": os.readlink("/proc/self/ns/net")}), flush=True)
        while True:
            command = frame()
            if command == {"fixture": "stop"}:
                return 0
            if command != {"fixture": "stats"}:
                raise ValueError("private source command denied")
            print(
                _json(
                    {
                        "source_io": dict(io),
                        "requests": sum(len(s.requests) for s in servers),
                        "canaries": sum(c.accepted for c in canaries),
                    }
                ),
                flush=True,
            )


def mutation_checks(paths):
    checks = {
        name + "_inaccessible": not readable(path)
        for name, path in paths.items()
        if name not in ("history", "anchor")
    }
    for name in ("history", "anchor"):
        path = paths[name]
        for operation in ("rewrite", "truncate", "unlink", "rollback"):
            try:
                if operation in ("rewrite", "truncate"):
                    fd = os.open(path, os.O_WRONLY | (os.O_TRUNC if operation == "truncate" else 0))
                    os.close(fd)
                elif operation == "unlink":
                    os.unlink(path)
                else:
                    os.replace("/tmp/rollback-canary", path)
                checks[name + "_" + operation + "_denied"] = False
            except OSError:
                checks[name + "_" + operation + "_denied"] = True
    return checks


def broker_child(value):
    key = value["capability"].encode()
    policy = LocalPolicy(PORT, "/fixture/cert.pem", value["certificate_sha256"], value["host"])
    # A real local rollback payload; denial must not be missing-input ENOENT.
    Path("/tmp/rollback-canary").write_bytes(b"old-history")
    checks = mutation_checks(value["paths"])
    checks.update(confinement(value["parent"]))
    checks["configured_private_network"] = os.readlink("/proc/self/ns/net") == value["broker_net"]
    mutation = subprocess.run(
        [shutil.which("nft", path=LAB_PATH), "delete", "table", "inet", "orion"],
        capture_output=True,
        timeout=2,
        check=False,
    )
    checks["firewall_mutation_denied"] = mutation.returncode != 0
    if not all(checks.values()):
        raise ValueError("broker custody confinement unavailable")
    print(_json({"ready": True, "checks": checks}), flush=True)
    last_receipt = None
    while True:
        command = frame()
        try:
            if command == {"fixture": "stop"}:
                return 0
            if command["fixture"] == "direct":
                target = next(
                    t for t in targets(value["ipv6"], value["host"]) if t[0] == command["name"]
                )
                answer = {"connected": connectable(target[1], (target[2], target[3]))}
            elif command["fixture"] == "audit_attack":
                try:
                    rpc("/audit/service", command["role"], key, command["action"], command["value"])
                    answer = {"denied": False}
                except (OSError, ValueError):
                    answer = {"denied": True}
            elif command["fixture"] == "rpc_attack":
                try:
                    rpc(
                        "/authorization/service",
                        command["role"],
                        key,
                        command["action"],
                        command["value"],
                    )
                    answer = {"denied": False}
                except (OSError, ValueError):
                    answer = {"denied": True}
            elif command["fixture"] == "raw":
                # Deliberately bypass all broker URL/application authorization.
                https_read(policy, command.get("receipt") or last_receipt or "0" * 128, 32768)
                answer = {"denied": False}
            elif command["fixture"] in ("request", "reserve"):
                answer = rpc("/authorization/service", "broker", key, "begin", command["message"])
                while answer.get("status") == "offered":
                    last_receipt = answer["receipt"]
                    if command["fixture"] == "reserve":
                        break
                    raw = https_read(policy, last_receipt, 32768)
                    answer = rpc(
                        "/authorization/service",
                        "broker",
                        key,
                        "complete",
                        {"receipt": last_receipt, "body": raw.hex()},
                    )
                if answer.get("status") == "admitted" and command["message"]["operation"] == "read":
                    answer["source_requests"] = 1
            else:
                raise ValueError("private attack selector denied")
        except Exception:  # noqa: BLE001 - transport/custody loss is always denial
            answer = {"status": "denied", "denied": True}
        print(_json(answer), flush=True)


def common_mounts():
    # Existing mechanics have a single semantic owner; do not copy implementations.
    return [
        (ROOT / "src", "/app/src"),
        *[
            (ROOT / "tests" / name, "/work/" + name)
            for name in (
                "pilot_runtime_deployment.py",
                "protected_custody_support.py",
                "isolation_lab.py",
                "destination_network_lab.py",
                "https_broker_support.py",
                "isolated_broker_lab.py",
                "kernel_destination_https_lab.py",
                "broker_namespace_lab.py",
            )
        ],
    ]


def command(readonly, *, writable=(), network=False):
    mounts = common_mounts() + list(readonly)
    argv = jail_command(SCRIPT, mounts) if network else namespace_command(SCRIPT, readonly=mounts)
    if not network:
        for option in ("--uid", "--gid"):
            argv[argv.index(option) + 1] = "0"
    index = argv.index("--chdir")
    for source, target in writable:
        argv[index:index] = ["--bind", str(source), target]
    return argv


def write_private(path, value):
    path.write_text(value)
    path.chmod(0o600)


def parent_scope():
    return {kind: os.readlink("/proc/self/ns/" + kind) for kind in ("net", "user", "pid", "mnt")}


def exchange(process, value):
    process.stdin.write((_json(value) + "\n").encode())
    process.stdin.flush()
    return Frames(process.stdout).read()


def terminate(process):
    if process.poll() is None:
        process.kill()
    process.wait(timeout=5)


class Deployment:
    def __init__(self, root, protocol, host):
        from test_broker_metadata import MetadataHarness
        from test_supervised_broker import Harness

        self.root, self.host = root, host
        self.h = Harness(root / "records", protocol)
        self.m = MetadataHarness(
            root / "metadata",
            schemas={
                "r_01": [
                    {"name": name, "kind": kind, "classification": "public"}
                    for name, kind in (
                        ("f_a", "reference"),
                        ("f_b", "reference"),
                        ("f_c", "date"),
                        ("f_d", "number"),
                    )
                ]
            },
        )
        self.m.key, self.m.secret = self.h.key, self.h.secret
        self.m.write_source()
        self.h.config["limits"].update(max_requests=1, total_response_bytes=65536)
        self.m.config["limits"].update(max_requests=6, total_response_bytes=393216)
        self.keys = {
            name: secrets.token_hex(32).encode()
            for name in ("broker", "source", "owner", "supervisor", "audit")
        }
        for name in ("audit-state", "audit-endpoint", "auth-endpoint"):
            (root / name).mkdir(mode=0o700)
        for owner in (self.m, self.h):
            (root / "audit-state" / owner.config["operation"]).mkdir(mode=0o700)
        self.cert, self.tls_key = certificates(root, hostname=host)
        write_private(root / "configs.json", _json([self.m.config, self.h.config]))
        write_private(root / "issuer-key", self.h.key.decode())
        write_private(root / "source-secret", self.h.secret)
        for name in ("audit", "supervisor", "source"):
            write_private(root / (name + "-key"), self.keys[name].decode())
        write_private(
            root / "audit-capabilities.json",
            _json({"supervisor": self.keys["supervisor"].decode()}),
        )
        write_private(
            root / "auth-capabilities.json",
            _json({name: self.keys[name].decode() for name in ("broker", "source", "owner")}),
        )
        self.processes = []
        self.checks = {}

    def service(self, role, *, expect_denial=False):
        endpoint = self.root / ("audit-endpoint" if role == "audit" else "auth-endpoint")
        socket_path = endpoint / "service"
        if socket_path.exists():
            socket_path.unlink()  # Exact disposable service socket, never history.
        mounts = [
            (self.root / "configs.json", "/private/configs.json"),
            (self.root / (role + "-capabilities.json"), "/private/capabilities.json"),
        ]
        writable = [(endpoint, "/endpoint")]
        if role == "audit":
            mounts += [(self.root / "audit-key", "/private/audit-key")]
            writable += [(self.root / "audit-state", "/state")]
        else:
            mounts += [
                (self.root / "issuer-key", "/private/issuer-key"),
                (self.root / "source-secret", "/private/source-secret"),
                (self.root / "supervisor-key", "/private/audit-capability"),
                (self.root / "audit-endpoint", "/audit"),
            ]
        process = subprocess.Popen(
            command(mounts, writable=writable),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env={"PATH": LAB_PATH},
            close_fds=True,
        )
        self.processes.append(process)
        ready = exchange(process, {"role": role, "parent": parent_scope()})
        if expect_denial:
            assert ready.get("ready") is False and ready.get("status") == "BLOCKED"
            self.checks["restart_denial"] = {"pending_custody_refuses_authority": True}
            return process
        if ready.get("ready") is not True or not all(ready["checks"].values()):
            if ready.get("status") == "FAIL":
                raise AssertionError("custody service acceptance failed: " + _json(ready))
            raise KernelUnavailable("custody process launch unavailable")
        self.checks[role] = ready["checks"]
        return process

    def owner(self, action, value=None):
        return rpc(
            self.root / "auth-endpoint" / "service", "owner", self.keys["owner"], action, value
        )

    def control(self, operation, control):
        status = self.owner("status", operation)
        payload = {"control": control, "head": status["head"], "nonce": status["nonce"]}
        return self.owner(
            "control",
            {
                "operation": operation,
                "message": dict(payload, mac=authenticate(self.h.key, "control", payload)),
            },
        )

    def close(self):
        for process in reversed(self.processes):
            terminate(process)
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()


def network_exchange(process, value):
    process.stdin.write(_json(value) + "\n")
    process.stdin.flush()
    return Frames(process.stdout).read()


def launch_network(d, fabric, source, broker):
    bindings = {digest(c): c["operation"] for c in (d.m.config, d.h.config)}
    Fabric.boot(
        source,
        command(
            [
                (d.cert, "/fixture/cert.pem"),
                (d.tls_key, "/fixture/key.pem"),
                (d.m.source, "/fixture/metadata.json"),
                (d.h.source, "/fixture/read.json"),
                (d.root / "source-key", "/private/source-capability"),
                (d.root / "auth-endpoint", "/authorization"),
            ],
            network=True,
        ),
        {"role": "source", "ipv6": fabric.ipv6, "bindings": bindings},
    )
    if Frames(source.stdout).read() != {"ready": True, "net": source.private_net}:
        raise KernelUnavailable("source launch unavailable")
    mounts = [
        (d.cert, "/fixture/cert.pem"),
        (d.root / "auth-endpoint", "/authorization"),
        (d.root / "audit-endpoint", "/audit"),
    ]
    # Read-only live history/anchor mounts give concrete EROFS mutation controls;
    # signing keys are absent, not just owner-permission protected.
    for name, filename in (("history", "broker.db"), ("anchor", "accepted-head")):
        mounts.append((d.root / "audit-state" / "read" / filename, "/protected/" + name))
    paths = {
        "history": "/protected/history",
        "anchor": "/protected/anchor",
        "issuer_key": "/private/issuer-key",
        "audit_key": "/private/audit-key",
        "source_secret": "/private/source-secret",
        "source_data": "/fixture/read.json",
        "tls_key": "/fixture/key.pem",
    }
    Fabric.boot(
        broker,
        command(mounts, network=True),
        {
            "role": "broker",
            "capability": d.keys["broker"].decode(),
            "host": d.host,
            "ipv6": fabric.ipv6,
            "broker_net": broker.private_net,
            "parent": parent_scope(),
            "paths": paths,
            "certificate_sha256": hashlib.sha256(d.cert.read_bytes()).hexdigest(),
        },
    )
    ready = Frames(broker.stdout).read()
    if ready.get("ready") is not True or not all(ready["checks"].values()):
        if ready.get("status") == "FAIL":
            raise AssertionError("broker acceptance failed: " + _json(ready))
        raise KernelUnavailable("broker launch unavailable")
    d.checks["broker"] = ready["checks"]


def direct_denials(fabric, broker, host):
    proofs = []
    for name, _, _, _ in targets(fabric.ipv6, host):
        before = fabric.counters(broker)
        result = network_exchange(broker, {"fixture": "direct", "name": name})
        after = fabric.counters(broker)
        counter = counter_for(name)
        assert result == {"connected": False} and after[counter] > before[counter]
        proofs.append(
            {"target": name, "counter": counter, "rejects": after[counter] - before[counter]}
        )
    return proofs


def reasoner_read(d, broker):
    # Existing confined reasoning consumer: no new reasoning implementation.
    from confined_https_broker_lab import relay_reasoner

    d.h.process = broker
    d.h.send = lambda msg: network_exchange(broker, {"fixture": "request", "message": msg})
    paths = {
        "config": str(d.root / "issuer-key"),
        "source": str(d.h.source),
        "route": str(d.root / "audit-key"),
        "tls_key": str(d.tls_key),
    }
    scope = {
        "secret": str(d.root / "source-secret"),
        "audit": str(d.root / "audit-state" / "read" / "broker.db"),
        "unix": str(d.root / "auth-endpoint" / "service"),
        "port": PORT,
        "parent": broker.pid,
        **parent_scope(),
    }
    response, checks, reasoner = relay_reasoner(d.h, paths, scope, approved_host=d.host)
    from orion.pilot.broker_contract import observations_from

    d.checks["reasoner"] = checks
    return response, observations_from(response["observations"]), reasoner


def run_case(root, protocol, ending, initial_user):
    fabric = Fabric(SCRIPT)
    fabric.initial_user = initial_user
    d = None
    try:
        fabric.initialize()
        host = V6_APPROVED if protocol == "local_columns_v1" and fabric.ipv6 else V4_APPROVED
        d = Deployment(root, protocol, host)
        audit = d.service("audit")
        authority = d.service("auth")
        source, broker = fabric.spawn("source"), fabric.spawn("broker")
        fabric.connect(source, broker, host)
        launch_network(d, fabric, source, broker)
        assert source.private_net != broker.private_net
        reachable = {
            name: connectable(af, (address, port))
            for name, af, address, port in targets(fabric.ipv6, host)
        }
        reachable["approved"] = connectable(
            socket.AF_INET6 if ":" in host else socket.AF_INET, (host, PORT)
        )
        assert all(reachable.values())
        kernel = direct_denials(fabric, broker, host)
        assert d.owner("health")["record_authority_armed"] is False
        # Metadata-first policy is at custody, not in the hostile broker.
        assert (
            network_exchange(broker, {"fixture": "request", "message": d.h.message()})["status"]
            == "denied"
        )
        assert d.owner("status", "read")["budget"]["attempts"] == 0
        d.control("metadata", "arm")
        discovery = network_exchange(broker, {"fixture": "request", "message": d.m.message()})
        assert discovery["status"] == "admitted" and discovery["budget"]["attempts"] == 2
        assert discovery["interpretation"] == "UNKNOWN"
        d.control("read", "arm")
        for attack in ("token", "tenant_id", "company", "source_id", "resource", "fields"):
            message = d.h.message("bad-" + attack)
            if attack == "token":
                message["grant_token"] = authenticate(
                    d.keys["broker"], "read_grant", digest(d.h.config)
                )
            elif attack == "fields":
                message["request"]["fields"].append("unapproved")
            else:
                message["request"][attack] = (
                    "https://other.test" if attack == "source_id" else "other"
                )
            assert (
                network_exchange(broker, {"fixture": "request", "message": message})["status"]
                == "denied"
            )
        # Capability holders cannot impersonate owner/source/audit or mint controls.
        for role, action in (
            ("broker", "control"),
            ("owner", "control"),
            ("source", "redeem"),
            ("broker", "sign"),
            ("broker", "widen"),
            ("broker", "re-sign"),
        ):
            assert (
                network_exchange(
                    broker, {"fixture": "rpc_attack", "role": role, "action": action, "value": {}}
                )["denied"]
                is True
            )
        for role, action in (
            ("broker", "stop"),
            ("supervisor", "begin"),
            ("supervisor", "rewrite"),
        ):
            assert (
                network_exchange(
                    broker,
                    {
                        "fixture": "audit_attack",
                        "role": role,
                        "action": action,
                        "value": {"binding": digest(d.h.config), "arguments": {}},
                    },
                )["denied"]
                is True
            )
        assert network_exchange(source, {"fixture": "stats"})["source_io"] == {
            "metadata": 2,
            "read": 0,
        }
        assert d.owner("status", "read")["budget"]["attempts"] == 0
        if ending in ("audit-loss", "auth-loss", "pending-stop"):
            ticket = network_exchange(broker, {"fixture": "reserve", "message": d.h.message()})
            assert ticket["status"] == "offered"
            if ending == "pending-stop":
                d.control("read", "stop")
            else:
                terminate(audit if ending == "audit-loss" else authority)
            assert (
                network_exchange(broker, {"fixture": "raw", "receipt": ticket["receipt"]})["denied"]
                is True
            )
            assert network_exchange(source, {"fixture": "stats"})["source_io"]["read"] == 0
            if ending == "audit-loss":
                d.service("audit")
            if ending in ("audit-loss", "auth-loss"):
                terminate(authority)
                d.service("auth", expect_denial=True)
            retained = rpc(
                d.root / "audit-endpoint" / "service",
                "supervisor",
                d.keys["supervisor"],
                "inspect",
                {"binding": digest(d.h.config), "arguments": {}},
            )
            assert retained["attempts"] == 1 and retained["reserved_bytes"] == 65536
            return {
                "status": "PASS",
                "ending": ending,
                "protocol": protocol,
                "checks": d.checks,
                "reachable": reachable,
                "kernel_denials": kernel,
                "read_source_io": 0,
                "budget_after_restart": retained,
                "ipv6_enabled": fabric.ipv6,
                "source_outside_broker": True,
                "live_ready": False,
                "execution_allowed": False,
            }
        response, observations, reasoner = reasoner_read(d, broker)
        assert response["status"] == "admitted" and len(observations) == 1
        assert (
            observations[0].evidence.payload["provenance"]["authorization_id"]
            == d.h.grant.authorization_id
        )
        assert reasoner["canonical_immutable"] is True
        assert network_exchange(source, {"fixture": "stats"})["source_io"] == {
            "metadata": 2,
            "read": 1,
        }
        # Raw replay is refused by the source, even with all broker checks bypassed.
        assert network_exchange(broker, {"fixture": "raw"})["denied"] is True
        assert (
            network_exchange(broker, {"fixture": "request", "message": d.h.message()})["status"]
            == "denied"
        )
        before = d.owner("status", "read")["budget"]
        # Restart BOTH independent custodians; owner protected tips retain limits.
        terminate(authority)
        terminate(audit)
        audit = d.service("audit")
        authority = d.service("auth")
        after = d.owner("status", "read")["budget"]
        assert (
            (before["attempts"], before["reserved_bytes"])
            == (after["attempts"], after["reserved_bytes"])
            == (1, 65536)
        )
        assert d.owner("health")["metadata_admitted_this_start"] is False
        assert d.owner("health")["record_authority_armed"] is False
        assert (
            network_exchange(broker, {"fixture": "request", "message": d.h.message("restart")})[
                "status"
            ]
            == "denied"
        )
        d.control("metadata", "arm")
        assert (
            network_exchange(broker, {"fixture": "request", "message": d.m.message("restart-m")})[
                "status"
            ]
            == "admitted"
        )
        d.control("read", "arm")
        assert (
            network_exchange(broker, {"fixture": "request", "message": d.h.message("budget")})[
                "status"
            ]
            == "denied"
        )
        d.control("read", ending)
        d.control("metadata", ending)
        terminate(authority)
        terminate(audit)
        d.service("audit")
        d.service("auth")
        for operation in ("metadata", "read"):
            try:
                d.control(operation, "arm")
                raise AssertionError("terminal authority restored")
            except JournalDenied:
                pass
        assert network_exchange(broker, {"fixture": "raw"})["denied"] is True
        assert network_exchange(source, {"fixture": "stats"})["source_io"] == {
            "metadata": 4,
            "read": 1,
        }
        assert not d.owner("health")["custody_available"]
        if ending == "stop":
            stats = network_exchange(source, {"fixture": "stats"})
            # Trusted operator removes the only accepted route in the BROKER'S
            # private netns, then terminates acquisition. No host rules touched.
            fabric.in_net(broker, ["nft", "flush", "chain", "inet", "orion", "egress"])
            assert network_exchange(broker, {"fixture": "raw"})["denied"] is True
            assert network_exchange(source, {"fixture": "stats"}) == stats
            terminate(broker)
            d.checks["emergency_stop"] = {"kernel_egress_removed": True, "broker_terminated": True}
        assert all(d.checks["broker"].values())
        # Journal contains only references/counters, never any private key or rows.
        history = (root / "audit-state" / "read" / "broker.db").read_bytes()
        assert all(key not in history for key in [d.h.key, *d.keys.values(), d.h.secret.encode()])
        return {
            "status": "PASS",
            "ending": ending,
            "protocol": protocol,
            "checks": d.checks,
            "reachable": reachable,
            "kernel_denials": kernel,
            "read_source_io": 1,
            "ipv6_enabled": fabric.ipv6,
            "source_outside_broker": True,
            "metadata_source_io": 4,
            "budget_after_restart": after,
            "reasoner": reasoner,
            "provenance_intact": True,
            "interpretation": response["interpretation"],
            "live_ready": False,
            "execution_allowed": False,
        }
    finally:
        fabric.close()
        if d is not None:
            d.close()


def setup(value):
    cases = []
    with tempfile.TemporaryDirectory(prefix="orion-runtime-deployment-") as temporary:
        for index, (protocol, ending) in enumerate(
            (
                ("local_rows_v1", "stop"),
                ("local_columns_v1", "revoke"),
                ("local_rows_v1", "audit-loss"),
                ("local_rows_v1", "auth-loss"),
                ("local_rows_v1", "pending-stop"),
            )
        ):
            # Each acceptance deployment has its own disposable fabric namespace.
            completed = subprocess.run(
                ["unshare", "--net", sys.executable, "-I", str(SCRIPT), "--case"],
                input=_json(
                    {
                        "root": str(Path(temporary) / str(index)),
                        "protocol": protocol,
                        "ending": ending,
                        "initial_user": value["initial_user"],
                    }
                ).encode(),
                capture_output=True,
                timeout=25,
                check=False,
                env={"PATH": LAB_PATH},
            )
            if completed.returncode or completed.stderr:
                raise KernelUnavailable("runtime process launch failed")
            cases.append(json.loads(completed.stdout))
    passed = all(case.get("status") == "PASS" for case in cases)
    return dict(
        result(
            "PASS" if passed else "FAIL",
            "synthetic_runtime_deployment_only",
            negative_controls=True,
        ),
        cases=cases,
    )


def run():
    if not prerequisites() or os.getuid() == 0:
        return result("BLOCKED", "private_kernel_prerequisites_unavailable")
    try:
        completed = subprocess.run(
            [
                "unshare",
                "--user",
                "--map-root-user",
                "--net",
                sys.executable,
                "-I",
                str(SCRIPT),
                "--setup",
            ],
            input=_json({"initial_user": os.readlink("/proc/self/ns/user")}).encode(),
            capture_output=True,
            timeout=140,
            check=False,
            env={"PATH": LAB_PATH},
        )
        if completed.returncode or completed.stderr:
            raise KernelUnavailable("runtime containment launch failed")
        return json.loads(completed.stdout)
    except (OSError, subprocess.TimeoutExpired, KernelUnavailable):
        return result("BLOCKED", "runtime_kernel_or_process_capability_unavailable")


if __name__ == "__main__":
    if sys.argv[1:] == ["--net-child"]:
        net_child()
    elif sys.argv[1:] == ["--child"]:
        sys.path[:0] = ["/app/src", "/work"]
        value = frame()
        try:
            if value["role"] in ("audit", "auth"):
                raise SystemExit(service_child(value))
            raise SystemExit(
                source_child(value) if value["role"] == "source" else broker_child(value)
            )
        except JournalDenied:
            print('{"ready":false,"status":"BLOCKED"}', flush=True)
            raise SystemExit(2) from None
        except Exception as error:  # noqa: BLE001 - no exception text/locals/keys
            locations = [
                f"{entry.name}:{entry.lineno}"
                for entry in traceback.extract_tb(error.__traceback__)
            ]
            print(_json({"ready": False, "status": "FAIL", "locations": locations}), flush=True)
            raise SystemExit(2) from None
    elif sys.argv[1:] == ["--case"]:
        value = json.loads(sys.stdin.buffer.read(65536))
        try:
            report = run_case(
                Path(value["root"]), value["protocol"], value["ending"], value["initial_user"]
            )
        except KernelUnavailable as error:
            report = result("BLOCKED", str(error))
        except Exception as error:  # noqa: BLE001 - no exception text/locals/keys
            report = dict(
                result("FAIL", "runtime_acceptance_failed"),
                locations=[
                    f"{entry.name}:{entry.lineno}"
                    for entry in traceback.extract_tb(error.__traceback__)
                ],
            )
        print(_json(report))
    elif sys.argv[1:] == ["--setup"]:
        try:
            print(_json(setup(json.loads(sys.stdin.buffer.read(65536)))))
        except (OSError, KernelUnavailable, subprocess.TimeoutExpired):
            print(_json(result("BLOCKED", "runtime_kernel_or_process_capability_unavailable")))
    elif not sys.argv[1:]:
        print(_json(run()))
    else:
        raise SystemExit(2)
