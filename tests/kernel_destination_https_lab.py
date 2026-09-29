"""Reachable synthetic source outside broker netns; kernel-enforced single route."""

import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
from contextlib import ExitStack
from pathlib import Path

if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from broker_namespace_lab import readonly
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
from https_broker_support import FixtureServer, HTTPSBroker
from isolation_lab import CHECKS, connectable, inspect_child, readable, result

from orion.pilot.broker_contract import MAX_FRAME, decode, exact, observations_from, private_bytes
from orion.understanding.role_checkpoint import _json

EXTRA_CHECKS = (
    "configuration_readonly",
    "route_readonly",
    "certificate_readonly",
    "source_file_inaccessible",
    "source_private_key_inaccessible",
    "source_namespace_distinct",
    "configured_network_retained",
    "firewall_mutation_denied",
)


def frame():
    raw = sys.stdin.buffer.readline(MAX_FRAME + 1)
    if not raw.endswith(b"\n") or len(raw) > MAX_FRAME:
        raise ValueError("private fixture framing denied")
    return decode(raw)


class CanaryServer:
    """Fixed byte echo listener. Not a proxy, tunnel, transport or network relay."""

    def __init__(self, host, port):
        self.listener = socket.socket(
            socket.AF_INET6 if ":" in host else socket.AF_INET, socket.SOCK_STREAM
        )
        if ":" in host:
            self.listener.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
        self.listener.bind((host, port))
        self.listener.listen(4)
        self.listener.settimeout(0.1)
        self.accepted = 0
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.stop.is_set():
            try:
                client, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            self.accepted += 1
            with client:
                client.settimeout(1)
                try:
                    if client.recv(64) == b"synthetic-control":
                        client.sendall(b"synthetic-reachable")
                except OSError:
                    pass

    def close(self):
        self.stop.set()
        self.listener.close()
        self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise ValueError("fixture listener did not stop")


def source_child(value):
    exact(value, ("role", "secret", "behavior", "ipv6", "source_net", "broker_host"))
    if value["behavior"] not in ("ok", "redirect") or type(value["ipv6"]) is not bool:
        raise ValueError("fixed source required")
    source_net = os.readlink("/proc/self/ns/net")
    if source_net != value["source_net"]:
        raise ValueError("source private namespace not retained")
    families = [(V4_APPROVED, V4_UNAPPROVED)]
    if value["ipv6"]:
        families.append((V6_APPROVED, V6_UNAPPROVED))
    tls, canaries = [], []
    with ExitStack() as stack:
        for approved, unapproved in families:
            server = FixtureServer(
                Path("/fixture/cert.pem"),
                Path("/fixture/key.pem"),
                private_bytes("/fixture/source.json"),
                value["secret"],
                value["behavior"],
                host=approved,
                port=PORT,
                redirect=(
                    f"https://[{V6_UNAPPROVED}]:{PORT}/fixture"
                    if ":" in value["broker_host"]
                    else f"https://{V4_UNAPPROVED}:{PORT}/fixture"
                ),
            )
            tls.append(server)
            stack.callback(server.close)
            for host, port in ((unapproved, PORT), (approved, PORT + 1), (unapproved, PORT + 2)):
                canary = CanaryServer(host, port)
                canaries.append(canary)
                stack.callback(canary.close)
        print(_json({"ready": True, "net": source_net}), flush=True)
        for _ in range(20):
            message = frame()
            if message == {"fixture": "stop"}:
                return 0
            if message != {"fixture": "stats"}:
                raise ValueError("fixed fixture command required")
            print(
                _json(
                    {
                        "requests": sum(len(server.requests) for server in tls),
                        "canary_connections": sum(server.accepted for server in canaries),
                        "authorized": all(
                            request["get"] and request["authorized"]
                            for server in tls
                            for request in server.requests
                        ),
                        "net": source_net,
                    }
                ),
                flush=True,
            )
    raise ValueError("source fixture session exhausted")


def broker_child(value):
    exact(
        value,
        (
            "role",
            "key",
            "secret",
            "head",
            "scope",
            "source_net",
            "broker_net",
            "host",
            "ipv6",
            "source_path",
            "key_path",
        ),
    )
    checks = inspect_child(value["scope"])
    # Mapped root in TWO private user namespaces, with ALL capabilities removed.
    # The outer launcher rejects initial-namespace root and attests the owner map.
    checks["unprivileged_uid"] = (
        os.getuid() == 0 and os.readlink("/proc/self/ns/user") != value["scope"]["user"]
    )
    config, route, cert = (
        Path("/fixture/" + name) for name in ("config.json", "tls-profile.json", "cert.pem")
    )
    mutation = subprocess.run(
        [shutil.which("nft", path=LAB_PATH), "delete", "table", "inet", "orion"],
        capture_output=True,
        timeout=3,
        check=False,
    )
    checks.update(
        configuration_readonly=readonly(config),
        route_readonly=readonly(route),
        certificate_readonly=readonly(cert),
        source_file_inaccessible=not readable(value["source_path"])
        and not readable("/fixture/source.json"),
        source_private_key_inaccessible=not readable(value["key_path"])
        and not readable("/fixture/key.pem"),
        source_namespace_distinct=os.readlink("/proc/self/ns/net") != value["source_net"],
        configured_network_retained=os.readlink("/proc/self/ns/net") == value["broker_net"],
        firewall_mutation_denied=mutation.returncode != 0,
    )
    print(_json({"checks": checks}), flush=True)
    if set(checks) != set(CHECKS + EXTRA_CHECKS) or not all(
        item is True for item in checks.values()
    ):
        raise ValueError("broker kernel isolation witness failed")
    # No policy object, URL, HTTP client, authorization or Broker participates.
    # The owner reads kernel counters after EACH raw socket attempt before ACK.
    for name, family, host, port in targets(value["ipv6"], value["host"]):
        with socket.socket(family, socket.SOCK_STREAM) as client:
            client.settimeout(0.3)
            try:
                client.connect((host, port))
            except OSError as error:
                code = error.errno
                connected = False
            else:
                code = 0
                connected = True
        print(_json({"direct": name, "connected": connected, "errno": code}), flush=True)
        if frame() != {"ack": name}:
            raise ValueError("kernel counter handshake failed")
    os.environ["BROKER_AUTH_KEY"] = value["key"]
    os.environ["BROKER_SOURCE_SECRET"] = value["secret"]

    class CountedBroker(HTTPSBroker):
        acquisitions = 0

        def _worker(self, bootstrap, **kwargs):
            self.acquisitions += 1
            return super()._worker(bootstrap, **kwargs)

        def status(self, status):
            return dict(super().status(status), source_requests=self.acquisitions)

    broker = CountedBroker(
        decode(private_bytes(config)), Path("/state"), expected_head=value["head"]
    )
    print(_json(broker.status("unarmed")), flush=True)
    for _ in range(40):
        raw = sys.stdin.buffer.readline(MAX_FRAME + 1)
        if not raw:
            broker._event("broker_shutdown")
            print(_json(broker.status("shutdown")), flush=True)
            return 0
        if len(raw) > MAX_FRAME or not raw.endswith(b"\n"):
            raise ValueError("broker frame denied")
        try:
            message = decode(raw)
        except Exception:  # noqa: BLE001 - malformed messages confer no authority
            message = None
        print(_json(broker.handle(message)), flush=True)
    raise ValueError("broker fixture session exhausted")


def command(paths, *, source=False, state=None):
    root = Path(__file__).resolve().parents[1]
    mounts = [(root / "src", "/src")]
    for name in (
        "isolation_lab",
        "broker_namespace_lab",
        "https_broker_support",
        "destination_network_lab",
    ):
        mounts.append((root / "tests" / (name + ".py"), "/work/" + name + ".py"))
    names = [("certificate", "cert.pem")]
    names += (
        [("source", "source.json"), ("tls_key", "key.pem")]
        if source
        else [("config", "config.json"), ("route", "tls-profile.json")]
    )
    mounts += [(paths[name], "/fixture/" + target) for name, target in names]
    return jail_command(Path(__file__), mounts, state=state)


def stats(source):
    source.stdin.write(_json({"fixture": "stats"}) + "\n")
    source.stdin.flush()
    return json.loads(source.stdout.readline(4097))


def controls(ipv6, host):
    values = {}
    approved = [("approved-ipv4", socket.AF_INET, V4_APPROVED)]
    if ipv6:
        approved.append(("approved-ipv6", socket.AF_INET6, V6_APPROVED))
    for name, family, address in approved:
        values[name] = connectable(family, (address, PORT))
    for name, family, address, port in targets(ipv6, host):
        if name.endswith("other-family"):
            values[name] = connectable(family, (address, port))
            continue
        with socket.socket(family, socket.SOCK_STREAM) as client:
            client.settimeout(1)
            client.connect((address, port))
            client.sendall(b"synthetic-control")
            values[name] = client.recv(64) == b"synthetic-reachable"
    if not all(values.values()):
        raise KernelUnavailable("reachable controls unavailable")
    return values


def start_broker(fabric, source, h, paths, scope, host, head=None):
    broker = fabric.spawn("broker")
    before = fabric.connect(source, broker, host)
    assert before["approved"] == 0
    fabric.boot(
        broker,
        command(paths, state=h.state),
        {
            "role": "broker",
            "key": h.key.decode(),
            "secret": h.secret,
            "head": head,
            "scope": scope,
            "host": host,
            "ipv6": fabric.ipv6,
            "source_net": source.private_net,
            "broker_net": broker.private_net,
            "source_path": str(paths["source"]),
            "key_path": str(paths["tls_key"]),
        },
    )
    witness = json.loads(broker.stdout.readline(4097))["checks"]
    assert set(witness) == set(CHECKS + EXTRA_CHECKS) and all(witness.values()), witness
    direct = []
    for name, _, _, _ in targets(fabric.ipv6, host):
        value = json.loads(broker.stdout.readline(4097))
        after = fabric.counters(broker)
        assert set(value) == {"direct", "connected", "errno"}
        assert value["direct"] == name and value["connected"] is False
        counter = counter_for(name)
        assert after[counter] > before[counter] and after["approved"] == before["approved"]
        direct.append(
            dict(value, counter=counter, kernel_rejections=after[counter] - before[counter])
        )
        before = after
        broker.stdin.write(_json({"ack": name}) + "\n")
        broker.stdin.flush()
    h.process = broker
    h.last = json.loads(broker.stdout.readline(4097))
    assert h.last["status"] == "unarmed" and h.last["source_requests"] == 0
    return witness, direct


def run_case(
    root, protocol, initial_user, *, ipv6_acquisition=False, behavior="ok", durable_control="revoke"
):
    from confined_https_broker_lab import _attacks, prepare, relay_reasoner
    from test_supervised_broker import Harness

    h = Harness(root / "broker", protocol)
    h.config["limits"]["max_requests"] = 1
    fabric = Fabric(Path(__file__).resolve())
    fabric.initial_user = initial_user
    try:
        fabric.initialize()
        host = V6_APPROVED if fabric.ipv6 and ipv6_acquisition else V4_APPROVED
        paths = prepare(h, host=host)
        original = {name: path.read_bytes() for name, path in paths.items()}
        source = fabric.spawn("source")
        fabric.boot(
            source,
            command(paths, source=True),
            {
                "role": "source",
                "secret": h.secret,
                "behavior": behavior,
                "ipv6": fabric.ipv6,
                "source_net": source.private_net,
                "broker_host": host,
            },
        )
        assert json.loads(source.stdout.readline(4097)) == {
            "ready": True,
            "net": source.private_net,
        }
        reachable = controls(fabric.ipv6, host)
        baseline = stats(source)
        assert baseline["requests"] == 0 and baseline["authorized"]
        secret, audit = root / "secret", root / "audit"
        secret.write_text("synthetic-owner-canary")
        secret.chmod(0o600)
        audit.write_bytes(b"synthetic-audit-control")
        with (
            socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp,
            socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as unix,
        ):
            tcp.bind(("127.0.0.1", 0))
            tcp.listen(4)
            unix_path = str(root / "unix")
            unix.bind(unix_path)
            unix.listen(4)
            assert connectable(socket.AF_INET, tcp.getsockname())
            assert connectable(socket.AF_UNIX, unix_path)
            assert all(readable(path) for path in (*paths.values(), secret, audit))
            with audit.open("ab") as stream:
                stream.write(b"-positive-write-control")
            audit_before = audit.read_bytes()
            scope = {
                "secret": str(secret),
                "audit": str(audit),
                "unix": unix_path,
                "port": tcp.getsockname()[1],
                "parent": os.getpid(),
            }
            scope.update(
                {
                    kind: os.readlink("/proc/self/ns/" + kind)
                    for kind in ("net", "pid", "mnt", "user")
                }
            )
            witnesses, direct_runs = [], []
            witness, direct = start_broker(fabric, source, h, paths, scope, host)
            witnesses.append(witness)
            direct_runs.append(direct)
            assert h.send(h.message())["budget"]["attempts"] == 0
            assert h.control("arm")["status"] == "arm"
            before = fabric.counters(h.process)
            for attack in _attacks(h):
                denied = h.send(attack)
                assert denied["status"] == "denied" and denied["budget"]["attempts"] == 0
            assert fabric.counters(h.process) == before
            assert stats(source) == baseline  # No TCP, TLS, canary or HTTP source I/O.
            reasoner_checks, reasoner_result, observations = {}, {}, ()
            if behavior == "ok":
                response, reasoner_checks, reasoner_result = relay_reasoner(
                    h, paths, scope, approved_host=host
                )
                assert response["status"] == "admitted"
                observations = observations_from(response["observations"])
                assert observations[0].evidence.payload["record"] == h.rows[0]
                assert observations[0].evidence.payload["provenance"]["authorization_id"] == (
                    h.grant.authorization_id
                )
            else:
                response = h.send(h.message())
                assert response["status"] == "denied" and "observations" not in response
            acquired = stats(source)
            after = fabric.counters(h.process)
            assert after["approved"] > before["approved"] and after["denied"] == before["denied"]
            assert acquired["requests"] == 1 and acquired["authorized"]
            assert acquired["canary_connections"] == baseline["canary_connections"]
            budget = {
                name: response["budget"][name]
                for name in ("attempts", "failures", "reserved_bytes")
            }
            assert budget == {
                "attempts": 1,
                "failures": int(behavior == "redirect"),
                "reserved_bytes": 65536,
            }
            assert h.send(h.message())["status"] == "denied"  # Replay.
            head = h.close()["head"]
            # Restart BEFORE stop: budget exhaustion must not reset into a new allowance.
            witness, direct = start_broker(fabric, source, h, paths, scope, host, head)
            witnesses.append(witness)
            direct_runs.append(direct)
            assert h.control("arm")["status"] == "arm"
            before_restart = fabric.counters(h.process)
            denied = h.send(h.message("budget-restart"))
            assert denied["status"] == "denied"
            assert {name: denied["budget"][name] for name in budget} == budget
            assert fabric.counters(h.process) == before_restart and stats(source) == acquired
            assert h.control(durable_control)["status"] == durable_control
            head = h.close()["head"]
            witness, direct = start_broker(fabric, source, h, paths, scope, host, head)
            witnesses.append(witness)
            direct_runs.append(direct)
            assert h.last["budget"]["stopped"] and h.control("arm")["status"] == "denied"
            denied = h.send(h.message("durable-restart"))
            assert denied["status"] == "denied" and denied["source_requests"] == 0
            assert {name: denied["budget"][name] for name in budget} == budget
            assert stats(source) == acquired and fabric.counters(h.process)["approved"] == 0
            h.close()
            assert audit.read_bytes() == audit_before
            assert all(path.read_bytes() == original[name] for name, path in paths.items())
        source.stdin.write(_json({"fixture": "stop"}) + "\n")
        source.stdin.flush()
        source.wait(timeout=5)
        assert source.returncode == 0 and not source.stderr.read(4097)
        return {
            "status": "PASS",
            "protocol": protocol,
            "host": host,
            "ipv6_enabled": fabric.ipv6,
            "behavior": behavior,
            "durable_control": durable_control,
            "reachable_controls": reachable,
            "source_outside_broker": True,
            "kernel_direct_denials": direct_runs,
            "broker_checks": {name: all(w[name] for w in witnesses) for name in witnesses[0]},
            "reasoner_checks": reasoner_checks,
            "reasoner_result": reasoner_result,
            "pre_io_denials": 10,
            "source_requests_before_acquisition": 0,
            "source_requests": 1,
            "approved_acquisition_packets": after["approved"] - before["approved"],
            "unauthorized_approved_packets": 0,
            "restart_approved_packets": 0,
            "restart_source_requests": 0,
            "unapproved_connections_after_controls": 0,
            "budget_restart_exhaustion_denied": True,
            "budget_after_restart": budget,
            "observations": len(observations),
            "provenance_intact": True,
            "execution_allowed": False,
            "live_ready": False,
        }
    finally:
        fabric.close()


def setup():
    value = exact(frame(), ("initial_user", "initial_net", "initial_uid"))
    report = result("BLOCKED", "kernel_destination_prerequisite")
    report["cases"] = []
    if (
        value["initial_uid"] == 0
        or os.getuid() != 0
        or os.readlink("/proc/self/ns/user") == value["initial_user"]
        or os.readlink("/proc/self/ns/net") == value["initial_net"]
    ):
        return report
    try:
        # Each case gets a new fabric netns too: no fixture interface/name reuse.
        cases = []
        for protocol, ipv6, behavior, control in (
            ("local_rows_v1", False, "ok", "revoke"),
            ("local_columns_v1", True, "ok", "stop"),
            ("local_rows_v1", False, "redirect", "revoke"),
            ("local_columns_v1", True, "redirect", "stop"),
        ):
            completed = subprocess.run(
                ["unshare", "--net", sys.executable, "-I", str(Path(__file__).resolve()), "--case"],
                input=_json(
                    dict(value, protocol=protocol, ipv6=ipv6, behavior=behavior, control=control)
                )
                + "\n",
                capture_output=True,
                text=True,
                timeout=35,
                check=False,
            )
            if completed.returncode or completed.stderr:
                raise KernelUnavailable("case namespace unavailable")
            case = json.loads(completed.stdout)
            if case.get("status") != "PASS":
                report.update(status=case["status"], reason=case["reason"])
                return report
            cases.append(case)
        report.update(
            status="PASS",
            reason="kernel_single_synthetic_https_destination",
            negative_controls=True,
            rootless_setup=True,
            cases=cases,
        )
    except (KernelUnavailable, OSError, subprocess.TimeoutExpired):
        pass
    except Exception:  # noqa: BLE001 - never print bootstrap/source/credential details
        report.update(status="FAIL", reason="kernel_destination_lifecycle_failed")
    return report


def case_child():
    value = frame()
    try:
        with tempfile.TemporaryDirectory(prefix="orion-kernel-destination-") as temporary:
            return run_case(
                Path(temporary),
                value["protocol"],
                value["initial_user"],
                ipv6_acquisition=value["ipv6"],
                behavior=value["behavior"],
                durable_control=value["control"],
            )
    except (KernelUnavailable, OSError):
        return {"status": "BLOCKED", "reason": "private_kernel_network_unavailable"}
    except Exception:  # noqa: BLE001 - suppress credentials and private source data
        return {"status": "FAIL", "reason": "kernel_destination_assertion_failed"}


def run():
    report = result("BLOCKED", "kernel_network_tools_unavailable")
    report["cases"] = []
    if not prerequisites() or os.getuid() == 0:
        return report
    try:
        completed = subprocess.run(
            [
                "unshare",
                "--user",
                "--map-root-user",
                "--net",
                sys.executable,
                "-I",
                str(Path(__file__).resolve()),
                "--setup",
            ],
            input=_json(
                {
                    "initial_user": os.readlink("/proc/self/ns/user"),
                    "initial_net": os.readlink("/proc/self/ns/net"),
                    "initial_uid": os.getuid(),
                }
            )
            + "\n",
            capture_output=True,
            text=True,
            timeout=150,
            env={"PATH": LAB_PATH},
            check=False,
        )
        if completed.returncode or completed.stderr or len(completed.stdout) > MAX_FRAME:
            raise KernelUnavailable("private setup unavailable")
        return json.loads(completed.stdout)
    except (KernelUnavailable, OSError, ValueError, subprocess.TimeoutExpired):
        return report


if __name__ == "__main__":
    if sys.argv[1:] == ["--net-child"]:
        net_child()
    elif sys.argv[1:] == ["--child"]:
        try:
            value = frame()
            raise SystemExit(
                source_child(value) if value["role"] == "source" else broker_child(value)
            )
        except Exception:  # noqa: BLE001 - private bootstrap never printed
            raise SystemExit(2) from None
    elif sys.argv[1:] == ["--setup"]:
        print(_json(setup()))
    elif sys.argv[1:] == ["--case"]:
        print(_json(case_child()))
    elif not sys.argv[1:]:
        print(_json(run()))
    else:
        raise SystemExit(2)
