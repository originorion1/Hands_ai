"""External, test-owned controller and arbitrary installed-role attack payload.

Never shipped in the wheel. The trusted controller replaces only the executable
suffix of captured production commands, not their jail or mount composition.
The attacker receives public scope/path fixtures, never an operator secret.
"""

import json
import os
import select
import signal
import subprocess
import sys
import traceback
from pathlib import Path


def emit(value):
    print(json.dumps(value, sort_keys=True), flush=True)


def forge_acquisition(response):
    """Compromised acquisition controls its own socket, never custody storage."""
    from orion.pilot.ipc import Endpoint

    # Consume the same private bootstrap pipe used by the production role.
    json.loads(sys.stdin.readline(65537))
    endpoint_path = Path("/endpoint/service")
    displaced = Path("/endpoint/displaced-original-service")
    endpoint_path.rename(displaced)
    endpoint = None
    try:
        capabilities = json.loads(Path("/private/capabilities").read_text())

        def dispatch(role, action, message):
            if role != "reasoner" or action != "read":
                raise ValueError("fixed attack reply route required")
            return response

        endpoint = Endpoint(endpoint_path, {"reasoner": capabilities["reasoner"].encode()}, dispatch)
        emit({"ready": True})
        if json.loads(sys.stdin.readline(65537)) != {"command": "shutdown"}:
            raise ValueError("fixed test shutdown required")
    finally:
        if endpoint:
            endpoint.close()
        if endpoint_path.exists():
            endpoint_path.unlink()
        displaced.rename(endpoint_path)


def attack(value):
    import ctypes
    import hashlib
    import socket
    import ssl
    from collections.abc import Mapping
    from datetime import UTC, datetime
    from urllib.request import Request, urlopen

    from orion.discovery.erpnext_adapter import ERPNextDiscoveryAdapter, _default_opener
    from orion.discovery.erpnext_live_session import CredentialEnvironmentReferences
    from orion.discovery.erpnext_pilot import ERPNextPilotReader
    from orion.discovery.http_adapter import ReadOnlyHttpDiscoveryAdapter, _default_fetcher
    from orion.pilot.broker_contract import authenticate, digest, grant_from
    from orion.pilot.gateway import CredentialGateway
    from orion.pilot.ipc import rpc

    checks = {}

    def denied(name, action):
        try:
            result = action()
        except Exception:  # noqa: BLE001 - attacker records only an outcome, never secrets
            checks[name] = True
        else:
            checks[name] = type(result) is dict and result.get("status") == "denied"

    status = dict(line.split(":", 1) for line in Path("/proc/self/status").read_text().splitlines()
                  if ":" in line)
    checks["actual_capabilities_zero"] = int(status["CapEff"], 16) == 0
    checks["actual_namespaces_separated"] = all(
        os.readlink("/proc/self/ns/" + name) != value["parent"][name]
        for name in ("user", "pid", "mnt", "net")
    )
    checks["actual_proxy_environment_cleared"] = not any("proxy" in key.lower() for key in os.environ)
    checks["control_environment_credentials_absent"] = all(
        key not in os.environ for key in ("BROKER_AUTH_KEY", "BROKER_SOURCE_SECRET", "ORION_API_KEY", "ORION_API_SECRET")
    )
    private_paths = [
        "/private/issuer", "/private/credential", "/private/worker-secret", "/private/signing-key",
        "/private/audit-capability", "/private/evidence-capability", "/state",
        *value["protected_paths"],
    ]
    for index, path in enumerate(private_paths):
        denied("protected_original_or_role_path_" + str(index), lambda p=path: Path(p).read_bytes())
        checks["protected_path_absent_" + str(index)] = not Path(path).exists()
    for index, path in enumerate(value["storage_paths"]):
        denied("custody_truncate_denied_" + str(index),
               lambda p=path: os.close(os.open(p, os.O_WRONLY | os.O_TRUNC)))
        denied("custody_unlink_denied_" + str(index), lambda p=path: os.unlink(p))
        denied("custody_rollback_replace_denied_" + str(index),
               lambda p=path: os.replace(p, p + ".attacker-rollback"))
    # The PID jail hides the real supervisor/custodians, rather than relying on
    # normal same-UID /proc permissions. No process contents are printed.
    for index, pid in enumerate(value["protected_pids"]):
        checks["protected_process_not_visible_" + str(index)] = not Path("/proc/" + str(pid)).exists()
        for field in ("environ", "mem", "fd/0", "root"):
            denied("protected_proc_" + str(index) + "_" + field.replace("/", "_"),
                   lambda p=pid, f=field: Path("/proc/" + str(p) + "/" + f).read_bytes())

    refs = CredentialEnvironmentReferences("ORION_API_KEY", "ORION_API_SECRET")
    denied("legacy_real_environment_credential_resolution", lambda: refs.resolve(os.environ))

    class FabricatedEnvironment(Mapping):
        def __getitem__(self, key):
            return "AttackerFabricatedKey" if key == "ORION_API_KEY" else "AttackerFabricatedSecret"

        def __iter__(self):
            return iter(("ORION_API_KEY", "ORION_API_SECRET"))

        def __len__(self):
            return 2

    bogus = refs.resolve(FabricatedEnvironment())
    # Legacy caller-owned mappings can fabricate values, not obtain custody.
    checks["legacy_fake_mapping_only_attacker_values"] = (
        bogus.api_key == "AttackerFabricatedKey" and bogus.api_secret == "AttackerFabricatedSecret"
    )
    mapping_attempts = []

    class CustodyReadingEnvironment(FabricatedEnvironment):
        def __getitem__(self, key):
            mapping_attempts.append(True)
            return Path(value["protected_paths"][0]).read_text()

    denied("legacy_callable_mapping_cannot_read_issuer", lambda: refs.resolve(CustodyReadingEnvironment()))
    checks["legacy_callable_mapping_executed"] = bool(mapping_attempts)
    host = "[" + value["host"] + "]" if ":" in value["host"] else value["host"]
    origin = "https://" + host + ":44443"
    url = origin + "/records"
    tls = ssl._create_unverified_context()  # Hostile TLS-policy bypass; synthetic source only.

    fetch_calls = []

    def fetch(target):
        fetch_calls.append(True)
        return urlopen(target, timeout=0.3, context=tls).read()

    denied("legacy_default_fetcher_disabled", lambda: _default_fetcher(url))
    denied("legacy_default_opener_disabled", lambda: _default_opener(Request(url), timeout=1))
    denied("legacy_injected_fetcher_kernel_denied", lambda: ReadOnlyHttpDiscoveryAdapter(
        base_url=origin, paths=("/records",), fetcher=fetch,
    ).discover(tenant_id="t_01", observed_at=datetime.now(UTC)))
    checks["legacy_injected_fetcher_executed"] = bool(fetch_calls)
    called = []

    def malicious_opener(request, timeout):
        called.append(True)
        return urlopen(url, timeout=0.3, context=tls)

    denied("legacy_injected_opener_kernel_denied", lambda: ERPNextDiscoveryAdapter(
        base_url=origin, tenant_id="t_01", api_key=bogus.api_key, api_secret=bogus.api_secret,
        resources=("r_01",), page_size=1, max_pages=1, timeout_seconds=1,
        opener=malicious_opener,
    ).discover())
    checks["legacy_injected_opener_executed"] = bool(called)
    pilot = ERPNextPilotReader(source_id=origin, api_key=bogus.api_key, api_secret=bogus.api_secret,
                              opener=malicious_opener)
    denied("legacy_pilot_without_launcher_permit", lambda: pilot.read(object()))
    denied("legacy_private_callable_kernel_denied", lambda: pilot._opener(Request(url), timeout=1))
    denied("arbitrary_urllib_kernel_denied", lambda: fetch(url))
    # Constructor APIs are reachable too. Attacker-selected approval and trust
    # cannot turn this zero-network application role into the credential gateway.
    certificate = Path("/tmp/attacker-public-certificate.pem")
    certificate.write_text(value["certificate_pem"])
    certificate.chmod(0o600)
    approvals = []

    def invented_custody(action, request):
        approvals.append(True)
        return {"authorized": True, "binding": request["binding"]}

    fake_gateway = CredentialGateway(value["configs"], invented_custody,
                                     "AttackerFabricatedCredential0123456789", str(certificate),
                                     hashlib.sha256(certificate.read_bytes()).hexdigest(), value["host"])
    denied("raw_gateway_callable_approval_kernel_denied", lambda: fake_gateway.dispatch(
        "acquisition", "acquire", {"binding": digest(value["configs"][1]), "receipt": "fake"},
    ))
    checks["raw_gateway_callable_approval_executed"] = bool(approvals)
    denied("raw_gateway_direct_read_kernel_denied", lambda: fake_gateway.read("/records"))
    addresses = [("192.0.2.2", 44443), ("192.0.2.3", 44443), ("192.0.2.2", 44444),
                 ("192.0.2.3", 44445)]
    if value["ipv6"]:
        addresses.extend([(a, p) for a in ("fd42:6f72:696f::2", "fd42:6f72:696f::3")
                          for p in (44443, 44444, 44445)])
        addresses.append(("::ffff:192.0.2.3", 44443))

    def connect(address, port):
        with socket.socket(socket.AF_INET6 if ":" in address else socket.AF_INET) as peer:
            peer.settimeout(0.2)
            peer.connect((address, port))

    for index, (address, port) in enumerate(addresses):
        denied("arbitrary_direct_socket_" + str(index), lambda a=address, p=port: connect(a, p))
    commands = {
        "native_curl": ["/usr/bin/curl", "--disable", "--insecure", "--noproxy", "*",
                        "--connect-timeout", "0.3", "--max-time", "0.5", url],
        "native_proxy": ["/usr/bin/curl", "--disable", "--insecure", "--proxy",
                         "http://192.0.2.3:44445", "--connect-timeout", "0.3", "--max-time", "0.5", url],
        "native_nsenter_escape": ["/usr/bin/nsenter", "--net=/proc/1/ns/net", "/usr/bin/true"],
        "native_nft_weaken": ["/usr/sbin/nft", "flush", "ruleset"],
        "native_nested_network": ["/usr/bin/unshare", "--net", "/usr/bin/true"],
        "native_nested_user_ancestor_network": ["/usr/bin/unshare", "--user", "--map-root-user",
                                               "/usr/bin/nsenter", "--net=/proc/1/ns/net", "/usr/bin/true"],
        "native_nested_user_private_network_source": ["/usr/bin/unshare", "--user", "--map-root-user", "--net",
                                                       sys.executable, "-I", "-c",
                                                       "import socket; socket.create_connection(('192.0.2.2',44443),timeout=0.2)"],
    }
    for name, argv in commands.items():
        result = subprocess.run(argv, capture_output=True, timeout=2, check=False)
        checks[name + "_denied"] = result.returncode != 0
    libc = ctypes.CDLL(None, use_errno=True)
    with open("/proc/1/ns/net", "rb") as namespace:
        checks["arbitrary_setns_denied"] = libc.setns(namespace.fileno(), 0) == -1

    config = value["configs"][1]
    grant = grant_from(config["grant"])
    forged_key = b"AttackerOwnSigningKeyNeverIssuer0123456789"
    checks["canonical_grant_constructible_but_not_authority"] = grant.authorization_id == config["grant"]["authorization_id"]
    message = dict(value["message"])
    message["grant_token"] = authenticate(forged_key, "read_grant", digest(config))
    role = value["role"]
    if role == "acquisition":
        authorization_key = Path("/private/authorization-capability").read_bytes()
        gateway_key = Path("/private/gateway-capability").read_bytes()
        for claimed in ("owner", "source"):
            denied("authorization_" + claimed + "_spoof", lambda r=claimed: rpc(
                "/authorization/service", r, authorization_key, "health" if r == "owner" else "redeem",
                {"receipt": "0" * 128, "binding": digest(config)},
            ))
        denied("canonical_grant_forgery_independent_denial", lambda: rpc(
            "/authorization/service", "broker", authorization_key, "begin", message,
        ))
        scope = dict(value["message"], request=dict(value["message"]["request"], tenant_id="attacker-tenant"))
        denied("canonical_scope_expansion_independent_denial", lambda: rpc(
            "/authorization/service", "broker", authorization_key, "begin", scope,
        ))
        denied("grant_issuance_role_action_denied", lambda: rpc(
            "/authorization/service", "broker", authorization_key, "control",
            {"operation": "read", "message": {"control": "arm", "mac": "0" * 64}},
        ))
        denied("forged_gateway_receipt_independent_denial", lambda: rpc(
            "/gateway/service", "acquisition", gateway_key, "acquire",
            {"receipt": "0" * 128, "binding": digest(config)},
        ))
        denied("gateway_arbitrary_destination_denied", lambda: rpc(
            "/gateway/service", "acquisition", gateway_key, "acquire",
            {"receipt": "0" * 128, "binding": digest(config), "url": "https://192.0.2.3:44443/records"},
        ))
        available_key = authorization_key
    else:
        available_key = Path("/private/reasoning-capability").read_bytes()
        denied("canonical_grant_forgery_independent_denial", lambda: rpc(
            "/acquisition/service", "reasoner", available_key, "read", message,
        ))
        denied("acquisition_owner_role_spoof", lambda: rpc(
            "/acquisition/service", "owner", available_key, "read", message,
        ))
        denied("acquisition_control_action_denied", lambda: rpc(
            "/acquisition/service", "reasoner", available_key, "control", message,
        ))
    for service, action in (("evidence", "append"), ("evidence", "prune"), ("audit", "lifecycle")):
        denied(service + "_mutation_or_rollback_" + action, lambda s=service, a=action: rpc(
            "/" + s + "/service", "owner", available_key, a,
            {"binding": digest(config), "arguments": {}},
        ))
    denied("witness_state_and_advance_inaccessible", lambda: rpc(
        "/witness/service", "audit", available_key, "advance",
        {"expected_sequence": 1, "expected_head": "0" * 64, "state": {}},
    ))
    config_path = Path("/tmp/forged-config.json")
    config_path.write_text(json.dumps(config))
    config_path.chmod(0o600)
    alternatives = {
        "broker": ["--config", str(config_path), "--state", "/tmp/attacker-journal"],
        "broker_worker": ["--seal-fd", "0"],
        "deployment": ["--serve", "/tmp/forged-manifest.json"],
        "services": ["--child"],
    }
    for module, argv in alternatives.items():
        result = subprocess.run([sys.executable, "-I", "-m", "orion.pilot." + module, *argv],
                                input=b"{}\n", capture_output=True, timeout=3, check=False)
        checks["alternate_installed_entrypoint_" + module + "_denied"] = result.returncode != 0
    legacy = subprocess.run([sys.executable, "-I", "-m", "orion.discovery.erpnext_live_session"],
                            input=b"{}\n", capture_output=True, timeout=3, check=False)
    checks["alternate_legacy_live_session_entrypoint_denied"] = legacy.returncode != 0
    emit({"checks": checks})


def supervisor(manifest_path, descriptors):
    import orion.pilot.deployment as runtime
    from orion.pilot.broker_contract import digest
    from orion.pilot.ipc import rpc
    from orion.pilot.journal import JournalDenied

    runtime.protect_parent()
    baselines = [os.readlink("/proc/self/fd/" + str(fd)) for fd in descriptors]
    for descriptor in descriptors:
        os.close(descriptor)
    deployment = runtime.Deployment(runtime.load_manifest(manifest_path),
                                    baseline_net=baselines[0], baseline_user=baselines[1])
    original = runtime.process_command
    captured = {}
    malicious_stdout = [None]
    admitted_output = [None]

    def command(module, *, readonly=(), writable=(), network=False):
        result = original(module, readonly=readonly, writable=writable, network=network)
        targets = {target for _, target in readonly}
        role = ("acquisition" if "/private/gateway-capability" in targets else
                "reasoner" if "/private/reasoning-capability" in targets else None)
        if role:
            captured[role] = result
        if role == "reasoner" and malicious_stdout[0] is not None:
            return replace(result, "print(" + repr(json.dumps(malicious_stdout[0])) + ")")
        return result

    def replace(command, code, *arguments):
        if command[-4:] != ["-I", "-m", "orion.pilot.services", "--child"]:
            raise ValueError("exact production suffix required")
        replaced = [*command[:-5], command[-5], "-I", "-c", code, *arguments]
        assert replaced[:-4 - len(arguments)] == command[:-5]
        return replaced

    runtime.process_command = command
    signal.signal(signal.SIGTERM, lambda *args: (_ for _ in ()).throw(KeyboardInterrupt()))
    try:
        emit(deployment.start())
        while select.select([sys.stdin], [], [], 0.2)[0] or not deployment.blocked:
            raw = sys.stdin.readline(65537)
            if not raw:
                break
            value = json.loads(raw)
            if value == {"command": "shutdown"}:
                break
            try:
                if value["command"] != "attack":
                    response = deployment.dispatch(value)
                    if value["command"] == "read" and response.get("response", {}).get("status") == "admitted":
                        admitted_output[0] = response
                    emit(response)
                    continue
                evidence = deployment.endpoints["evidence"]
                audit = deployment.endpoints["audit"]
                before = {
                    operation: rpc(audit, "supervisor", deployment.caps["audit"], "records",
                                   {"binding": digest(config), "arguments": {}})
                    for operation, config in zip(("metadata", "read"), deployment.configs, strict=True)
                }
                checks = {}
                for role in ("acquisition", "reasoner"):
                    inputs = dict(value, role=role, parent=deployment.parent,
                                  host=deployment.manifest["host"], configs=deployment.configs,
                                  certificate_pem=Path(deployment.manifest["certificate"]).read_text(),
                                  protected_paths=[str(deployment.keys / name) for name in
                                                   ("issuer", "source-credential", "worker-secret",
                                                    "audit-signing", "evidence-signing", "witness-signing")]
                                  + [str(deployment.root), str(evidence), str(audit),
                                     str(deployment.witness_root), str(deployment.endpoints["witness"]),
                                     str(deployment.cap_files["owner"]), str(deployment.cap_files["auth-source"])],
                                  protected_pids=[os.getpid(), *(p.pid for p in deployment.processes.values())])
                    inputs["storage_paths"] = [str(deployment.root / directory / filename)
                                               for directory, filename in (
                                                   ("audit/read", "broker.db"),
                                                   ("audit/read", "accepted-head"),
                                                   ("evidence", "evidence.db"),
                                                   ("evidence", "accepted-evidence-head"),
                                               )]
                    inputs["storage_paths"].append(
                        str(deployment.witness_root / "progress-witness.db")
                    )
                    program = replace(captured[role], Path(__file__).read_text(), "--attack", json.dumps(inputs))
                    bootstrap = {"role": role, "parent": deployment.parent,
                                 "policy": deployment.manifest["policy"]}
                    if role == "reasoner":
                        bootstrap["message"] = value["message"]
                    result = subprocess.run(program, input=json.dumps(bootstrap) + "\n",
                                            capture_output=True, text=True, timeout=30,
                                            close_fds=True, env={"PATH": runtime.LAB_PATH}, check=False)
                    if result.returncode or result.stderr:
                        checks[role + "_attack_payload_executed"] = False
                        try:
                            failure = json.loads(result.stdout)
                        except (ValueError, TypeError):
                            failure = {}
                        checks[role + "_payload_failure_line_" + str(failure.get("failure_line", 0))] = False
                        continue
                    checks[role + "_attack_payload_executed"] = True
                    checks.update({role + "_" + key: answer for key, answer in json.loads(result.stdout)["checks"].items()})
                for index, forged in enumerate((
                    {"response": {"status": "admitted", "observations": []}, "reasoner_checks": {}},
                    {"response": {"status": "admitted", "observations": [], "checkpoint": "fake"},
                     "reasoner_checks": {"separated": True}},
                    admitted_output[0],
                )):
                    malicious_stdout[0] = forged
                    try:
                        response = deployment.reason(dict(value["message"], request_id="forged-stdout-" + str(index)))
                    except (JournalDenied, ValueError, KeyError, TypeError):
                        checks["forged_reasoner_stdout_independent_denial_" + str(index)] = True
                    else:
                        checks["forged_reasoner_stdout_independent_denial_" + str(index)] = response.get("response", {}).get("status") == "denied"
                    finally:
                        malicious_stdout[0] = None
                forger = subprocess.Popen(
                    replace(captured["acquisition"], Path(__file__).read_text(),
                            "--forge-acquisition", json.dumps(admitted_output[0]["response"])),
                    stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                    text=True, close_fds=True, env={"PATH": runtime.LAB_PATH},
                )
                try:
                    forger.stdin.write(json.dumps({"role": "acquisition", "parent": deployment.parent,
                                                  "policy": deployment.manifest["policy"]}) + "\n")
                    forger.stdin.flush()
                    checks["compromised_acquisition_rpc_payload_executed"] = runtime.receive(forger)["ready"] is True
                    try:
                        response = deployment.reason(dict(value["message"], request_id="forged-acquisition-reply"))
                    except (JournalDenied, ValueError, KeyError, TypeError):
                        checks["forged_acquisition_rpc_independent_denial"] = True
                    else:
                        checks["forged_acquisition_rpc_independent_denial"] = response.get("response", {}).get("status") == "denied"
                finally:
                    forger.stdin.write(json.dumps({"command": "shutdown"}) + "\n")
                    forger.stdin.flush()
                    forger.wait(timeout=5)
                    checks["original_acquisition_socket_restored"] = forger.returncode == 0 and not forger.stderr.read()
                after = {
                    operation: rpc(audit, "supervisor", deployment.caps["audit"], "records",
                                   {"binding": digest(config), "arguments": {}})
                    for operation, config in zip(("metadata", "read"), deployment.configs, strict=True)
                }
                emit({"checks": checks, "audit_prefix_intact": all(
                    after[operation][:len(before[operation])] == before[operation]
                    for operation in before)})
            except Exception:  # noqa: BLE001 - fixed test denial, never key-bearing values
                emit({"status": "denied", "LIVE_PILOT_READY": False})
    finally:
        deployment.stop()
        deployment.close()


if __name__ == "__main__":
    if sys.argv[1:2] == ["--attack"]:
        try:
            attack(json.loads(sys.argv[2]))
        except Exception as error:  # noqa: BLE001 - no exception values or private input
            emit({"status": "FAIL", "failure_type": type(error).__name__,
                  "failure_line": traceback.extract_tb(error.__traceback__)[-1].lineno})
            raise SystemExit(1) from None
    elif sys.argv[1:2] == ["--supervisor"]:
        supervisor(sys.argv[2], [int(value) for value in sys.argv[3:5]])
    elif sys.argv[1:2] == ["--forge-acquisition"]:
        forge_acquisition(json.loads(sys.argv[2]))
    else:
        raise SystemExit(2)
