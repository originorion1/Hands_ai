"""Installed local IPC authentication and canonical namespace composition."""

import ctypes
import hashlib
import json
import multiprocessing
import os
import select
import shutil
import signal
import stat
import subprocess
import sys
import threading
import time
import venv
from multiprocessing.connection import Client, Listener

import pytest

from orion.pilot.broker_contract import MAX_FRAME
from orion.pilot.ipc import Endpoint, receive, rpc, send
from orion.pilot.isolation import (
    LAB_PATH,
    PORT,
    V4_APPROVED,
    Fabric,
    KernelUnavailable,
    ambient_mount_roots,
    firewall,
    net_child,
    process_command,
    validate_protected_paths,
)
from orion.pilot.journal import JournalDenied

KEY = b"synthetic-capability-0000000000000"


@pytest.fixture
def mock_bwrap_composition(monkeypatch):
    from orion.pilot import isolation

    monkeypatch.setattr(isolation.shutil, "which", lambda *args, **kwargs: "/usr/bin/bwrap")


@pytest.fixture
def available_bubblewrap():
    executable = shutil.which("bwrap", path=LAB_PATH)
    if executable is None:
        pytest.skip("bubblewrap tool unavailable; kernel isolation NOT PROVEN")
    return executable


def test_installed_ipc_authenticated_call_and_private_endpoint(tmp_path):
    calls = []
    path = tmp_path / "custody"
    endpoint = Endpoint(
        path, {"supervisor": KEY}, lambda *args: calls.append(args) or {"accepted": True}
    )
    try:
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert rpc(path, "supervisor", KEY, "inspect", {"scope": "synthetic"}) == {"accepted": True}
        assert calls == [("supervisor", "inspect", {"scope": "synthetic"})]
    finally:
        endpoint.close()
    assert not path.exists()


def test_authenticated_rpc_long_filesystem_alias_uses_same_unix_socket_inode(tmp_path):
    calls = []
    directory = tmp_path / "ep"
    directory.mkdir(mode=0o700)
    short_path = directory / "service"
    alias = tmp_path / ("long-ancestor-" * 12)
    alias.symlink_to(tmp_path, target_is_directory=True)
    long_path = alias / "ep" / "service"
    assert len(os.fsencode(long_path)) > 108
    endpoint = Endpoint(short_path, {"supervisor": KEY}, lambda *args: calls.append(args) or True)
    try:
        assert short_path.stat().st_ino == long_path.stat().st_ino
        with pytest.raises(OSError):
            Client(str(long_path), family="AF_UNIX", authkey=None)
        assert rpc(long_path, "supervisor", KEY, "inspect", {"scope": "synthetic"}) is True
        with pytest.raises(JournalDenied):
            rpc(long_path, "supervisor", b"wrong-capability-" * 3, "inspect")
        assert calls == [("supervisor", "inspect", {"scope": "synthetic"})]
    finally:
        endpoint.close()


def test_checked_resolves_and_supplies_lab_path_without_inheriting_caller_path(monkeypatch):
    from orion.pilot import isolation

    monkeypatch.setenv("PATH", "/usr/bin")
    resolved, calls = [], []

    def which(tool, *, path):
        resolved.append((tool, path))
        return "/usr/sbin/ip"

    def run(argv, **options):
        calls.append((argv, options))
        return subprocess.CompletedProcess(argv, 0, "synthetic-routes", "")

    monkeypatch.setattr(isolation.shutil, "which", which)
    monkeypatch.setattr(isolation.subprocess, "run", run)
    assert isolation.checked(["ip", "-j", "route"]) == "synthetic-routes"
    assert resolved == [("ip", isolation.LAB_PATH)]
    assert calls == [
        (
            ["/usr/sbin/ip", "-j", "route"],
            {
                "input": None,
                "capture_output": True,
                "text": True,
                "timeout": 5,
                "check": False,
                "env": {"PATH": isolation.LAB_PATH, "LANG": "C.UTF-8"},
            },
        )
    ]
    assert os.environ["PATH"] == "/usr/bin"


def test_checked_classifies_kernel_command_timeout_as_unavailable(monkeypatch):
    from orion.pilot import isolation

    monkeypatch.setattr(isolation.shutil, "which", lambda *args, **kwargs: "/usr/sbin/ip")

    def timeout(argv, **options):
        raise subprocess.TimeoutExpired(argv, options["timeout"])

    monkeypatch.setattr(isolation.subprocess, "run", timeout)
    with pytest.raises(KernelUnavailable) as captured:
        isolation.checked(["ip", "link", "show", "fabric0"])
    assert captured.value.operation == ["ip", "link", "show"]


@pytest.mark.parametrize("operations", [("metadata", "read"), ("metadata", "read", "instrument_0")])
def test_emergency_stop_keeps_durable_controls_when_kernel_cutoff_command_fails(operations):
    from orion.pilot.deployment import Deployment
    from orion.pilot.isolation import KernelUnavailable

    class Process:
        def __init__(self):
            self.returncode = None

        def poll(self):
            return self.returncode

        def kill(self):
            self.returncode = -signal.SIGKILL

        def wait(self, *, timeout):
            assert timeout == 5
            return self.returncode

    class FabricFailure:
        def in_net(self, process, command):
            assert command == ["nft", "flush", "chain", "inet", "orion", "egress"]
            raise KernelUnavailable("synthetic kernel command unavailable")

    deployment = Deployment.__new__(Deployment)
    deployment.configs = [{"operation": operation} for operation in operations]
    deployment.transition = threading.RLock()
    deployment.egress_removed = False
    deployment.blocked = False
    deployment.gateway = Process()
    deployment.processes = {"acquisition": Process()}
    deployment.fabric = FabricFailure()
    controls = []

    def control(operation, action):
        controls.append((operation, action))
        return {"status": action}

    deployment.control = control
    assert deployment.stop() == {
        "status": "blocked",
        "kernel_egress_removed": False,
        "acquisition_terminated": True,
        "gateway_terminated": True,
        "termination_failed": False,
        "LIVE_PILOT_READY": False,
        "execution_allowed": False,
        "durable_controls": {operation: "stop" for operation in operations},
    }
    assert controls == [(operation, "stop") for operation in operations]
    assert deployment.blocked is True


@pytest.mark.parametrize("artifact_failure", ["exception", "mismatch"])
def test_restart_artifact_failure_cuts_off_existing_authority_before_reconstruction(
    monkeypatch, artifact_failure
):
    from orion.pilot import deployment as deployment_module

    runtime = deployment_module.Deployment.__new__(deployment_module.Deployment)
    runtime.transition = threading.RLock()
    runtime.manifest = {"artifact_record_sha256": "approved-artifact"}
    runtime.blocked = False
    cutoffs = []

    def identity():
        if artifact_failure == "exception":
            raise FileNotFoundError("synthetic installed artifact unavailable")
        return {"record_sha256": "changed-artifact"}

    def cutoff():
        cutoffs.append("existing-authority")
        runtime.blocked = True
        return {"status": "blocked", "LIVE_PILOT_READY": False}

    def forbidden(*args, **kwargs):
        pytest.fail("artifact failure must not reconstruct or launch services")

    runtime._cutoff = cutoff
    runtime.service = forbidden
    monkeypatch.setattr(deployment_module, "artifact_identity", identity)
    monkeypatch.setattr(deployment_module.subprocess, "run", forbidden)
    assert runtime.restart() == {"status": "blocked", "LIVE_PILOT_READY": False}
    assert cutoffs == ["existing-authority"]
    assert runtime.blocked is True


@pytest.mark.parametrize("role,key", [("unknown", KEY), ("supervisor", b"wrong" * 8)])
def test_unknown_roles_and_wrong_capabilities_denied_before_dispatch(tmp_path, role, key):
    calls = []
    endpoint = Endpoint(tmp_path / "custody", {"supervisor": KEY}, lambda *args: calls.append(args))
    try:
        with pytest.raises(JournalDenied):
            rpc(tmp_path / "custody", role, key, "issue")
        assert calls == []
    finally:
        endpoint.close()


def test_captured_authenticated_request_cannot_replay_on_new_connection(tmp_path):
    calls, captured = [], []
    path = tmp_path / "custody"
    endpoint = Endpoint(path, {"supervisor": KEY}, lambda *args: calls.append(args) or True)
    try:
        assert rpc(path, "supervisor", KEY, "inspect", capture=captured) is True
        with Client(str(path), family="AF_UNIX", authkey=None) as peer:
            assert receive(peer)["challenge"] != captured[0]["body"]["challenge"]
            send(peer, captured[0])
            assert receive(peer)["body"]["ok"] is False
        assert len(calls) == 1
    finally:
        endpoint.close()


def test_oversized_bytes_never_decode_or_dispatch(tmp_path):
    calls = []
    path = tmp_path / "custody"
    endpoint = Endpoint(path, {"supervisor": KEY}, lambda *args: calls.append(args))
    try:
        with Client(str(path), family="AF_UNIX", authkey=None) as peer:
            receive(peer)
            peer.send_bytes(b"x" * (MAX_FRAME + 1))
            assert receive(peer)["body"]["ok"] is False
        assert calls == []
    finally:
        endpoint.close()


def test_endpoint_requires_private_parent_and_denies_unavailable_custody(tmp_path):
    tmp_path.chmod(0o755)
    with pytest.raises(JournalDenied):
        Endpoint(tmp_path / "custody", {"supervisor": KEY}, lambda *args: True)
    with pytest.raises(JournalDenied):
        rpc(tmp_path / "absent", "supervisor", KEY, "inspect")


def test_unavailable_handshake_is_bounded_and_cannot_authorize(tmp_path, monkeypatch):
    from orion.pilot import ipc

    monkeypatch.setattr(ipc, "DEADLINE", 0.05)
    listener = Listener(str(tmp_path / "silent"), family="AF_UNIX", authkey=None)
    try:
        with pytest.raises(JournalDenied):
            rpc(tmp_path / "silent", "supervisor", KEY, "continue")
    finally:
        listener.close()


def test_promoted_kernel_policy_is_legacy_semantic_owner():
    from destination_network_lab import Fabric as LegacyFabric
    from destination_network_lab import firewall as legacy_firewall

    assert LegacyFabric is Fabric
    assert legacy_firewall is firewall
    policy = firewall(V4_APPROVED, ipv6=True)
    assert f"ip daddr {V4_APPROVED} tcp dport {PORT} counter name approved accept" in policy
    assert policy.count(" accept") == 1
    assert "policy drop" in policy
    assert "established" not in policy


def test_installed_process_uses_artifact_interpreter_without_pythonpath(mock_bwrap_composition):
    import sys

    command = process_command("orion.pilot.services")
    assert command[-5:] == [sys.executable, "-I", "-m", "orion.pilot.services", "--child"]
    assert "--unshare-all" in command
    assert "--clearenv" in command
    assert command[command.index("--cap-drop") + 1] == "ALL"
    assert not any("PYTHONPATH" in item or "tests/" in item for item in command)
    private = process_command("orion.pilot.services", network=True)
    assert "--share-net" not in private and "--unshare-all" not in private
    assert "--unshare-user" in private and "--unshare-pid" in private
    with pytest.raises(ValueError):
        process_command("untrusted.module")


def test_fabric_requires_exactly_one_trusted_child_entrypoint():
    assert Fabric(module="orion.pilot.services").module == "orion.pilot.services"
    with pytest.raises(ValueError):
        Fabric()
    with pytest.raises(ValueError):
        Fabric("legacy.py", module="orion.pilot.services")


def test_artifact_prefix_below_tmp_survives_private_tmpfs(
    tmp_path, monkeypatch, available_bubblewrap
):
    prefix = tmp_path / "clean-artifact"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(prefix)
    executable = prefix / "bin" / "python"
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(sys, "executable", str(executable))
    command = process_command("orion.pilot.services")
    assert command[0] == available_bubblewrap
    mounted = command.index(str(prefix))
    assert command[mounted - 1] == "--ro-bind"
    assert command.index("--tmpfs") < mounted
    # Only the test substitutes a fixed stdlib inspection for service startup;
    # installed ORION startup remains the actual artifact deployment probe.
    command[-5:] = [
        str(executable),
        "-I",
        "-c",
        "import json, pathlib, sqlite3, sys; print(json.dumps(sys.prefix))",
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    assert completed.returncode == 0, "artifact interpreter did not start in private namespace"
    assert json.loads(completed.stdout) == str(prefix)


@pytest.mark.parametrize("placement", ["inside", "ancestor", "same"])
def test_protected_roots_must_not_overlap_ambient_mounts(tmp_path, monkeypatch, placement):
    from orion.pilot import isolation

    ambient = tmp_path / "ambient"
    inside = ambient / "custody"
    inside.mkdir(parents=True)
    monkeypatch.setattr(isolation, "ambient_mount_roots", lambda: (ambient,))
    path = {"inside": inside, "ancestor": tmp_path, "same": ambient}[placement]
    with pytest.raises(KernelUnavailable):
        validate_protected_paths(path)


def test_protected_roots_are_resolved_private_siblings_not_symlink_aliases(tmp_path, monkeypatch):
    from orion.pilot import isolation

    ambient, safe = tmp_path / "ambient", tmp_path / "private"
    ambient.mkdir()
    safe.mkdir()
    nested = safe / "custody"
    nested.mkdir()
    monkeypatch.setattr(isolation, "ambient_mount_roots", lambda: (ambient,))
    assert validate_protected_paths(nested) == (nested.resolve(),)
    alias = tmp_path / "alias"
    alias.symlink_to(safe, target_is_directory=True)
    with pytest.raises(KernelUnavailable):
        validate_protected_paths(alias)
    with pytest.raises(KernelUnavailable):
        validate_protected_paths(alias / "custody")
    with pytest.raises(KernelUnavailable):
        validate_protected_paths(tmp_path / "absent")


def test_protected_custody_roots_must_be_mutually_disjoint(tmp_path, monkeypatch):
    from orion.pilot import isolation

    state = tmp_path / "state"
    witness = state / "witness"
    witness.mkdir(parents=True)
    monkeypatch.setattr(isolation, "ambient_mount_roots", lambda: ())
    with pytest.raises(KernelUnavailable, match="overlapping custody roots"):
        validate_protected_paths(state, witness)


def test_ambient_runtime_mounts_never_include_whole_host_root(monkeypatch):
    monkeypatch.setattr(sys, "base_prefix", "/")
    with pytest.raises(KernelUnavailable):
        ambient_mount_roots()


def test_synthetic_key_inside_venv_is_ambiently_readable_and_startup_placement_denied(
    tmp_path, monkeypatch, available_bubblewrap
):
    prefix = tmp_path / "artifact"
    venv.EnvBuilder(with_pip=False, symlinks=True).create(prefix)
    private = prefix / "protected-keys"
    private.mkdir(mode=0o700)
    key = private / "issuer"
    key.write_bytes(b"synthetic-private-key-canary")
    key.chmod(0o600)
    executable = prefix / "bin" / "python"
    monkeypatch.setattr(sys, "prefix", str(prefix))
    monkeypatch.setattr(sys, "executable", str(executable))
    command = process_command("orion.pilot.services")
    assert command[0] == available_bubblewrap
    command[-5:] = [
        str(executable),
        "-I",
        "-c",
        "import hashlib,pathlib,sys; print(hashlib.sha256(pathlib.Path(sys.argv[1]).read_bytes()).hexdigest())",
        str(key),
    ]
    completed = subprocess.run(command, capture_output=True, text=True, timeout=10, check=False)
    assert completed.returncode == 0, "ambient synthetic-key reproduction failed"
    assert completed.stdout.strip() == hashlib.sha256(key.read_bytes()).hexdigest()
    with pytest.raises(KernelUnavailable):
        validate_protected_paths(private)


def _parent_death_probe(result):
    """Dedicated subreaper keeps the actual killed grandchild from orphaning."""
    libc = ctypes.CDLL(None, use_errno=True)
    if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER, test cleanup only
        result.send({"error": "test_subreaper_unavailable"})
        return
    ready_read, ready_write = os.pipe()
    input_read, input_write = os.pipe()
    parent = os.fork()
    if parent == 0:
        child = os.fork()
        if child == 0:
            os.close(ready_read)
            os.dup2(input_read, 0)
            sys.stdout = os.fdopen(ready_write, "w")
            net_child()
            os._exit(3)
        os.close(ready_write)
        os.close(ready_read)
        while True:
            signal.pause()
    os.close(ready_write)
    child = None
    try:
        if not select.select([ready_read], [], [], 3)[0]:
            raise RuntimeError("holder readiness missing")
        ready = json.loads(os.read(ready_read, 4096))
        child = ready["pid"]
        os.kill(parent, signal.SIGKILL)
        os.waitpid(parent, 0)
        parent = None
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            waited, status = os.waitpid(child, os.WNOHANG)
            if waited:
                child = None
                result.send(
                    {
                        "signal": os.WTERMSIG(status) if os.WIFSIGNALED(status) else None,
                        "handshake": set(ready) == {"pid", "net"},
                    }
                )
                return
            time.sleep(0.01)
        result.send({"error": "holder_survived_parent_death"})
    finally:
        for pid in (parent, child):
            if pid is not None:
                try:
                    os.kill(pid, signal.SIGKILL)
                    os.waitpid(pid, 0)
                except ProcessLookupError:
                    pass
        for fd in (ready_read, input_read, input_write):
            os.close(fd)


def test_actual_namespace_holder_dies_when_supervisor_is_killed():
    context = multiprocessing.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    process = context.Process(target=_parent_death_probe, args=(sender,))
    process.start()
    sender.close()
    try:
        assert receiver.poll(8), "bounded parent-death probe did not report"
        assert receiver.recv() == {"signal": signal.SIGKILL, "handshake": True}
        process.join(timeout=1)
        assert process.exitcode == 0
    finally:
        if process.is_alive():
            process.kill()
            process.join(timeout=1)
        receiver.close()
