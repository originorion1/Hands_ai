"""Fixed broker/source private-net HTTPS laboratory; synthetic inputs only."""

import hashlib
import json
import os
import socket
import subprocess
import sys
import tempfile
from contextlib import ExitStack
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from broker_namespace_lab import readonly
from https_broker_support import HOST, FixtureServer, HTTPSBroker, certificates
from isolation_lab import (
    CHECKS,
    connectable,
    inspect_child,
    namespace_command,
    readable,
    result,
    run_lab,
)

from orion.pilot.broker_contract import (
    MAX_FRAME,
    authenticate,
    decode,
    digest,
    exact,
    observations_from,
    private_bytes,
)
from orion.understanding.role_checkpoint import _json

APPROVED_PORT = 44443
BROKER_EXTRA_CHECKS = (
    "source_write_denied",
    "config_write_denied",
    "route_write_denied",
    "certificate_write_denied",
    "tls_key_write_denied",
    "approved_destination_bound",
    "unapproved_host_denied",
    "unapproved_port_denied",
    "test_network_denied",
    "loopback_only",
    "proxy_environment_cleared",
)


def broker_checks_valid(value):
    return (
        type(value) is dict
        and set(value) == set(CHECKS + BROKER_EXTRA_CHECKS)
        and all(item is True for item in value.values())
    )


def _decorate(broker, server, status):
    value = broker.status(status)
    value["source_requests"] = len(server.requests)
    return value


def broker_child():
    raw = sys.stdin.buffer.readline(MAX_FRAME + 1)
    if not raw.endswith(b"\n") or len(raw) > MAX_FRAME:
        raise ValueError("bootstrap denied")
    value = exact(decode(raw), ("head", "key", "secret", "scope", "behavior"))
    if value["behavior"] not in ("ok", "redirect"):
        raise ValueError("fixed fixture behavior required")
    paths = {
        "config": Path("/fixture/config.json"),
        "source": Path("/fixture/source.json"),
        "route": Path("/fixture/tls-profile.json"),
        "certificate": Path("/fixture/cert.pem"),
        "tls_key": Path("/fixture/key.pem"),
        "state": Path("/state"),
    }
    source = private_bytes(paths["source"])
    server = None
    try:
        checks = inspect_child(value["scope"])
        checks["unprivileged_uid"] = os.getuid() > 0 and os.getuid() == value["scope"]["uid"]
        checks.update(
            source_write_denied=readonly(paths["source"]),
            config_write_denied=readonly(paths["config"]),
            route_write_denied=readonly(paths["route"]),
            certificate_write_denied=readonly(paths["certificate"]),
            tls_key_write_denied=readonly(paths["tls_key"]),
            loopback_only={name for _, name in socket.if_nameindex()} == {"lo"},
            proxy_environment_cleared=not any("proxy" in name.lower() for name in os.environ),
        )
        server = FixtureServer(
            paths["certificate"],
            paths["tls_key"],
            source,
            value["secret"],
            value["behavior"],
            port=APPROVED_PORT,
        )
        checks.update(
            approved_destination_bound=server.listener.getsockname() == (HOST, APPROVED_PORT),
            unapproved_host_denied=not connectable(socket.AF_INET, ("127.0.0.2", APPROVED_PORT)),
            unapproved_port_denied=not connectable(socket.AF_INET, (HOST, APPROVED_PORT + 1)),
            test_network_denied=not connectable(socket.AF_INET, ("192.0.2.1", 443)),
        )
        print(_json({"checks": checks}), flush=True)
        if not broker_checks_valid(checks):
            return 2
        os.environ["BROKER_AUTH_KEY"] = value["key"]
        os.environ["BROKER_SOURCE_SECRET"] = value["secret"]
        broker = HTTPSBroker(
            decode(private_bytes(paths["config"])), paths["state"], expected_head=value["head"]
        )
        print(_json(_decorate(broker, server, "unarmed")), flush=True)
        for _ in range(40):
            raw = sys.stdin.buffer.readline(MAX_FRAME + 1)
            if not raw:
                broker._event("broker_shutdown")
                print(_json(_decorate(broker, server, "shutdown")), flush=True)
                return 0
            if len(raw) > MAX_FRAME or not raw.endswith(b"\n"):
                raise ValueError("framing denied")
            try:
                message = decode(raw)
            except Exception:  # noqa: BLE001 - malformed frames have no trusted fields
                message = None
            response = broker.handle(message)
            response["source_requests"] = len(server.requests)
            print(_json(response), flush=True)
        raise ValueError("session exhausted")
    finally:
        if server is not None:
            server.close()


def confined_command(paths, state):
    """Fixed owner composition: private net namespace and one writable state mount."""
    if os.getuid() == 0:
        raise ValueError("unprivileged owner required")
    root = Path(__file__).resolve().parents[1]
    command = namespace_command(
        Path(__file__),
        readonly=(
            (root / "src", "/src"),
            (root / "tests" / "isolation_lab.py", "/work/isolation_lab.py"),
            (root / "tests" / "broker_namespace_lab.py", "/work/broker_namespace_lab.py"),
            (root / "tests" / "https_broker_support.py", "/work/https_broker_support.py"),
        ),
    )
    command[command.index("--uid") + 1] = str(os.getuid())
    command[command.index("--gid") + 1] = str(os.getgid())
    index = command.index("--chdir")
    mounts = ["--dir", "/fixture"]
    for name, target in (
        ("config", "/fixture/config.json"),
        ("source", "/fixture/source.json"),
        ("route", "/fixture/tls-profile.json"),
        ("certificate", "/fixture/cert.pem"),
        ("tls_key", "/fixture/key.pem"),
    ):
        mounts.extend(("--ro-bind", str(paths[name]), target))
    mounts.extend(("--bind", str(state), "/state"))
    command[index:index] = mounts
    return command


def prepare(h, *, host=HOST):
    h.secret = hashlib.sha256(os.urandom(32)).hexdigest()
    envelope = decode(h.source.read_bytes())
    envelope["credential_digest"] = hashlib.sha256(h.secret.encode()).hexdigest()
    h.source.write_text(_json(envelope))
    h.source.chmod(0o600)
    h.config["source_path"] = "/fixture/source.json"
    h.config["source_digest"] = hashlib.sha256(h.source.read_bytes()).hexdigest()
    cert, key = certificates(h.root, hostname=host)
    profile = {
        "binding": digest(h.config),
        "port": APPROVED_PORT,
        "certificate": "/fixture/cert.pem",
        "certificate_sha256": hashlib.sha256(cert.read_bytes()).hexdigest(),
    }
    if host != HOST:
        profile["host"] = host
    route = h.root / "tls-profile.json"
    route.write_text(
        _json({"profile": profile, "mac": authenticate(h.key, "local_https_fixture", profile)})
    )
    route.chmod(0o600)
    config = h.root / "config.json"
    config.write_text(_json(h.config))
    config.chmod(0o600)
    return {
        "config": config,
        "source": h.source,
        "route": route,
        "certificate": cert,
        "tls_key": key,
    }


def start_confined(h, paths, scope, behavior, head=None):
    h.process = subprocess.Popen(
        confined_command(paths, h.state),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        close_fds=True,
        env={
            "PATH": os.defpath,
            "ORION_ISOLATION_CANARY": "synthetic",
            "HTTPS_PROXY": "http://127.0.0.1:1",
            "ALL_PROXY": "http://127.0.0.1:1",
        },
    )
    bootstrap = {
        "head": head,
        "key": h.key.decode(),
        "secret": h.secret,
        "scope": scope,
        "behavior": behavior,
    }
    h.process.stdin.write(_json(bootstrap) + "\n")
    h.process.stdin.flush()
    witness = json.loads(h.process.stdout.readline())
    if set(witness) != {"checks"} or not broker_checks_valid(witness["checks"]):
        raise ValueError("broker confinement witnesses failed")
    h.last = json.loads(h.process.stdout.readline())
    if h.last.get("status") != "unarmed" or h.last.get("source_requests") != 0:
        raise ValueError("confined broker startup failed")
    return witness["checks"], h.last


def relay_reasoner(h, paths, isolation, *, approved_host=HOST):
    from isolated_broker_lab import Frames

    root = Path(__file__).resolve().parents[1]
    script = root / "tests" / "confined_https_reasoner.py"
    command = namespace_command(
        script,
        readonly=(
            (root / "src", "/app/src"),
            (root / "tests" / "isolation_lab.py", "/work/isolation_lab.py"),
        ),
    )
    scope = {
        "message": h.message(),
        "approved_port": APPROVED_PORT,
        "approved_host": approved_host,
        "denied_paths": {
            "configuration": str(paths["config"]),
            "source": str(paths["source"]),
            "route": str(paths["route"]),
            "tls_key": str(paths["tls_key"]),
        },
        "expected": {
            "tenant_id": h.request.tenant_id,
            "authorization_id": h.grant.authorization_id,
            "count": 1,
        },
        "isolation": dict(isolation, parent=h.process.pid),
    }
    with subprocess.Popen(
        command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        close_fds=True,
        env={
            "PATH": os.defpath,
            "BROKER_SOURCE_SECRET": "parent-canary",
            "HTTPS_PROXY": "http://127.0.0.1:1",
        },
    ) as reasoner:
        frames = Frames(reasoner.stdout)
        reasoner.stdin.write(_json(scope).encode() + b"\n")
        reasoner.stdin.flush()
        request = frames.read()
        if type(request) is not dict or set(request) != {"checks", "request"}:
            raise ValueError("reasoner request invalid")
        response = h.send(request["request"])
        reasoner.stdin.write(_json(response).encode() + b"\n")
        reasoner.stdin.flush()
        output = frames.read()
        reasoner.stdin.close()
        reasoner.wait(timeout=5)
        stderr = reasoner.stderr.read(4097)
        if reasoner.returncode or stderr or set(output) != {"reasoner_result"}:
            raise ValueError("reasoner failed")
    text = _json((request, output))
    if (
        h.secret in text
        or h.key.decode() in text
        or "PRIVATE KEY" in text
        or not all(value is True for value in request["checks"].values())
    ):
        raise ValueError("reasoner credential custody failed")
    return response, request["checks"], output["reasoner_result"]


def _attacks(h):
    message = h.message()
    wrong_tenant = h.message()
    wrong_tenant["request"]["tenant_id"] = "other"
    wrong_source = h.message()
    wrong_source["request"]["source_id"] = "https://other.test"
    return [
        dict(message, operation="write"),
        dict(message, grant_token="0" * 64),
        dict(message, url="https://unapproved.invalid/"),
        dict(message, destination="127.0.0.2:44443"),
        dict(message, proxy="http://127.0.0.1:44444"),
        dict(message, headers={"Authorization": "caller-selected"}),
        dict(message, credential="caller-selected"),
        wrong_tenant,
        wrong_source,
        {"control": "arm", "nonce": h.last["nonce"], "head": h.last["head"], "mac": "0" * 64},
    ]


def run_case(root, protocol, *, behavior="ok", durable_control="revoke"):
    from test_supervised_broker import Harness

    h = Harness(root / "broker", protocol)
    paths = prepare(h)
    protected_before = {name: path.read_bytes() for name, path in paths.items()}
    secret, audit = root / "owner-secret", root / "owner-audit"
    secret.write_text("synthetic-owner-canary")
    secret.chmod(0o600)
    audit.write_bytes(b"original")
    audit.chmod(0o600)
    with ExitStack() as stack:
        tcp = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
        unix = stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
        tcp.bind((HOST, 0))
        tcp.listen(4)
        unix_path = str(root / "owner-listener")
        unix.bind(unix_path)
        unix.listen(4)
        owner_negative = (
            all(readable(path) for path in (*paths.values(), secret, audit))
            and connectable(socket.AF_INET, (HOST, tcp.getsockname()[1]))
            and connectable(socket.AF_UNIX, unix_path)
            and not readonly(paths["config"])
            and not readonly(paths["source"])
        )
        with audit.open("ab") as stream:
            stream.write(b"-negative-control")
        audit_before = audit.read_bytes()
        scope = {
            "secret": str(secret),
            "audit": str(audit),
            "unix": unix_path,
            "port": tcp.getsockname()[1],
            "parent": os.getpid(),
            "uid": os.getuid(),
        }
        scope.update(
            {kind: os.readlink("/proc/self/ns/" + kind) for kind in ("net", "pid", "mnt", "user")}
        )
        witnesses = []
        try:
            witness, _ = start_confined(h, paths, scope, behavior)
            witnesses.append(witness)
            assert h.send(h.message())["budget"]["attempts"] == 0
            assert h.control("arm")["status"] == "arm"
            attacks = _attacks(h)
            for attack in attacks:
                denied = h.send(attack)
                assert denied["status"] == "denied"
                assert denied["budget"]["attempts"] == 0 and denied["source_requests"] == 0
            reasoner_checks, reasoner_result, observations = {}, {}, ()
            if behavior == "ok":
                response, reasoner_checks, reasoner_result = relay_reasoner(h, paths, scope)
                assert response["status"] == "admitted" and response["source_requests"] == 1
                observations = observations_from(response["observations"])
                assert observations[0].evidence.payload["record"] == h.rows[0]
                assert (
                    observations[0].evidence.payload["provenance"]["authorization_id"]
                    == h.grant.authorization_id
                )
                assert (
                    reasoner_result["canonical_immutable"]
                    and not reasoner_result["execution_allowed"]
                )
            else:
                response = h.send(h.message("redirect"))
                assert response["status"] == "denied" and "observations" not in response
                assert response["source_requests"] == 1 and response["budget"]["failures"] == 1
            budget = {
                name: response["budget"][name]
                for name in ("attempts", "failures", "reserved_bytes")
            }
            request_identity = "q_01" if behavior == "ok" else "redirect"
            duplicate = h.send(h.message(request_identity))
            assert duplicate["status"] == "denied" and duplicate["source_requests"] == 1
            assert duplicate["budget"]["attempts"] == 1
            assert h.control(durable_control)["status"] == durable_control
            first_final = h.close()
            assert first_final["source_requests"] == 1
            head = first_final["head"]
            witness, restarted = start_confined(h, paths, scope, behavior, head)
            witnesses.append(witness)
            assert restarted["source_requests"] == 0 and restarted["budget"]["stopped"]
            assert h.control("arm")["status"] == "denied"
            denied = h.send(h.message("restart"))
            assert denied["status"] == "denied" and denied["source_requests"] == 0
            assert {name: denied["budget"][name] for name in budget} == budget
            second_final = h.close()
            assert second_final["source_requests"] == 0
            unchanged = audit.read_bytes() == audit_before and all(
                path.read_bytes() == protected_before[name] for name, path in paths.items()
            )
            return {
                "status": "PASS" if owner_negative and unchanged else "FAIL",
                "protocol": protocol,
                "behavior": behavior,
                "durable_control": durable_control,
                "broker_checks": {name: all(w[name] for w in witnesses) for name in witnesses[0]},
                "reasoner_checks": reasoner_checks,
                "reasoner_result": reasoner_result,
                "owner_negative_controls": owner_negative,
                "pre_io_denials": len(attacks),
                "attempts_before_acquisition": 0,
                "source_requests_before_acquisition": 0,
                "reasoner_credentials_inaccessible": (
                    all(reasoner_checks.values()) if behavior == "ok" else None
                ),
                "source_requests": 1,
                "restart_source_requests": 0,
                "observations": len(observations),
                "provenance_intact": bool(observations) if behavior == "ok" else True,
                "budget_after_restart": budget,
                "execution_allowed": False,
                "live_ready": False,
            }
        finally:
            if h.process and h.process.poll() is None:
                h.close()


def run():
    report = result("BLOCKED", "kernel_prerequisite")
    report["cases"] = []
    prerequisite = run_lab()
    if prerequisite["status"] != "PASS":
        report["reason"] = "kernel_prerequisite_" + prerequisite["reason"]
        return report
    if os.getuid() == 0:
        report["reason"] = "unprivileged_owner_required"
        return report
    try:
        with tempfile.TemporaryDirectory(prefix="orion-confined-https-") as temporary:
            root = Path(temporary)
            cases = [
                run_case(root / "rows-revoke", "local_rows_v1", durable_control="revoke"),
                run_case(root / "columns-stop", "local_columns_v1", durable_control="stop"),
                run_case(root / "redirect", "local_rows_v1", behavior="redirect"),
            ]
        report.update(
            status="PASS" if all(case["status"] == "PASS" for case in cases) else "FAIL",
            reason="confined_synthetic_https_destination_only",
            negative_controls=True,
            cases=cases,
        )
    except (FileNotFoundError, ImportError):
        report["reason"] = "local_development_dependency_missing"
    except OSError:
        report["reason"] = "local_runtime_unavailable"
    except Exception:  # noqa: BLE001 - never disclose bootstrap, path, credential or response data
        report.update(status="FAIL", reason="confined_https_lifecycle_failed")
    return report


if __name__ == "__main__":
    if sys.argv[1:] == ["--child"]:
        try:
            raise SystemExit(broker_child())
        except Exception:  # noqa: BLE001 - never disclose private bootstrap or source details
            raise SystemExit(2) from None
    if len(sys.argv) != 1:
        raise SystemExit(2)
    print(_json(run()))
