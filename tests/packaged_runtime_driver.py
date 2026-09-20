"""External deployment acceptance driver; the HTTPS source uses only stdlib.

The installed runtime never imports this file. Run its controller from a clean
venv inside a disposable mapped user/network namespace, with no PYTHONPATH.
"""

import hashlib
import http.server
import json
import os
import secrets
import shutil
import signal
import socket
import sqlite3
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
            "deployment_profile_sha256": ready.get("deployment_profile_sha256"),
            "source_unmodified": True,
            "clean_installed_artifact": True,
            "ipv6_enabled": ready["ipv6_enabled"],
            "named_kernel_rejects": named_denials,
            "semantic_result": value.get("semantic_result"),
            "rollback_result": value.get("rollback_result"),
            "witness": ready.get("health", {}).get("witness"),
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
    authentication = {"obsolete_credential_rejections": 0}
    native = value["case"] == "erpnext_candidate"
    if native:
        bodies = {path: body.encode() for path, body in value["native_bodies"].items()}
        operations = {
            path: ("read" if path.startswith("/api/resource/Sales%20Invoice?") else "metadata")
            for path in bodies
        }
    else:
        bodies = {
            "/metadata": value["bodies"]["metadata"].encode(),
            "/records": value["bodies"]["read"].encode(),
        }
        operations = {"/metadata": "metadata", "/records": "read"}
    for index in range(8):
        operation = "instrument_" + str(index)
        if operation in value["bodies"]:
            path = "/instrument/" + str(index)
            bodies[path] = value["bodies"][operation].encode()
            operations[path] = operation
            io[operation] = 0
    paths = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            expected_authorization = ("token " if native else "Bearer ") + credential
            if self.headers.get("Authorization") != expected_authorization:
                with lock:
                    authentication["obsolete_credential_rejections"] += 1
                self.send_response(401)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            with lock:
                paths.append(self.path)
            if self.path not in bodies:
                self.send_response(404)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            body = bodies[self.path]
            with lock:
                operation = operations[self.path]
                io[operation] += 1
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
            elif command == {"command": "credential-rejections"}:
                with lock:
                    emit(dict(authentication))
            elif command == {"command": "paths"}:
                with lock:
                    emit({"paths": list(paths)})
            elif (
                type(command) is dict
                and set(command) == {"command", "credential"}
                and command["command"] == "rotate-credential"
                and type(command["credential"]) is str
            ):
                with lock:
                    credential = command["credential"]
                emit({"status": "rotated"})
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


def probe_source_credential(pid, control_pid, host, certificate, credential):
    """Issue one ordinary synthetic HTTPS request without exposing the credential in argv."""
    destination = "[" + host + "]" if ":" in host else host
    return subprocess.run(
        [
            "nsenter",
            "--preserve-credentials",
            "--user=/proc/" + str(control_pid) + "/ns/user",
            "--net=/proc/" + str(pid) + "/ns/net",
            "/usr/bin/curl",
            "--disable",
            "--config",
            "-",
            "--silent",
            "--output",
            "/dev/null",
            "--write-out",
            "%{http_code}",
            "--proto",
            "=https",
            "--max-redirs",
            "0",
            "--noproxy",
            "*",
            "--proxy",
            "",
            "--cacert",
            certificate,
            "https://" + destination + ":44443/records",
        ],
        input='header = "Authorization: Bearer ' + credential + '"\n',
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
        env={"PATH": os.defpath},
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
    keys, state, witness = (
        root / "runtime-keys",
        root / "runtime-state",
        root / "runtime-witness",
    )
    keys.mkdir(mode=0o700)
    state.mkdir(mode=0o700)
    witness.mkdir(mode=0o700)
    private = {
        "issuer": value["issuer"],
        "worker-secret": value["worker_secret"],
        "source-credential": value["source_credential"],
        "audit-signing": secrets.token_hex(32),
        "evidence-signing": secrets.token_hex(32),
        "witness-signing": secrets.token_hex(32),
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
        "version": 4 if value["case"] == "erpnext_candidate" else 1,
        "mode": (
            "candidate_erpnext_read_only"
            if value["case"] == "erpnext_candidate"
            else "synthetic_read_only"
        ),
        "configs": value["configs"],
        "state_directory": str(state),
        "keys_directory": str(keys),
        "witness_directory": str(witness),
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
    semantic_case = value["case"].startswith("semantic") or value[
        "case"
    ] == "witness_evidence_rollback"
    independent_case = value["case"].startswith("independent_")
    if independent_case:
        manifest.update(version=3, semantic=value["semantic"])
        if value["case"] == "independent_expired":
            manifest["policy"]["ttl_seconds"] = 45
    if semantic_case:
        from orion.pilot.semantic_runtime import semantic_policy_sha256
        from orion.understanding.semantic_study import SEMANTIC_EVALUATOR_VERSION

        manifest.update(version=2, semantic={
            "version": 1, "study_id": "synthetic-installed-study",
            "evaluator_version": SEMANTIC_EVALUATOR_VERSION,
            "policy_sha256": semantic_policy_sha256(),
        })
        manifest["policy"]["ttl_seconds"] = 8 if value["case"] == "semantic_retention" else 3600
    from orion.pilot.deployment_profile import profile_for_manifest, profile_sha256
    from orion.pilot.progress_witness import deployment_identity_for_manifest

    manifest_path = root / "manifest.json"
    manifest["deployment_identity"] = deployment_identity_for_manifest(manifest, identity)
    manifest["deployment_profile"] = profile_for_manifest(manifest, identity, manifest_path)
    manifest["deployment_profile_sha256"] = profile_sha256(manifest["deployment_profile"])
    manifest_path.write_text(json.dumps(manifest))
    manifest_path.chmod(0o600)
    runtime_entrypoint = [sys.executable, "-I", "-m", "orion.pilot.deployment"]
    launch_checks = {}
    if value["case"] == "security":
        copied_manifest = root / "copied-manifest.json"
        copied_manifest.write_bytes(manifest_path.read_bytes())
        copied_manifest.chmod(0o600)
        unsupported = {
            "console_script_mutating_launch_denied": (
                [
                    str(Path(sys.executable).with_name("orion-runtime")),
                    "--enroll-witness",
                    str(manifest_path),
                ],
                "/",
                {"PATH": os.defpath},
            ),
            "nonisolated_module_launch_denied": (
                [
                    sys.executable,
                    "-m",
                    "orion.pilot.deployment",
                    "--enroll-witness",
                    str(manifest_path),
                ],
                "/",
                {"PATH": os.defpath},
            ),
            "private_supervisor_without_descriptors_denied": (
                [
                    *runtime_entrypoint,
                    "--private-supervisor",
                    "--serve",
                    str(manifest_path),
                ],
                "/",
                {"PATH": os.defpath},
            ),
            "extra_launch_arguments_denied": (
                [
                    *runtime_entrypoint,
                    "--artifact",
                    "--enroll-witness",
                    str(manifest_path),
                ],
                "/",
                {"PATH": os.defpath},
            ),
            "wrong_working_directory_launch_denied": (
                [*runtime_entrypoint, "--enroll-witness", str(manifest_path)],
                str(root),
                {"PATH": os.defpath},
            ),
            "copied_manifest_launch_denied": (
                [*runtime_entrypoint, "--enroll-witness", str(copied_manifest)],
                "/",
                {"PATH": os.defpath},
            ),
            "python_environment_launch_denied": (
                [*runtime_entrypoint, "--enroll-witness", str(manifest_path)],
                "/",
                {"PATH": os.defpath, "PYTHONPATH": "/unauthorized/source"},
            ),
        }
        for name, (launch_command, working_directory, environment) in unsupported.items():
            denied = subprocess.run(
                launch_command,
                cwd=working_directory,
                env=environment,
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
            report = json.loads(denied.stdout)
            launch_checks[name] = denied.returncode == 2 and report["status"] == "BLOCKED"
        launch_checks["denied_launches_leave_no_witness_or_enrollment"] = not (
            (state / "witness-enrollment").exists()
            or (witness / "progress-witness.db").exists()
        )
    enrolled = subprocess.run(
        [*runtime_entrypoint, "--enroll-witness", str(manifest_path)],
        cwd="/",
        capture_output=True,
        text=True,
        env={"PATH": os.defpath},
        timeout=15,
        check=False,
    )
    enrollment = json.loads(enrolled.stdout)
    assert enrolled.returncode == 0 and enrollment["status"] == "enrolled", enrollment
    assert enrollment["whole_host_rollback_protection"] is False
    misplaced_denied = None
    if value["case"] == "security":
        misplaced = Path(sys.prefix) / ("invalid-custody-" + root.name)
        misplaced.mkdir(mode=0o700)
        for name in ("keys", "state", "witness"):
            (misplaced / name).mkdir(mode=0o700)
        invalid_manifest = dict(manifest, keys_directory=str(misplaced / "keys"),
                                state_directory=str(misplaced / "state"),
                                witness_directory=str(misplaced / "witness"))
        invalid_manifest["deployment_identity"] = deployment_identity_for_manifest(
            invalid_manifest, identity
        )
        invalid_path = root / "invalid-placement.json"
        invalid_manifest["deployment_profile"] = profile_for_manifest(
            invalid_manifest, identity, invalid_path
        )
        invalid_manifest["deployment_profile_sha256"] = profile_sha256(
            invalid_manifest["deployment_profile"]
        )
        invalid_path.write_text(json.dumps(invalid_manifest))
        invalid_path.chmod(0o600)
        rejected = subprocess.run(
            [sys.executable, "-I", "-m", "orion.pilot.deployment", "--serve", str(invalid_path)],
            cwd="/", capture_output=True, text=True, env={"PATH": os.defpath},
            timeout=10, check=False,
        )
        placement_result = json.loads(rejected.stdout)
        misplaced_denied = (
            rejected.returncode == 2
            and placement_result["status"] == "BLOCKED"
            and placement_result.get("failure_type") == "KernelUnavailable"
            and placement_result.get("failure_boundary") == "validate_protected_paths"
        )
    baseline_fds = []
    runtime_command = [*runtime_entrypoint, "--serve", str(manifest_path)]
    if semantic_case or independent_case:
        for field, invalid in (("evaluator_version", "unreviewed"),
                               ("policy_sha256", "0" * 64)):
            invalid_manifest = dict(manifest, semantic=dict(manifest["semantic"], **{field: invalid}))
            invalid_path = root / ("invalid-semantic-" + field + ".json")
            invalid_path.write_text(json.dumps(invalid_manifest))
            invalid_path.chmod(0o600)
            rejected = subprocess.run(
                [*runtime_entrypoint, "--serve", str(invalid_path)],
                cwd="/", capture_output=True, text=True, env={"PATH": os.defpath},
                timeout=10, check=False,
            )
            rejection = json.loads(rejected.stdout)
            assert rejected.returncode == 2 and rejection["status"] == "BLOCKED"
            assert rejection["failure_boundary"] == "validate_semantic_config"
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
        cwd="/",
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
                    "case": value["case"],
                    "bodies": value["bodies"],
                    "native_bodies": value["native_bodies"],
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
        checks.update(launch_checks)
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

        def audit_records(operation):
            with sqlite3.connect(state / "audit" / operation / "broker.db") as database:
                return [
                    json.loads(row[0])
                    for row in database.execute("SELECT body FROM events ORDER BY sequence")
                ]

        def denial_boundary(operation):
            records = audit_records(operation)
            reason = next(
                (
                    record.get("references", {}).get("reason")
                    for record in reversed(records)
                    if record.get("event") == "broker_denied"
                ),
                None,
            )
            return next(
                (
                    phase
                    for phase in (
                        "request_validation",
                        "grant_authentication",
                        "scope_authorization",
                        "resource_reservation",
                        "worker_acquisition",
                        "admission",
                    )
                    if digest(phase) == reason
                ),
                None,
            )

        def witness_rows():
            with sqlite3.connect(witness / "progress-witness.db") as database:
                return [
                    json.loads(row[0])
                    for row in database.execute("SELECT body FROM streams ORDER BY identity")
                ]

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

        def expected_source_io(metadata_count, record_count):
            counts = {"metadata": metadata_count, "read": record_count}
            if independent_case:
                counts.update({config["operation"]: 0 for config in value["configs"][2:]})
            return counts

        checks["metadata_first_denies_record_before_source_io"] = acquire(
            "read", "before-discovery"
        )["status"] == "denied" and command(server, {"command": "stats"}) == expected_source_io(0, 0)
        assert control("metadata", "arm")["status"] == "arm"
        attack = message("metadata", "wrong-scope")
        attack["request"] = dict(attack["request"], tenant_id="synthetic-unapproved")
        denied = rpc(endpoints["acquisition"], "reasoner", reader_key, "read", attack)
        checks["unauthorized_denied_before_source_io"] = (
            denied["status"] == "denied" and command(server, {"command": "stats"})["metadata"] == 0
        )
        metadata = acquire("metadata", "discovery-1")
        if value["case"] == "erpnext_candidate" and metadata["status"] != "admitted":
            emit(
                {
                    "status": "FAIL",
                    "reason": "candidate_metadata_denied",
                    "failure_boundary": denial_boundary("metadata"),
                    "source_io": command(server, {"command": "stats"}),
                    "source_paths": command(server, {"command": "paths"}),
                    "LIVE_PILOT_READY": False,
                }
            )
            return 1
        assert metadata["status"] == "admitted", metadata
        checks["metadata_governed_separate_grant"] = command(server, {"command": "stats"}) == expected_source_io(2, 0)
        if value["case"] == "rotation":
            source_credential = keys / "source-credential"
            staged = keys / "source-credential.next"
            staged.write_text(value["replacement_source_credential"])
            staged.chmod(0o640)
            profile_before = manifest["deployment_profile_sha256"]
            read_binding = digest(value["configs"][1])
            checks["initial_authorized_acquisition_uses_installed_runtime"] = (
                status("metadata")["budget"]["attempts"] == 2
                and command(server, {"command": "stats"}) == expected_source_io(2, 0)
            )
            checks["interrupted_stage_is_not_installed_or_consumed"] = (
                staged.exists()
                and source_credential.read_text() == value["source_credential"]
                and manifest["deployment_profile_sha256"] == profile_before
                and command(server, {"command": "stats"}) == expected_source_io(2, 0)
            )
            audit_before_restart = audit_records("read")
            budget_before_restart = status("read")["budget"]
            cutoff = command(runtime, {"command": "cutoff"})
            staged.chmod(0o600)
            staged.replace(source_credential)
            assert command(
                server,
                {
                    "command": "rotate-credential",
                    "credential": value["replacement_source_credential"],
                },
            ) == {"status": "rotated"}
            checks["controlled_atomic_replacement_preserves_reference_profile"] = (
                cutoff["status"] == "blocked"
                and cutoff["kernel_egress_removed"] is True
                and cutoff["gateway_terminated"] is True
                and cutoff["acquisition_terminated"] is True
                and source_credential.lstat().st_mode & 0o777 == 0o600
                and not staged.exists()
                and manifest["deployment_profile_sha256"] == profile_before
                and profile_before == ready["deployment_profile_sha256"]
                and all(
                    secret not in json.dumps(manifest["deployment_profile"])
                    for secret in (
                        value["source_credential"],
                        value["replacement_source_credential"],
                    )
                )
            )
            restarted = command(runtime, {"command": "restart"})
            budget_after_restart = status("read")["budget"]
            source_io_after_restart = command(server, {"command": "stats"})
            unarmed = acquire("read", "replacement-unarmed")
            checks["replacement_requires_restart_and_fresh_authorization"] = (
                restarted["status"] == "unarmed"
                and restarted["execution_allowed"] is False
                and restarted["deployment_profile_sha256"] == profile_before
                and unarmed["status"] == "denied"
                and budget_before_restart["attempts"] == 0
                and status("read")["budget"]["attempts"] == 0
                and source_io_after_restart == expected_source_io(2, 0)
                and command(server, {"command": "stats"}) == source_io_after_restart
            )
            assert control("metadata", "arm")["status"] == "arm"
            replacement_metadata = acquire("metadata", "rotation-discovery-2")
            checks["replacement_requires_fresh_scoped_metadata_discovery"] = (
                replacement_metadata["status"] == "admitted"
                and status("metadata")["budget"]["attempts"] == 4
                and command(server, {"command": "stats"}) == expected_source_io(4, 0)
            )
            assert control("read", "arm")["status"] == "arm"
            replacement = acquire("read", "replacement-authorized")
            replacement_budget = status("read")["budget"]
            audit_after_replacement = audit_records("read")
            checks["replacement_credential_authorized_after_service_restart"] = (
                replacement["status"] == "admitted"
                and command(server, {"command": "stats"}) == expected_source_io(4, 1)
                and command(server, {"command": "credential-rejections"})
                == {"obsolete_credential_rejections": 0}
            )
            checks["rotation_preserves_scope_audit_and_consumed_budget"] = (
                budget_after_restart["attempts"] == 0
                and budget_after_restart["failures"] == 0
                and replacement_budget["attempts"] == 1
                and replacement_budget["failures"] == 0
                and replacement_budget["stopped"] is False
                and audit_after_replacement[: len(audit_before_restart)] == audit_before_restart
                and [record["sequence"] for record in audit_after_replacement]
                == list(range(1, len(audit_after_replacement) + 1))
                and all(record["binding"] == read_binding for record in audit_after_replacement)
                and all(
                    secret not in json.dumps(audit_after_replacement)
                    for secret in (
                        value["source_credential"],
                        value["replacement_source_credential"],
                    )
                )
            )
            obsolete = probe_source_credential(
                ready["source_pid"],
                ready["control_pid"],
                value["host"],
                value["certificate"],
                value["source_credential"],
            )
            checks["source_rejects_obsolete_credential_without_runtime_state_change"] = (
                obsolete.returncode == 0
                and obsolete.stdout == "401"
                and command(server, {"command": "credential-rejections"})
                == {"obsolete_credential_rejections": 1}
                and command(server, {"command": "stats"}) == expected_source_io(4, 1)
                and status("read")["budget"] == replacement_budget
                and audit_records("read") == audit_after_replacement
            )
            before_stop_io = command(server, {"command": "stats"})
            stopped = command(runtime, {"command": "stop"})
            stopped_restart = command(runtime, {"command": "restart"})
            stopped_budget = status("read")["budget"]
            checks["rotation_preserves_durable_stop_and_cutoff"] = (
                stopped["status"] == "stopped"
                and stopped["durable_controls"] == {"metadata": "stop", "read": "stop"}
                and stopped["kernel_egress_removed"] is True
                and stopped["acquisition_terminated"] is True
                and stopped_restart["status"] in ("blocked", "unarmed")
                and command(runtime, {"command": "arm", "operation": "read"})["status"]
                == "denied"
                and stopped_budget["attempts"] == 1
                and stopped_budget["failures"] == 0
                and stopped_budget["stopped"] is True
                and command(server, {"command": "stats"}) == before_stop_io
            )
            return runtime_report(value, checks, identity, ready, named_denials)
        checks["records_unarmed_after_discovery"] = (
            acquire("read", "unarmed-read")["status"] == "denied"
        )
        assert control("read", "arm")["status"] == "arm"
        evidence_snapshot = None
        if value["case"] == "witness_evidence_rollback":
            evidence_snapshot = (
                root / "old-evidence.db",
                root / "old-accepted-evidence-head",
            )
            shutil.copyfile(state / "evidence/evidence.db", evidence_snapshot[0])
            shutil.copyfile(
                state / "evidence/accepted-evidence-head", evidence_snapshot[1]
            )
        if value["case"] == "semantic_failure":
            with sqlite3.connect(state / "evidence/evidence.db") as database:
                database.execute("""
                    CREATE TRIGGER fail_semantic AFTER INSERT ON orion_semantic_checkpoints
                    BEGIN SELECT RAISE(ABORT, 'synthetic storage failure'); END
                """)
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
        if value["case"] == "erpnext_candidate" and admitted["status"] != "admitted":
            emit(
                {
                    "status": "FAIL",
                    "reason": "candidate_records_denied",
                    "failure_boundary": denial_boundary("read"),
                    "source_io": command(server, {"command": "stats"}),
                    "source_paths": command(server, {"command": "paths"}),
                    "LIVE_PILOT_READY": False,
                }
            )
            return 1
        assert admitted["status"] == "admitted", admitted
        observations = observations_from(admitted["observations"])
        checks["canonical_provenance_unknown"] = (
            len(observations) == (2 if independent_case else 1)
            and admitted["interpretation"] == "UNKNOWN"
            and observations[0].evidence.payload["provenance"]["authorization_id"]
            == value["configs"][1]["grant"]["authorization_id"]
        )
        if value["case"] == "erpnext_candidate":
            checks["native_erpnext_exact_gets_derived_from_grants"] = command(
                server, {"command": "paths"}
            ) == {"paths": list(value["native_bodies"])}
            checks["native_erpnext_api_provenance_admitted"] = (
                observations[0].evidence.kind.value == "api"
                and observations[0].evidence.source
                == "erpnext-historical-sample-read-only"
            )
        checks["only_authorized_ordinary_bearer_source_io"] = command(
            server, {"command": "stats"}
        ) == expected_source_io(2, 1)
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
        witnessed_progress = witness_rows()
        checks["normal_audit_and_evidence_progress_witnessed"] = (
            len(witnessed_progress) == len(value["configs"]) + 1
            and all(row["sequence"] > 1 for row in witnessed_progress)
            and all(
                set(row)
                == {
                    "identity",
                    "kind",
                    "scope_binding",
                    "sequence",
                    "head",
                    "predecessor_sequence",
                    "predecessor_head",
                }
                for row in witnessed_progress
            )
        )

        if value["case"] == "witness_audit_rollback":
            audit_directory = state / "audit/read"
            old_database, old_tip = root / "old-audit.db", root / "old-accepted-head"
            shutil.copyfile(audit_directory / "broker.db", old_database)
            shutil.copyfile(audit_directory / "accepted-head", old_tip)
            before_io = command(server, {"command": "stats"})
            stopped = command(runtime, {"command": "stop"})
            witness_path = witness / "progress-witness.db"
            witnessed_sha256 = hashlib.sha256(witness_path.read_bytes()).hexdigest()
            os.kill(ready["service_pids"]["audit"], signal.SIGKILL)
            deadline = time.monotonic() + 3
            while (
                time.monotonic() < deadline
                and Path("/proc/" + str(ready["service_pids"]["audit"])).exists()
            ):
                time.sleep(0.05)
            shutil.copyfile(old_database, audit_directory / "broker.db")
            shutil.copyfile(old_tip, audit_directory / "accepted-head")
            preserved_sha256 = hashlib.sha256(witness_path.read_bytes()).hexdigest()
            rejected = command(runtime, {"command": "restart"})
            checks["audit_database_plus_tip_rollback_rejected_by_preserved_witness"] = (
                stopped["status"] == "stopped"
                and rejected["status"] == "blocked"
                and witnessed_sha256 == preserved_sha256
                and command(runtime, {"command": "health"})["status"] == "blocked"
            )
            checks["audit_rollback_denied_before_source_io_rearm_or_budget_reset"] = (
                command(server, {"command": "stats"}) == before_io
                and command(runtime, {"command": "arm", "operation": "read"})[
                    "status"
                ]
                == "denied"
            )
            checks["stopped_state_rollback_does_not_restore_authority"] = (
                stopped["durable_controls"] == {"metadata": "stop", "read": "stop"}
                and command(runtime, {"command": "read", "message": message("read", "rollback")})[
                    "status"
                ]
                == "denied"
                and command(server, {"command": "stats"}) == before_io
            )
            value["rollback_result"] = {
                "kind": "audit",
                "source_requests_before_restore": before_io,
                "source_requests_after_rejection": command(server, {"command": "stats"}),
                "restart_status": rejected["status"],
                "witness_preserved": witnessed_sha256 == preserved_sha256,
                "whole_host_rollback_protection": False,
            }
            return runtime_report(value, checks, identity, ready, named_denials)

        if value["case"] == "witness_evidence_rollback":
            with sqlite3.connect(state / "evidence/evidence.db") as database:
                accepted_semantic_revisions = database.execute(
                    "SELECT COUNT(*) FROM orion_semantic_checkpoints"
                ).fetchone()[0]
            before_io = command(server, {"command": "stats"})
            witness_path = witness / "progress-witness.db"
            witnessed_sha256 = hashlib.sha256(witness_path.read_bytes()).hexdigest()
            os.kill(ready["service_pids"]["evidence"], signal.SIGKILL)
            deadline = time.monotonic() + 3
            while (
                time.monotonic() < deadline
                and Path("/proc/" + str(ready["service_pids"]["evidence"])).exists()
            ):
                time.sleep(0.05)
            shutil.copyfile(evidence_snapshot[0], state / "evidence/evidence.db")
            shutil.copyfile(
                evidence_snapshot[1], state / "evidence/accepted-evidence-head"
            )
            preserved_sha256 = hashlib.sha256(witness_path.read_bytes()).hexdigest()
            rejected = command(runtime, {"command": "restart"})
            stale = command(runtime, {"command": "semantic", "mode": "restore"})
            checks["evidence_database_tip_and_semantic_rollback_rejected"] = (
                accepted_semantic_revisions == 1
                and admitted["semantic_assessment"]["status"] == "AVAILABLE"
                and rejected["status"] == "blocked"
                and witnessed_sha256 == preserved_sha256
            )
            checks["evidence_rollback_denied_before_source_io_or_stale_graph_publication"] = (
                command(server, {"command": "stats"}) == before_io
                and stale["status"] == "UNAVAILABLE"
                and "world_model" not in stale
                and command(runtime, {"command": "arm", "operation": "read"})[
                    "status"
                ]
                == "denied"
            )
            value["rollback_result"] = {
                "kind": "evidence_semantic",
                "accepted_semantic_revisions_before_restore": accepted_semantic_revisions,
                "stale_graph_publications_after_restore": 0,
                "source_requests_before_restore": before_io,
                "source_requests_after_rejection": command(server, {"command": "stats"}),
                "restart_status": rejected["status"],
                "witness_preserved": witnessed_sha256 == preserved_sha256,
                "whole_host_rollback_protection": False,
            }
            return runtime_report(value, checks, identity, ready, named_denials)

        if value["case"] == "witness_unavailable":
            before_io = command(server, {"command": "stats"})
            budget_before = status("read")["budget"]

            def acquisition_budget(budget):
                return {
                    name: budget[name]
                    for name in (
                        "attempts", "failures", "reserved_bytes", "stopped", "pending"
                    )
                }

            witness_path = witness / "progress-witness.db"
            backup = root / "preserved-witness.db"
            shutil.copyfile(witness_path, backup)
            os.kill(ready["service_pids"]["witness"], signal.SIGKILL)
            deadline = time.monotonic() + 3
            health = command(runtime, {"command": "health"})
            while time.monotonic() < deadline and health["status"] != "blocked":
                time.sleep(0.05)
                health = command(runtime, {"command": "health"})
            checks["witness_process_outage_uses_existing_cutoff"] = (
                health["status"] == "blocked"
                and command(server, {"command": "stats"}) == before_io
            )
            recovered = command(runtime, {"command": "restart"})
            budget_after_restart = status("read")["budget"]
            unarmed = acquire("read", "witness-outage-unarmed")
            budget_after_denial = status("read")["budget"]
            checks["intact_witness_restart_is_unarmed_and_preserves_budget"] = (
                recovered["status"] == "unarmed"
                and recovered["health"]["witness"]["available"] is True
                and acquisition_budget(budget_after_restart)
                == acquisition_budget(budget_before)
                and unarmed["status"] == "denied"
                and acquisition_budget(budget_after_denial)
                == acquisition_budget(budget_before)
                and command(server, {"command": "stats"}) == before_io
            )
            command(runtime, {"command": "cutoff"})
            os.kill(runtime.pid, signal.SIGKILL)
            runtime.wait(timeout=10)
            witness_path.unlink()
            runtime = subprocess.Popen(
                runtime_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd="/",
                env={"PATH": os.defpath},
                close_fds=True,
            )
            missing = read_frame(runtime.stdout)
            checks["missing_enrolled_witness_denies_fresh_runtime"] = (
                runtime.wait(timeout=10) == 2
                and missing["status"] == "BLOCKED"
                and command(server, {"command": "stats"}) == before_io
            )
            reenrollment = subprocess.run(
                [*runtime_entrypoint, "--enroll-witness", str(manifest_path)],
                cwd="/",
                capture_output=True,
                text=True,
                env={"PATH": os.defpath},
                timeout=15,
                check=False,
            )
            reenrollment_report = json.loads(reenrollment.stdout)
            checks["missing_enrolled_witness_cannot_reset_history"] = (
                reenrollment.returncode == 2
                and reenrollment_report["status"] == "BLOCKED"
                and not witness_path.exists()
                and command(server, {"command": "stats"}) == before_io
            )
            shutil.copyfile(backup, witness_path)
            witness_path.chmod(0o600)
            with sqlite3.connect(witness_path) as database:
                database.execute("UPDATE metadata SET body='{}'")
            runtime = subprocess.Popen(
                runtime_command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                cwd="/",
                env={"PATH": os.defpath},
                close_fds=True,
            )
            corrupt = read_frame(runtime.stdout)
            checks["corrupt_witness_denies_fresh_runtime"] = (
                runtime.wait(timeout=10) == 2
                and corrupt["status"] == "BLOCKED"
                and command(server, {"command": "stats"}) == before_io
            )
            value["rollback_result"] = {
                "kind": "witness_availability",
                "source_requests_before_failure": before_io,
                "source_requests_after_rejection": command(server, {"command": "stats"}),
                "process_outage_cutoff": health["status"],
                "missing_status": missing["status"],
                "corrupt_status": corrupt["status"],
                "whole_host_rollback_protection": False,
            }
            return runtime_report(value, checks, identity, ready, named_denials)

        if value["case"] == "restart_revalidation":
            before_io = command(server, {"command": "stats"})
            (keys / "source-credential").chmod(0o640)
            denied_restart = command(runtime, {"command": "restart"})
            checks["restart_secret_metadata_mismatch_uses_existing_cutoff"] = (
                denied_restart["status"] == "blocked"
                and denied_restart["execution_allowed"] is False
                and denied_restart["kernel_egress_removed"] is True
                and denied_restart["gateway_terminated"] is True
                and denied_restart["acquisition_terminated"] is True
                and command(runtime, {"command": "health"})["status"] == "blocked"
                and command(server, {"command": "stats"}) == before_io
            )
            checks["restart_revalidation_precedes_service_reconstruction"] = (
                all(
                    Path("/proc/" + str(ready["service_pids"][role])).exists()
                    for role in ("audit", "evidence", "authorization")
                )
                and not Path("/proc/" + str(ready["service_pids"]["acquisition"])).exists()
                and not Path("/proc/" + str(ready["gateway_pid"])).exists()
            )
            return runtime_report(value, checks, identity, ready, named_denials)

        if independent_case:
            assessment = admitted["semantic_assessment"]

            def budget_state(budget):
                # Denials and process starts legitimately append authenticated
                # audit history. They must not consume acquisition counters or
                # change the durable stop/pending state.
                return {key: item for key, item in budget.items() if key != "head"}

            def claim_status(current, field, role):
                return next(claim["hypothesis"]["status"] for claim in current["claims"]
                            if claim["field"] == field and claim["rule"]["role"] == role)

            checks["installed_initial_competing_unknown"] = (
                assessment["status"] == "AVAILABLE"
                and assessment["durable_checkpoint_sequence"] == 1
                and claim_status(assessment, "f_d", "monetary_measure") == "unknown"
                and claim_status(assessment, "f_e", "monetary_measure") == "unknown"
            )
            initial_references = set(assessment["evidence_references"])
            if value["case"] == "independent_failure":
                with sqlite3.connect(state / "evidence/evidence.db") as database:
                    database.execute("""
                        CREATE TRIGGER fail_independent AFTER INSERT ON orion_semantic_checkpoints
                        BEGIN SELECT RAISE(ABORT, 'synthetic revision storage failure'); END
                    """)
            operations = [config["operation"] for config in value["configs"][2:]]
            before_io = command(server, {"command": "stats"})
            for operation in operations:
                before_budget = budget_state(status(operation)["budget"])
                denied = command(runtime, {"command": "read",
                                           "message": message(operation, "instrument-unarmed")})
                checks[operation + "_separate_authority_unarmed_denial"] = (
                    denied.get("response", denied)["status"] == "denied"
                    and budget_state(status(operation)["budget"]) == before_budget
                    and command(server, {"command": "stats"}) == before_io
                )
            assessments = [assessment]
            for index, operation in enumerate(operations):
                armed = command(runtime, {"command": "arm", "operation": operation})
                assert armed["status"] == "arm", armed
                before_budget = budget_state(status(operation)["budget"])
                for donor in ("metadata", "read"):
                    denied_message = message(operation, "instrument-wrong-grant-" + donor)
                    denied_message["grant_token"] = message(donor, "unused")["grant_token"]
                    denied = command(runtime, {"command": "read", "message": denied_message})
                    checks[operation + "_" + donor + "_grant_denied_before_io"] = (
                        denied.get("response", denied)["status"] == "denied"
                        and budget_state(status(operation)["budget"]) == before_budget
                        and command(server, {"command": "stats"}) == before_io
                    )
                if index == 0:
                    for field, replacement in (("tenant_id", "unapproved-synthetic"),
                                               ("company", "unapproved-synthetic"),
                                               ("source_id", "https://unapproved.synthetic.test"),
                                               ("resource", "unapproved-synthetic"),
                                               ("fields", ["id", "partition", "on"]),
                                               ("start", "2024-05-01")):
                        denied_message = message(operation, "instrument-wrong-" + field)
                        denied_message["request"][field] = replacement
                        denied = command(runtime, {"command": "read", "message": denied_message})
                        checks["instrument_wrong_" + field + "_denied_before_io"] = (
                            denied.get("response", denied)["status"] == "denied"
                            and budget_state(status(operation)["budget"]) == before_budget
                            and command(server, {"command": "stats"}) == before_io
                        )
                result = command(runtime, {"command": "read",
                                           "message": message(operation, "instrument-read-" + str(index))})
                response = result.get("response", result)
                assert response["status"] == "admitted", response
                assert response["interpretation"] == "UNKNOWN"
                assessment = response["semantic_assessment"]
                if value["case"] == "independent_failure" and index == 0:
                    checks["failed_revision_append_never_publishes_success"] = (
                        assessment["status"] == "UNAVAILABLE" and assessment["durable"] is False
                        and "world_model" not in assessment
                    )
                    with sqlite3.connect(state / "evidence/evidence.db") as database:
                        checks["failed_revision_no_partial_checkpoint"] = database.execute(
                            "SELECT COUNT(*) FROM orion_semantic_checkpoints"
                        ).fetchone()[0] == 1
                    pending = command(runtime, {"command": "semantic", "mode": "restore"})
                    checks["pending_revision_no_stale_restore"] = (
                        pending["status"] == "UNAVAILABLE" and "world_model" not in pending
                    )
                    with sqlite3.connect(state / "evidence/evidence.db") as database:
                        database.execute("DROP TRIGGER fail_independent")
                    assessment = command(runtime, {"command": "semantic", "mode": "evaluate"})
                assert assessment["status"] == "AVAILABLE", assessment
                before_io[operation] += 1
                checks[operation + "_separately_admitted_exact_io"] = (
                    command(server, {"command": "stats"}) == before_io
                    and status(operation)["budget"]["attempts"] == 1
                    and all(obs.evidence.kind.value == "experiment"
                            for obs in observations_from(response["observations"]))
                )
                checks[operation + "_accepted_successive_checkpoint"] = (
                    assessment["durable_checkpoint_sequence"] == index + 2
                    and len(assessment["semantic_revision_ids"]) == index + 1
                    and initial_references <= set(assessment["evidence_references"])
                )
                assessments.append(assessment)
                replay = command(runtime, {"command": "semantic", "mode": "evaluate"})
                checks[operation + "_canonical_replay_idempotent_no_io"] = (
                    replay == assessment and command(server, {"command": "stats"}) == before_io
                )
            if value["case"] == "independent_unknown":
                checks["insufficient_independent_grounding_remains_unknown"] = (
                    claim_status(assessment, "f_d", "monetary_measure") == "unknown"
                    and claim_status(assessment, "f_c", "completion_date") == "unknown"
                    and claim_status(assessment, "f_a", "recipient_reference") == "unknown"
                )
                checks["unknown_requires_separate_next_evidence_authorization"] = (
                    bool(assessment["required_next_evidence"])
                    and assessment["authorization_required"] == "separate_instrument_read_grant"
                )
            else:
                convergence = assessments[2]
                checks["distinct_synthetic_roots_canonical_convergence"] = (
                    claim_status(convergence, "f_d", "monetary_measure") == "validated"
                    and claim_status(convergence, "f_e", "monetary_measure") == "invalidated"
                    and claim_status(convergence, "f_d", "physical_measure") == "unknown"
                    and claim_status(convergence, "f_c", "completion_date") == "unknown"
                )
                checks["reviewed_lineage_published_without_truth_certification"] = (
                    len(convergence["lineage"]) == 4
                    and len({root[0] for entry in convergence["lineage"]
                             for root in entry["origin"]["roots"]}) == 2
                )
                if value["case"] == "independent_revision":
                    checks["explicit_replacement_changes_current_belief"] = (
                        claim_status(assessment, "f_d", "monetary_measure") == "invalidated"
                        and claim_status(assessment, "f_e", "monetary_measure") == "validated"
                        and set(convergence["evidence_references"]) < set(assessment["evidence_references"])
                        and assessment["semantic_revision_ids"][:2] == convergence["semantic_revision_ids"]
                        and assessment["history"][:2] == convergence["history"]
                    )
            with sqlite3.connect(state / "evidence/evidence.db") as database:
                checks["successive_checkpoints_retained"] = database.execute(
                    "SELECT COUNT(*) FROM orion_semantic_checkpoints"
                ).fetchone()[0] == len(operations) + 1
            budget_before = {operation: budget_state(status(operation)["budget"])
                             for operation in ("metadata", "read", *operations)}
            if value["case"] == "independent_expired":
                time.sleep(45.2)
            os.kill(ready["control_pid"], signal.SIGKILL)
            runtime.wait(timeout=10)
            runtime = subprocess.Popen(
                runtime_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, cwd="/", env={"PATH": os.defpath},
                close_fds=True,
            )
            restored_start = read_frame(runtime.stdout)
            checks["fresh_process_restore_zero_source_io"] = (
                server.poll() is None and command(server, {"command": "stats"}) == before_io
            )
            checks["fresh_installed_process_identical_current_and_history"] = (
                restored_start["status"] == "unarmed"
                and (restored_start["semantic_assessment"] == assessment
                     if value["case"] != "independent_expired" else
                     restored_start["semantic_assessment"]["status"] == "UNAVAILABLE"
                     and "world_model" not in restored_start["semantic_assessment"])
                and restored_start["health"]["metadata_admitted_this_start"] is False
                and restored_start["health"]["record_authority_armed"] is False
                and all(budget_state(restored_start["health"]["budgets"][operation]) == budget
                        for operation, budget in budget_before.items())
                and restored_start["health"]["instrument_authority_armed"]
                == dict.fromkeys(operations, False)
            )
            for operation in operations:
                denied = command(runtime, {"command": "read",
                                           "message": message(operation, "post-restart")})
                checks[operation + "_restart_does_not_restore_authority"] = (
                    denied.get("response", denied)["status"] == "denied"
                    and budget_state(command(runtime, {"command": "health"})["budgets"][operation])
                    == budget_before[operation]
                    and command(server, {"command": "stats"}) == before_io
                )
            restored_assessment = command(runtime, {"command": "semantic", "mode": "restore"})
            checks["explicit_semantic_restore_zero_source_io"] = (
                restored_assessment == restored_start["semantic_assessment"]
                and command(server, {"command": "stats"}) == before_io
            )
            checks["semantic_recovery_is_not_execution_authority"] = (
                assessment["authority_restored"] is False and assessment["execution_allowed"] is False
            )
            checks["instrument_durable_stop"] = (
                command(runtime, {"command": "stop"})["status"] == "stopped"
                and command(server, {"command": "stats"}) == before_io
            )
            for operation in operations:
                checks[operation + "_stop_denies_rearm"] = command(
                    runtime, {"command": "arm", "operation": operation})["status"] == "denied"
            stopped_assessment = command(runtime, {"command": "semantic", "mode": "restore"})
            checks["stopped_runtime_retains_only_knowledge"] = (
                stopped_assessment == assessment if value["case"] != "independent_expired" else
                stopped_assessment["status"] == "UNAVAILABLE" and "world_model" not in stopped_assessment
            )
            checks["stop_and_recovery_zero_source_io"] = (
                server.poll() is None and command(server, {"command": "stats"}) == before_io
            )
            value["semantic_result"] = {
                "sampled_monetary_f_d": claim_status(assessment, "f_d", "monetary_measure"),
                "sampled_monetary_f_e": claim_status(assessment, "f_e", "monetary_measure"),
                "completion_date": claim_status(assessment, "f_c", "completion_date"),
                "checkpoint_sequence": assessment["durable_checkpoint_sequence"],
                "revision_count": len(assessment["semantic_revision_ids"]),
                "historical_claim_batches": len(assessment["history"]),
                "lineage_observations": len(assessment["lineage"]),
                "fresh_process_recovery": checks["fresh_installed_process_identical_current_and_history"],
                "registry_trust": "separately reviewed synthetic registry; not collector truth certification",
                "next_authorization": assessment["authorization_required"],
            }
            return runtime_report(value, checks, identity, ready, named_denials)

        if semantic_case:
            assessment = admitted["semantic_assessment"]
            io_before = command(server, {"command": "stats"})
            budget_before = status("read")["budget"]
            if value["case"] == "semantic_failure":
                checks["failed_append_not_durable_publication"] = (
                    assessment["status"] == "UNAVAILABLE" and assessment["durable"] is False
                    and "world_model" not in assessment
                )
                with sqlite3.connect(state / "evidence/evidence.db") as database:
                    checks["failed_append_no_partial_index"] = database.execute(
                        "SELECT COUNT(*) FROM orion_semantic_checkpoints"
                    ).fetchone()[0] == 0
                    database.execute("DROP TRIGGER fail_semantic")
                assessment = command(runtime, {"command": "semantic", "mode": "evaluate"})
            checks["installed_canonical_unknown_is_persisted"] = (
                assessment["status"] == "AVAILABLE"
                and assessment["epistemic_status"] == "UNKNOWN"
                and assessment["durable"] is True
                and assessment["authority_restored"] is False
                and assessment["execution_allowed"] is False
                and assessment["semantic_revision_ids"] == []
            )
            checks["independent_evidence_explicitly_blocked"] = assessment[
                "independent_grounding"
            ].startswith("BLOCKED:")
            replay = command(runtime, {"command": "semantic", "mode": "evaluate"})
            checks["semantic_replay_identical_no_source_io"] = (
                replay == assessment and status("read")["budget"] == budget_before
                and command(server, {"command": "stats"}) == io_before
            )
            with sqlite3.connect(state / "evidence/evidence.db") as database:
                checks["one_append_only_semantic_checkpoint"] = database.execute(
                    "SELECT COUNT(*) FROM orion_semantic_checkpoints"
                ).fetchone()[0] == 1
            if value["case"] == "semantic_retention":
                time.sleep(8.2)
                unavailable = command(runtime, {"command": "semantic", "mode": "restore"})
                checks["expired_evidence_no_cached_assessment"] = (
                    unavailable["status"] == "UNAVAILABLE" and "world_model" not in unavailable
                    and archive("load")["observations"] == []
                    and len(archive("inspect")["checkpoints"]) == 1
                )
                with sqlite3.connect(state / "evidence/evidence.db") as database:
                    checks["expired_archive_keeps_semantic_history"] = database.execute(
                        "SELECT COUNT(*) FROM orion_semantic_checkpoints"
                    ).fetchone()[0] == 1
                return runtime_report(value, checks, identity, ready, named_denials)
            if value["case"] in ("semantic_changed", "semantic_missing"):
                # Ordinary offline integrity mutation of synthetic archive data;
                # no key access, extraction or containment experiment.
                with sqlite3.connect(state / "evidence/evidence.db") as database:
                    if value["case"] == "semantic_missing":
                        database.execute("DELETE FROM payloads")
                    else:
                        database.execute("UPDATE payloads SET body = '[]'")
                unavailable = command(runtime, {"command": "semantic", "mode": "restore"})
                checks["changed_archive_no_cached_assessment"] = (
                    unavailable["status"] == "UNAVAILABLE" and "world_model" not in unavailable
                )
                return runtime_report(value, checks, identity, ready, named_denials)
            stopped_server = server
            os.kill(ready["control_pid"], signal.SIGKILL)
            runtime.wait(timeout=10)
            stopped_server.kill()
            stopped_server.wait(timeout=5)
            server = None
            runtime = subprocess.Popen(
                runtime_command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.PIPE, text=True, cwd="/", env={"PATH": os.defpath},
                close_fds=True,
            )
            restored_start = read_frame(runtime.stdout)
            checks["actual_entrypoint_process_restart_recovers_graph"] = (
                restored_start["status"] == "unarmed"
                and restored_start["semantic_assessment"] == assessment
                and restored_start["health"]["budgets"]["read"]["attempts"] == 1
                and restored_start["health"]["metadata_admitted_this_start"] is False
                and restored_start["health"]["record_authority_armed"] is False
            )
            restart_denial = command(runtime, {
                "command": "read", "message": message("read", "post-crash")})
            checks["restart_recovery_no_authority_or_source_io"] = (
                restart_denial.get("response", restart_denial)["status"] == "denied"
                and command(runtime, {"command": "health"})["budgets"]["read"]["attempts"] == 1
            )
            checks["semantic_revocation_preserves_only_historical_knowledge"] = (
                command(runtime, {"command": "revoke"})["status"] == "revoked"
                and command(runtime, {"command": "semantic", "mode": "restore"}) == assessment
                and command(runtime, {"command": "arm", "operation": "read"})["status"] == "denied"
            )
            checks["semantic_stop_durable"] = command(runtime, {"command": "stop"})["status"] == "stopped"
            checks["stopped_semantic_restore_not_authority"] = (
                command(runtime, {"command": "semantic", "mode": "restore"}) == assessment
                and command(runtime, {"command": "arm", "operation": "read"})["status"] == "denied"
            )
            command(runtime, {"command": "fail", "role": "evidence"})
            lost = command(runtime, {"command": "semantic", "mode": "restore"})
            checks["lost_custody_never_publishes_cached_graph"] = (
                lost["status"] == "UNAVAILABLE" and lost["durable"] is False
                and "world_model" not in lost
            )
            checks["durable_stop_restart_still_denied"] = command(
                runtime, {"command": "restart"})["status"] == "blocked"
            return runtime_report(value, checks, identity, ready, named_denials)

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
            before_retention = next(
                row for row in witness_rows() if row["kind"] == "evidence"
            )
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
            after_retention = next(
                row for row in witness_rows() if row["kind"] == "evidence"
            )
            checks["retention_advances_without_erasing_witness_high_water_mark"] = (
                after_retention["sequence"] > before_retention["sequence"]
                and after_retention["predecessor_sequence"]
                == before_retention["sequence"]
                and after_retention["predecessor_head"] == before_retention["head"]
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
        if value["case"] not in ("full", "ipv6", "erpnext_candidate"):
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
