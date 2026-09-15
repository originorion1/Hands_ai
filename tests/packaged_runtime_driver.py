"""External deployment acceptance driver; the HTTPS source uses only stdlib.

The installed runtime never imports this file. Run its controller from a clean
venv inside a disposable mapped user/network namespace, with no PYTHONPATH.
"""

import hashlib
import http.server
import json
import os
import secrets
import signal
import socket
import ssl
import subprocess
import sys
import threading
import time
import traceback
from pathlib import Path


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def runtime_report(value, checks, identity, ready, named_denials):
    success = all(checks.values())
    emit(
        {
            "status": "PASS" if success else "FAIL",
            "case": value["case"],
            "checks": checks,
            "artifact": identity,
            "wheel_sha256": value["wheel_sha256"],
            "source_unmodified": True,
            "clean_installed_artifact": True,
            "ipv6_enabled": ready["ipv6_enabled"],
            "named_kernel_rejects": named_denials,
            "LIVE_PILOT_READY": False,
        }
    )
    return 0 if success else 1


def read_frame(stream):
    line = stream.readline(1048577)
    if not line or len(line) > 1048576:
        raise ValueError("bounded deployment handshake required")
    return json.loads(line)


def source(value):
    """Ordinary HTTPS/Bearer fixture: no ORION imports, grants, receipts or IPC."""
    io = {"metadata": 0, "read": 0}
    connections = {"canary": 0, "approved": 0}
    lock = threading.Lock()
    credential = value["source_credential"]
    bodies = {
        "/metadata": value["bodies"]["metadata"].encode(),
        "/records": value["bodies"]["read"].encode(),
    }

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            if self.headers.get("Authorization") != "Bearer " + credential:
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            if self.path not in bodies:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = bodies[self.path]
            with lock:
                io["metadata" if self.path == "/metadata" else "read"] += 1
            if self.path == "/records" and value["redirect"]:
                self.send_response(302)
                self.send_header("Location", "https://192.0.2.3:44443/records")
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_arguments):
            pass

    servers, listeners = [], []
    families = [("192.0.2.2", "192.0.2.3")]
    if value["ipv6"]:
        families.append(("fd42:6f72:696f::2", "fd42:6f72:696f::3"))
    for approved, unapproved in families:

        class Server(http.server.ThreadingHTTPServer):
            address_family = socket.AF_INET6 if ":" in approved else socket.AF_INET

            def get_request(self):
                peer, address = self.socket.accept()
                with lock:
                    connections["approved"] += 1
                peer.settimeout(1)
                try:
                    return self.tls.wrap_socket(peer, server_side=True), address
                except OSError:
                    peer.close()
                    raise

        server = Server((approved, 44443), Handler)
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        context.load_cert_chain(value["certificate"], value["source_key"])
        server.tls = context
        threading.Thread(target=server.serve_forever, daemon=True).start()
        servers.append(server)
        for host, port in ((unapproved, 44443), (approved, 44444), (unapproved, 44445)):
            listener = socket.socket(socket.AF_INET6 if ":" in host else socket.AF_INET)
            listener.bind((host, port))
            listener.listen(8)

            def accept(listener=listener):
                while True:
                    try:
                        connection, _ = listener.accept()
                    except OSError:
                        return
                    with lock:
                        connections["canary"] += 1
                    connection.close()

            threading.Thread(target=accept, daemon=True).start()
            listeners.append(listener)
    emit(
        {
            "ready": True,
            "net": os.readlink("/proc/self/ns/net"),
            "orion_imports": [
                name for name in sys.modules if name == "orion" or name.startswith("orion.")
            ],
        }
    )
    try:
        while True:
            command = read_frame(sys.stdin)
            if command == {"command": "stats"}:
                with lock:
                    emit(dict(io))
            elif command == {"command": "connections"}:
                with lock:
                    emit(dict(connections))
            elif command == {"command": "shutdown"}:
                return
            else:
                raise ValueError("fixed source fixture command required")
    finally:
        for server in servers:
            server.shutdown()
        for listener in listeners:
            listener.close()


def command(process, value):
    process.stdin.write(json.dumps(value) + "\n")
    process.stdin.flush()
    return read_frame(process.stdout)


def connect(pid, host, port):
    subprocess.run(
        [
            "nsenter",
            "--preserve-credentials",
            "--user=/proc/" + str(pid) + "/ns/user",
            "--net=/proc/" + str(pid) + "/ns/net",
            sys.executable,
            "-I",
            "-c",
            (
                "import socket,sys; s=socket.socket(socket.AF_INET6 if ':' in sys.argv[1] "
                "else socket.AF_INET); s.settimeout(1); s.connect((sys.argv[1],int(sys.argv[2])))"
            ),
            host,
            str(port),
        ],
        check=True,
        capture_output=True,
        text=True,
        timeout=5,
    )


def source_command(pid, control_pid, value):
    """The independent ordinary fixture sees only stdlib, certificate and TLS key."""
    argv = [
        "nsenter",
        "--preserve-credentials",
        "--user=/proc/" + str(control_pid) + "/ns/user",
        "--net=/proc/" + str(pid) + "/ns/net",
        "bwrap",
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--clearenv",
    ]
    for location in ("/usr", "/lib", "/lib64", "/bin"):
        if Path(location).exists():
            argv.extend(("--ro-bind", location, location))
    argv.extend(
        (
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--tmpfs",
            "/tmp",
            "--dir",
            "/work",
            "--dir",
            "/fixture",
        )
    )
    for location in dict.fromkeys((sys.base_prefix, sys.prefix)):
        if not Path(location).is_relative_to("/usr"):
            argv.extend(("--ro-bind", location, location))
    argv.extend(
        (
            "--ro-bind",
            str(Path(__file__).resolve()),
            "/work/source.py",
            "--ro-bind",
            value["certificate"],
            "/fixture/cert.pem",
            "--ro-bind",
            value["source_key"],
            "/fixture/key.pem",
            "--chdir",
            "/work",
            sys.executable,
            "-I",
            "/work/source.py",
            "--source",
        )
    )
    return argv


def controller(value):
    # Imports are inside the controller: the ordinary source has no ORION dependency.
    from orion.pilot.broker_contract import authenticate, digest, observations_from
    from orion.pilot.ipc import rpc
    from orion.pilot.isolation import LAB_PATH, counter_for
    from orion.pilot.journal import JournalDenied

    root = Path(value["root"])
    keys, state = root / "runtime-keys", root / "runtime-state"
    keys.mkdir(mode=0o700)
    state.mkdir(mode=0o700)
    private = {
        "issuer": value["issuer"],
        "worker-secret": value["worker_secret"],
        "source-credential": value["source_credential"],
        "audit-signing": secrets.token_hex(32),
        "evidence-signing": secrets.token_hex(32),
    }
    for name, content in private.items():
        path = keys / name
        path.write_text(content)
        path.chmod(0o600)
    artifact = subprocess.run(
        [sys.executable, "-I", "-m", "orion.pilot.deployment", "--artifact"],
        check=True,
        capture_output=True,
        text=True,
        timeout=10,
    )
    identity = json.loads(artifact.stdout)
    manifest = {
        "version": 1,
        "mode": "synthetic_read_only",
        "configs": value["configs"],
        "state_directory": str(state),
        "keys_directory": str(keys),
        "certificate": value["certificate"],
        "certificate_sha256": hashlib.sha256(Path(value["certificate"]).read_bytes()).hexdigest(),
        "artifact_record_sha256": identity["record_sha256"],
        "host": value["host"],
        "policy": {
            "max_entries": 20,
            "max_bytes": 1048576,
            "ttl_seconds": 1 if value["case"] == "retention" else 3600,
        },
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest))
    manifest_path.chmod(0o600)
    misplaced_denied = None
    if value["case"] == "security":
        misplaced = Path(sys.prefix) / ("invalid-custody-" + root.name)
        misplaced.mkdir(mode=0o700)
        for name in ("keys", "state"):
            (misplaced / name).mkdir(mode=0o700)
        invalid_manifest = dict(manifest, keys_directory=str(misplaced / "keys"),
                                state_directory=str(misplaced / "state"))
        invalid_path = root / "invalid-placement.json"
        invalid_path.write_text(json.dumps(invalid_manifest))
        invalid_path.chmod(0o600)
        rejected = subprocess.run(
            [sys.executable, "-I", "-m", "orion.pilot.deployment", "--serve", str(invalid_path)],
            capture_output=True, text=True, env={"PATH": os.defpath}, timeout=10, check=False,
        )
        placement_result = json.loads(rejected.stdout)
        misplaced_denied = (
            rejected.returncode == 2
            and placement_result["status"] == "BLOCKED"
            and placement_result.get("failure_type") == "KernelUnavailable"
            and placement_result.get("failure_boundary") == "validate_protected_paths"
        )
    baseline_fds = []
    runtime_command = [sys.executable, "-I", "-m", "orion.pilot.deployment", "--serve", str(manifest_path)]
    if value["case"] == "security":
        baseline_fds = [os.open("/proc/self/ns/" + kind, os.O_RDONLY) for kind in ("net", "user")]
        runtime_command = [
            "/usr/bin/unshare", "--user", "--map-root-user", "--net", sys.executable,
            "-I", value["attacker_script"], "--supervisor", str(manifest_path),
            *(str(fd) for fd in baseline_fds),
        ]
    runtime = subprocess.Popen(
        runtime_command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env={"PATH": os.defpath},
        close_fds=True,
        pass_fds=baseline_fds,
    )
    for descriptor in baseline_fds:
        os.close(descriptor)
    server = None
    try:
        ready = read_frame(runtime.stdout)
        if ready.get("status") == "BLOCKED":
            emit(ready)
            return 2
        assert ready["status"] == "unarmed", ready
        server = subprocess.Popen(
            source_command(ready["source_pid"], ready["control_pid"], value),
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env={"PATH": os.defpath},
            close_fds=True,
        )
        server.stdin.write(
            json.dumps(
                {
                    "bodies": value["bodies"],
                    "source_credential": value["source_credential"],
                    "certificate": "/fixture/cert.pem",
                    "source_key": "/fixture/key.pem",
                    "ipv6": ready["ipv6_enabled"],
                    "redirect": value["case"] == "redirect",
                }
            )
            + "\n"
        )
        server.stdin.flush()
        try:
            source_ready = read_frame(server.stdout)
        except ValueError:
            diagnostic = server.stderr.read(4096)
            emit(
                {
                    "status": "FAIL",
                    "reason": "ordinary_source_jail_startup_failed",
                    "diagnostic": {
                        term: term in diagnostic
                        for term in (
                            "nsenter",
                            "bwrap",
                            "Operation not permitted",
                            "Permission denied",
                            "No such file or directory",
                            "Invalid argument",
                            "reassociate",
                            "ns/user",
                            "setgroups",
                        )
                    },
                    "LIVE_PILOT_READY": False,
                }
            )
            return 1
        assert source_ready["ready"] is True
        endpoints = ready["endpoints"]
        operator = json.loads(Path(ready["operator_capability_file"]).read_text())
        reasoning = json.loads(Path(ready["reasoning_capability_file"]).read_text())
        owner_key = operator["owner"].encode()
        reader_key = reasoning["reasoner"].encode()
        checks = {
            role + "_" + name: passed
            for role, items in ready["checks"].items()
            for name, passed in items.items()
        }
        checks["ordinary_source_has_no_orion_dependency"] = source_ready["orion_imports"] == []
        if value["case"] == "security":
            checks["installed_prefix_original_path_custody_denied_at_startup"] = misplaced_denied
        checks["source_outside_gateway_namespace"] = source_ready["net"] != os.readlink(
            "/proc/" + str(ready["gateway_pid"]) + "/ns/net"
        )
        destinations = [
            ("ipv4-unapproved", "192.0.2.3", 44443),
            ("ipv4-alternate-port", "192.0.2.2", 44444),
            ("ipv4-proxy", "192.0.2.3", 44445),
        ]
        if ready["ipv6_enabled"]:
            destinations.extend(
                [
                    ("ipv6-unapproved", "fd42:6f72:696f::3", 44443),
                    ("ipv6-alternate-port", "fd42:6f72:696f::2", 44444),
                    ("ipv6-proxy", "fd42:6f72:696f::3", 44445),
                    ("ipv4-mapped-ipv6", "::ffff:192.0.2.3", 44443),
                ]
            )
            destinations.append(
                ("ipv4-other-family", "192.0.2.2", 44443)
                if value["case"] == "ipv6"
                else ("ipv6-other-family", "fd42:6f72:696f::2", 44443)
            )
        for _, host, port in destinations:
            connect(ready["control_pid"], host, port)
        connect(ready["control_pid"], value["host"], 44443)
        checks["all_unapproved_controls_demonstrably_reachable"] = True
        network = "--net=/proc/" + str(ready["gateway_pid"]) + "/ns/net"
        user_namespace = "--user=/proc/" + str(ready["control_pid"]) + "/ns/user"

        def counters():
            result = subprocess.run(
                [
                    "nsenter",
                    "--preserve-credentials",
                    user_namespace,
                    network,
                    "nft",
                    "-j",
                    "list",
                    "table",
                    "inet",
                    "orion",
                ],
                check=True,
                env={"PATH": LAB_PATH, "LANG": "C.UTF-8"},
                capture_output=True,
                text=True,
                timeout=5,
            )
            return {
                item["counter"]["name"]: item["counter"]["packets"]
                for item in json.loads(result.stdout)["nftables"]
                if "counter" in item
            }

        named_denials = {}
        for name, host, port in destinations:
            before_counter = counters()
            probe = subprocess.run(
                [
                    "nsenter",
                    "--preserve-credentials",
                    user_namespace,
                    network,
                    "/usr/bin/setpriv",
                    "--bounding-set=-all",
                    "--inh-caps=-all",
                    "--ambient-caps=-all",
                    sys.executable,
                    "-I",
                    "-c",
                    (
                        "import socket,sys; from pathlib import Path; "
                        "status=dict(l.split(':',1) for l in Path('/proc/self/status').read_text().splitlines() "
                        "if ':' in l); assert int(status['CapEff'],16)==0; "
                        "s=socket.socket(socket.AF_INET6 if ':' in sys.argv[1] "
                        "else socket.AF_INET); s.settimeout(1); "
                        "s.connect((sys.argv[1],int(sys.argv[2])))"
                    ),
                    host,
                    str(port),
                ],
                check=False,
                capture_output=True,
                text=True,
                timeout=5,
            )
            named_denials[name] = counters()[counter_for(name)] - before_counter[counter_for(name)]
            # A single denied connect may retransmit its SYN. Packet counters
            # witness the kernel boundary; they are not application-attempt counts.
            checks["kernel_denies_" + name] = probe.returncode != 0 and named_denials[name] >= 1

        def status(operation):
            return rpc(endpoints["authorization"], "owner", owner_key, "status", operation)

        def control(operation, action):
            current = status(operation)
            payload = {"control": action, "nonce": current["nonce"], "head": current["head"]}
            return rpc(
                endpoints["authorization"],
                "owner",
                owner_key,
                "control",
                {
                    "operation": operation,
                    "message": dict(
                        payload, mac=authenticate(value["issuer"].encode(), "control", payload)
                    ),
                },
            )

        def message(operation, request_id):
            config = next(c for c in value["configs"] if c["operation"] == operation)
            grant = config["grant"]
            if operation == "metadata":
                request = grant["request"]
            else:
                window = grant["window"]
                request = {
                    name: window[name]
                    for name in (
                        "tenant_id",
                        "company",
                        "resource",
                        "fields",
                        "date_field",
                        "start",
                        "end",
                    )
                }
                request.update(source_id=grant["source_id"], max_records=grant["max_records"])
            return {
                "operation": operation,
                "request_id": request_id,
                "request": request,
                "grant_token": authenticate(value["issuer"].encode(), "read_grant", digest(config)),
            }

        def acquire(operation, request_id):
            try:
                return rpc(
                    endpoints["acquisition"],
                    "reasoner",
                    reader_key,
                    "read",
                    message(operation, request_id),
                )
            except JournalDenied:
                return {"status": "denied"}

        checks["metadata_first_denies_record_before_source_io"] = acquire(
            "read", "before-discovery"
        )["status"] == "denied" and command(server, {"command": "stats"}) == {
            "metadata": 0,
            "read": 0,
        }
        assert control("metadata", "arm")["status"] == "arm"
        attack = message("metadata", "wrong-scope")
        attack["request"] = dict(attack["request"], tenant_id="synthetic-unapproved")
        denied = rpc(endpoints["acquisition"], "reasoner", reader_key, "read", attack)
        checks["unauthorized_denied_before_source_io"] = (
            denied["status"] == "denied" and command(server, {"command": "stats"})["metadata"] == 0
        )
        metadata = acquire("metadata", "discovery-1")
        assert metadata["status"] == "admitted", metadata
        checks["metadata_governed_separate_grant"] = command(server, {"command": "stats"}) == {
            "metadata": 2,
            "read": 0,
        }
        checks["records_unarmed_after_discovery"] = (
            acquire("read", "unarmed-read")["status"] == "denied"
        )
        assert control("read", "arm")["status"] == "arm"
        if value["case"] == "redirect":
            before_connection = command(server, {"command": "connections"})
            before_kernel = counters()
            denied = acquire("read", "redirect-read")
            persisted = rpc(
                endpoints["evidence"],
                "owner",
                owner_key,
                "load",
                {"binding": digest(value["configs"][1]), "arguments": {}},
            )
            checks["native_gateway_redirect_denies_before_admission"] = (
                denied["status"] == "denied"
                and persisted["observations"] == []
                and persisted["checkpoints"] == []
                and command(server, {"command": "stats"}) == {"metadata": 2, "read": 1}
            )
            checks["native_gateway_does_not_follow_redirect"] = (
                command(server, {"command": "connections"}) == {
                    "canary": before_connection["canary"],
                    "approved": before_connection["approved"] + 1,
                }
                and counters()[counter_for("ipv4-unapproved")]
                == before_kernel[counter_for("ipv4-unapproved")]
            )
            return runtime_report(value, checks, identity, ready, named_denials)
        isolated = command(runtime, {"command": "read", "message": message("read", "read-1")})
        admitted = isolated["response"]
        checks["actual_reasoning_process_isolated"] = bool(isolated["reasoner_checks"]) and all(
            isolated["reasoner_checks"].values()
        )
        assert admitted["status"] == "admitted", admitted
        observations = observations_from(admitted["observations"])
        checks["canonical_provenance_unknown"] = (
            len(observations) == 1
            and admitted["interpretation"] == "UNKNOWN"
            and observations[0].evidence.payload["provenance"]["authorization_id"]
            == value["configs"][1]["grant"]["authorization_id"]
        )
        checks["only_authorized_ordinary_bearer_source_io"] = command(
            server, {"command": "stats"}
        ) == {"metadata": 2, "read": 1}
        checks["no_keys_in_admitted_output"] = all(
            content not in json.dumps(admitted) for content in private.values()
        )
        record_binding = digest(value["configs"][1])

        def archive(action):
            return rpc(
                endpoints["evidence"],
                "owner",
                owner_key,
                action,
                {"binding": record_binding, "arguments": {}},
            )

        loaded = archive("load")
        checks["protected_admitted_evidence_load_exact"] = (
            loaded["observations"] == admitted["observations"]
            and len(loaded["checkpoints"]) == 1
            and loaded["checkpoints"][0]["references"][0]["revision"] == 1
            and loaded["authority_restored"] is False
        )

        if value["case"] == "security":
            before_io = command(server, {"command": "stats"})
            # Allow positive-control TLS failures to finish before the baseline.
            time.sleep(0.1)
            before_connections = command(server, {"command": "connections"})
            attacks = command(runtime, {"command": "attack", "message": message("read", "attacker-forgery"),
                                        "ipv6": ready["ipv6_enabled"]})
            checks.update(attacks["checks"])
            checks["compromise_no_source_io_or_connections"] = (
                command(server, {"command": "stats"}) == before_io
                and command(server, {"command": "connections"}) == before_connections
            )
            checks["compromise_archive_and_audit_history_intact"] = (
                archive("load") == loaded
                and attacks["audit_prefix_intact"] is True
            )
            checks["compromise_supervised_runtime_remains_healthy"] = (
                command(runtime, {"command": "health"})["status"] == "healthy"
            )
            return runtime_report(value, checks, identity, ready, named_denials)

        def report():
            return runtime_report(value, checks, identity, ready, named_denials)

        if value["case"] == "revision":
            time.sleep(1.05)
            second = acquire("read", "read-2")
            assert second["status"] == "admitted", second
            history = archive("load")
            references = [e["references"][0] for e in history["checkpoints"]]
            checks["revision_history_preserves_scoped_provenance"] = (
                [r["revision"] for r in references] == [1, 2]
                and len({r["provenance"]["source_record_id"] for r in references}) == 1
                and all(r["tenant_id"] == "t_01" for r in references)
                and all(e["company"] == "c_01" for e in history["checkpoints"])
                and len(history["observations"]) == 2
            )
            assert command(runtime, {"command": "restart"})["status"] == "unarmed"
            checks["restart_retains_revision_index_and_budget"] = (
                archive("load")["checkpoints"] == history["checkpoints"]
                and status("read")["budget"]["attempts"] == 2
                and acquire("read", "revision-restart")["status"] == "denied"
            )
            assert control("metadata", "arm")["status"] == "arm"
            before_replay = command(server, {"command": "stats"})
            checks["restart_replay_denies_before_source_io"] = (
                acquire("metadata", "discovery-1")["status"] == "denied"
                and command(server, {"command": "stats"}) == before_replay
            )
            checks["new_authorized_discovery_after_restart"] = (
                acquire("metadata", "discovery-2")["status"] == "admitted"
            )
            return report()
        if value["case"] == "retention":
            time.sleep(1.2)
            health = command(runtime, {"command": "health"})
            expired = archive("load")
            checks["runtime_retention_erases_payload_keeps_provenance_index"] = (
                health["status"] == "healthy"
                and expired["observations"] == []
                and len(expired["checkpoints"]) == 1
                and expired["checkpoints"][0]["retained"] is False
                and expired["checkpoints"][0]["references"]
                == loaded["checkpoints"][0]["references"]
                and expired["authority_restored"] is False
            )
            return report()
        if value["case"] == "revoke":
            revoked = command(runtime, {"command": "revoke"})
            checks["revocation_durable_independent_cutoff"] = (
                revoked["status"] == "revoked"
                and revoked["durable_controls"] == {"metadata": "revoke", "read": "revoke"}
                and revoked["kernel_egress_removed"] is True
                and revoked["acquisition_terminated"] is True
            )
            checks["revocation_zero_further_source_io"] = command(server, {"command": "stats"}) == {
                "metadata": 2,
                "read": 1,
            }
            restarted_revoked = command(runtime, {"command": "restart"})
            checks["revocation_restart_does_not_restore_authority"] = (
                restarted_revoked["status"] in ("blocked", "unarmed")
                and command(runtime, {"command": "arm", "operation": "metadata"})["status"]
                == "denied"
                and command(server, {"command": "stats"}) == {"metadata": 2, "read": 1}
            )
            return report()
        if value["case"] not in ("full", "ipv6"):
            descriptor = os.open("/proc/" + str(ready["gateway_pid"]) + "/ns/net", os.O_RDONLY)
            try:
                os.kill(ready["service_pids"][value["case"]], signal.SIGKILL)
                deadline = time.monotonic() + 3
                health = command(runtime, {"command": "health"})
                while time.monotonic() < deadline and (
                    Path("/proc/" + str(ready["gateway_pid"])).exists()
                    or Path("/proc/" + str(ready["service_pids"]["acquisition"])).exists()
                ):
                    time.sleep(0.1)
                    health = command(runtime, {"command": "health"})
                policy = subprocess.run(
                    [
                        "nsenter",
                        "--preserve-credentials",
                        user_namespace,
                        "--net=/proc/self/fd/" + str(descriptor),
                        "nft",
                        "-j",
                        "list",
                        "chain",
                        "inet",
                        "orion",
                        "egress",
                    ],
                    pass_fds=(descriptor,),
                    env={"PATH": LAB_PATH},
                    check=True,
                    capture_output=True,
                    text=True,
                    timeout=5,
                )
                items = json.loads(policy.stdout)["nftables"]
                checks["independent_custody_loss_denies_further_acquisition"] = (
                    health["status"] == "blocked"
                    and not Path("/proc/" + str(ready["gateway_pid"])).exists()
                    and not Path("/proc/" + str(ready["service_pids"]["acquisition"])).exists()
                )
                checks["spontaneous_custody_loss_kernel_policy_default_drop"] = any(
                    item.get("chain", {}).get("policy") == "drop" for item in items
                ) and not any("rule" in item for item in items)
            finally:
                os.close(descriptor)
            checks["custody_loss_zero_further_source_io"] = command(
                server, {"command": "stats"}
            ) == {"metadata": 2, "read": 1}
            checks["custody_loss_health_fails_closed"] = (
                command(runtime, {"command": "health"})["status"] == "blocked"
            )
            return report()
        health = command(runtime, {"command": "health"})
        checks["bounded_healthy_supervision"] = health["status"] == "healthy"
        before = status("read")["budget"]
        restarted = command(runtime, {"command": "restart"})
        assert restarted["status"] == "unarmed", restarted
        checks["restart_does_not_restore_authority"] = (
            acquire("read", "restart-unarmed")["status"] == "denied"
        )
        checks["restart_retains_budget"] = (
            status("read")["budget"]["attempts"] == before["attempts"] == 1
        )
        checks["restart_retains_protected_checkpoint"] = (
            archive("load")["observations"] == admitted["observations"]
            and archive("inspect")["checkpoints"] == loaded["checkpoints"]
        )
        assert control("metadata", "arm")["status"] == "arm"
        before_replay = command(server, {"command": "stats"})
        checks["restart_replay_denies_before_source_io"] = (
            acquire("metadata", "discovery-1")["status"] == "denied"
            and command(server, {"command": "stats"}) == before_replay
        )
        assert acquire("metadata", "discovery-2")["status"] == "admitted"
        assert control("read", "arm")["status"] == "arm"
        checks["budget_exhaustion_no_source_io"] = (
            acquire("read", "exhausted")["status"] == "denied"
        )
        stopped = command(runtime, {"command": "stop"})
        checks["emergency_stop_durable"] = stopped["status"] == "stopped" and stopped[
            "durable_controls"
        ] == {"metadata": "stop", "read": "stop"}
        checks["emergency_stop_kernel_cutoff_and_termination"] = (
            stopped["kernel_egress_removed"] is True and stopped["acquisition_terminated"] is True
        )
        checks["source_read_budget_never_refunded"] = (
            command(server, {"command": "stats"})["read"] == 1
        )
        post_stop = command(runtime, {"command": "restart"})
        post_stop_arm = command(runtime, {"command": "arm", "operation": "metadata"})
        checks["durable_stop_restart_does_not_restore_authority"] = (
            post_stop["status"] in ("blocked", "unarmed")
            and post_stop_arm["status"] == "denied"
            and command(server, {"command": "stats"})["read"] == 1
        )
        return report()
    finally:
        for process in (server, runtime):
            if process is not None:
                if process.poll() is None:
                    try:
                        command(process, {"command": "shutdown"})
                    except (OSError, ValueError):
                        pass
                    try:
                        process.wait(timeout=5)
                    except subprocess.TimeoutExpired:
                        process.kill()
                process.wait(timeout=5)


if __name__ == "__main__":
    try:
        payload = read_frame(sys.stdin)
        if sys.argv[1:] == ["--source"]:
            source(payload)
        elif not sys.argv[1:]:
            raise SystemExit(controller(payload))
        else:
            raise ValueError("fixed driver mode required")
    except Exception as error:  # noqa: BLE001 - fixed denial never exposes private values
        # Do not expose source credentials or key-bearing tracebacks on failure.
        emit(
            {
                "status": "FAIL",
                "reason": "packaged_deployment_witness_failed",
                "trace": [
                    {"function": frame.name, "line": frame.lineno}
                    for frame in traceback.extract_tb(error.__traceback__)
                ],
                "LIVE_PILOT_READY": False,
            }
        )
        raise SystemExit(1) from None
