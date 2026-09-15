"""Non-activating installed supervisor for one bounded synthetic read-only runtime.

Operator-provided private manifests/keys are deployment inputs, not test fixtures.
The source is external and unmodified. Startup requires a private rootless fabric,
verified installed files, independently protected custody and a fixed kernel route.
"""

import argparse
import base64
import csv
import hashlib
import importlib.metadata
import io
import os
import select
import shutil
import signal
import stat
import subprocess
import sys
import threading
import traceback
from pathlib import Path

from ..contracts import utc_now
from ..understanding.role_checkpoint import _json
from .broker_contract import (
    MAX_FRAME,
    authenticate,
    decode,
    digest,
    exact,
    grant_from,
    metadata_grant_from,
    private_bytes,
)
from .ipc import rpc
from .isolation import (
    LAB_PATH,
    V4_APPROVED,
    V6_APPROVED,
    Fabric,
    KernelUnavailable,
    process_command,
    protect_parent,
)
from .journal import JournalDenied
from .readiness import release_report

MODULE = "orion.pilot.services"


def artifact_identity():
    """Revalidate installed hashed RECORD files; not a signature or release grant."""
    distribution = importlib.metadata.distribution("orion-core")
    record = distribution.read_text("RECORD")
    if not record:
        raise ValueError("installed artifact RECORD required")
    verified = 0
    for filename, checksum, size in csv.reader(io.StringIO(record)):
        if not checksum:
            if not filename.endswith(("RECORD", ".pyc")):
                raise ValueError("unhashed installed artifact file")
            continue
        algorithm, separator, expected = checksum.partition("=")
        if not separator or algorithm != "sha256":
            raise ValueError("artifact hash policy required")
        raw = distribution.locate_file(filename).read_bytes()
        actual = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
        if actual != expected or len(raw) != int(size):
            raise ValueError("installed artifact tampered")
        verified += 1
    return {
        "name": distribution.metadata["Name"],
        "version": distribution.version,
        "record_sha256": hashlib.sha256(record.encode()).hexdigest(),
        "verified_files": verified,
        "package_root": str(Path(__file__).resolve().parents[1]),
        "execution_allowed": False,
        "LIVE_PILOT_READY": False,
    }


def private_directory(path):
    path = Path(path)
    info = path.lstat()
    if (
        not stat.S_ISDIR(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) & 0o077
    ):
        raise ValueError("private deployment directory required")
    return path


def load_manifest(path):
    if not all(
        shutil.which(tool, path=LAB_PATH)
        for tool in ("unshare", "nsenter", "ip", "nft", "bwrap", "curl")
    ):
        raise KernelUnavailable("required installed runtime tools unavailable")
    value = exact(
        decode(private_bytes(path)),
        (
            "version",
            "mode",
            "configs",
            "state_directory",
            "keys_directory",
            "certificate",
            "certificate_sha256",
            "artifact_record_sha256",
            "host",
            "policy",
        ),
    )
    if value["version"] != 1 or value["mode"] != "synthetic_read_only":
        raise ValueError("explicit synthetic deployment only")
    if value["host"] not in (V4_APPROVED, V6_APPROVED):
        raise ValueError("fixed approved synthetic destination required")
    if type(value["configs"]) is not list or len(value["configs"]) != 2:
        raise ValueError("separate canonical authorizations required")
    if [c.get("operation") for c in value["configs"]] != ["metadata", "read"]:
        raise ValueError("metadata-first separate record configuration required")
    private_directory(value["state_directory"])
    private_directory(value["keys_directory"])
    if (
        hashlib.sha256(private_bytes(value["certificate"])).hexdigest()
        != value["certificate_sha256"]
    ):
        raise ValueError("pinned source trust required")
    if artifact_identity()["record_sha256"] != value["artifact_record_sha256"]:
        raise ValueError("exact installed artifact required")
    # Policy validation occurs independently in the protected evidence owner.
    return value


def write_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def receive(process, *, timeout=12):
    if not select.select([process.stdout], [], [], timeout)[0]:
        raise JournalDenied("supervised process startup deadline")
    raw = process.stdout.readline(MAX_FRAME + 1)
    if len(raw) > MAX_FRAME or not raw.endswith("\n"):
        raise JournalDenied("supervised process framing denied")
    return decode(raw.encode())


def terminate(process):
    if process is not None and process.poll() is None:
        process.kill()
        process.wait(timeout=5)


class Deployment:
    """Trusted operator supervisor; not exposed inside acquisition or reasoning."""

    def __init__(self, manifest, *, baseline_net, baseline_user):
        self.manifest = manifest
        self.root, self.keys = Path(manifest["state_directory"]), Path(manifest["keys_directory"])
        self.processes, self.checks = {}, {}
        self.blocked = False
        self.egress_removed = False
        self.transition = threading.RLock()
        self.monitor_stop = threading.Event()
        self.monitor = None
        self.fabric = None
        self.source = self.gateway = None
        self.host_net = baseline_net
        initial_user = baseline_user
        self.baseline_user = baseline_user
        mapping = Path("/proc/self/uid_map").read_text().split()
        if (
            os.getuid() != 0
            or len(mapping) != 3
            or mapping[0] != "0"
            or mapping[2] != "1"
            or os.readlink("/proc/self/ns/user") == initial_user
            or os.readlink("/proc/self/ns/net") == self.host_net
        ):
            raise KernelUnavailable("private rootless operator namespaces required")
        self.parent = {
            kind: os.readlink("/proc/self/ns/" + kind) for kind in ("user", "pid", "mnt", "net")
        }
        self.parent["host_net"] = self.host_net
        self.configs = manifest["configs"]
        self.endpoints = {}
        self.cap_files = {}
        import secrets

        # Fresh role capabilities on each controller start; no persisted authority.
        self.caps = {
            name: secrets.token_hex(32).encode()
            for name in (
                "owner",
                "audit",
                "evidence",
                "auth-broker",
                "auth-source",
                "gateway",
                "reasoner",
            )
        }
        self.session = self.root / ("session-" + secrets.token_hex(12))
        self.session.mkdir(mode=0o700)
        self.config_file = self.session / "configs"
        write_private(self.config_file, _json(self.configs))
        for name, key in self.caps.items():
            self.cap_files[name] = self.session / (name + "-key")
            write_private(self.cap_files[name], key.decode())
        self.operator_file, self.reasoning_file = (
            self.session / "operator.json",
            self.session / "reasoner.json",
        )
        write_private(self.operator_file, _json({"owner": self.caps["owner"].decode()}))
        write_private(self.reasoning_file, _json({"reasoner": self.caps["reasoner"].decode()}))
        for role in ("audit", "evidence", "authorization", "gateway", "acquisition"):
            directory = self.session / role
            directory.mkdir(mode=0o700)
            self.endpoints[role] = directory / "service"
        for directory in (self.root / "audit", self.root / "evidence"):
            directory.mkdir(mode=0o700, exist_ok=True)
            private_directory(directory)
        for c in self.configs:
            directory = self.root / "audit" / c["operation"]
            directory.mkdir(mode=0o700, exist_ok=True)
            private_directory(directory)

    def service(self, role):
        endpoint = self.endpoints[role]
        if endpoint.exists():
            if not stat.S_ISSOCK(endpoint.lstat().st_mode):
                raise JournalDenied("service endpoint replacement denied")
            endpoint.unlink()  # Exact prior disposable socket, never custody history.
        readonly = [(self.config_file, "/private/configs")]
        writable = [(endpoint.parent, "/endpoint")]
        boot = {"role": role, "parent": self.parent, "policy": self.manifest["policy"]}
        role_keys = {}
        if role == "audit":
            role_keys = {"supervisor": self.caps["audit"]}
            readonly.append((self.keys / "audit-signing", "/private/signing-key"))
            writable.append((self.root / "audit", "/state"))
        elif role == "evidence":
            role_keys = {"supervisor": self.caps["evidence"], "owner": self.caps["owner"]}
            readonly.append((self.keys / "evidence-signing", "/private/signing-key"))
            writable.append((self.root / "evidence", "/state"))
        elif role == "authorization":
            role_keys = {
                "owner": self.caps["owner"],
                "broker": self.caps["auth-broker"],
                "source": self.caps["auth-source"],
            }
            readonly += [
                (self.keys / "issuer", "/private/issuer"),
                (self.keys / "worker-secret", "/private/worker-secret"),
                (self.cap_files["audit"], "/private/audit-capability"),
                (self.cap_files["evidence"], "/private/evidence-capability"),
                (self.endpoints["audit"].parent, "/audit"),
                (self.endpoints["evidence"].parent, "/evidence"),
            ]
        elif role == "gateway":
            role_keys = {"acquisition": self.caps["gateway"]}
            readonly += [
                (self.keys / "source-credential", "/private/credential"),
                (self.cap_files["auth-source"], "/private/authorization-capability"),
                (self.endpoints["authorization"].parent, "/authorization"),
                (self.manifest["certificate"], "/private/certificate"),
            ]
            boot.update(
                expected_net=self.gateway.private_net,
                host=self.manifest["host"],
                certificate_sha256=self.manifest["certificate_sha256"],
            )
        elif role == "acquisition":
            role_keys = {"reasoner": self.caps["reasoner"]}
            readonly += [
                (self.cap_files["auth-broker"], "/private/authorization-capability"),
                (self.cap_files["gateway"], "/private/gateway-capability"),
                (self.endpoints["authorization"].parent, "/authorization"),
                (self.endpoints["gateway"].parent, "/gateway"),
            ]
        else:
            raise JournalDenied("fixed role required")
        keyfile = self.session / (role + "-capabilities")
        if not keyfile.exists():
            write_private(keyfile, _json({k: v.decode() for k, v in role_keys.items()}))
        readonly.append((keyfile, "/private/capabilities"))
        command = process_command(
            MODULE, readonly=readonly, writable=writable, network=role == "gateway"
        )
        if role == "gateway":
            self.fabric.boot(self.gateway, command, boot)
            process = self.gateway
        else:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                close_fds=True,
                env={"PATH": LAB_PATH},
            )
            process.stdin.write(_json(boot) + "\n")
            process.stdin.flush()
        self.processes[role] = process
        ready = receive(process)
        if ready.get("ready") is not True or not all(ready["checks"].values()):
            raise JournalDenied("independent service startup denied")
        self.checks[role] = ready["checks"]

    def start(self):
        try:
            self.fabric = Fabric(module=MODULE)
            self.fabric.initial_user = self.baseline_user
            self.fabric.initialize()
            self.source, self.gateway = self.fabric.spawn("source"), self.fabric.spawn("broker")
            if self.manifest["host"] == V6_APPROVED and not self.fabric.ipv6:
                raise KernelUnavailable("required IPv6 unavailable")
            self.fabric.connect(self.source, self.gateway, self.manifest["host"])
            for role in ("audit", "evidence", "authorization", "gateway", "acquisition"):
                self.service(role)
            health = self.health()
            if health["status"] == "blocked":
                failure = JournalDenied("startup custody health denied")
                failure.health_boundary = health.get("failure_boundary", "custody_status")
                failure.dead_roles = health.get("dead_roles", [])
                raise failure
            self.monitor = threading.Thread(target=self.supervise, daemon=True)
            self.monitor.start()
            return {
                "status": "unarmed",
                "source_pid": self.source.pid,
                "control_pid": os.getpid(),
                "source_net": self.source.private_net,
                "gateway_pid": self.gateway.pid,
                "service_pids": {role: process.pid for role, process in self.processes.items()},
                "gateway_net": self.gateway.private_net,
                "ipv6_enabled": self.fabric.ipv6,
                "endpoints": {k: str(v) for k, v in self.endpoints.items()},
                "operator_capability_file": str(self.operator_file),
                "reasoning_capability_file": str(self.reasoning_file),
                "checks": self.checks,
                "artifact": artifact_identity(),
                "health": health,
                "LIVE_PILOT_READY": False,
                "execution_allowed": False,
            }
        except Exception:
            self.close()
            raise

    def owner(self, action, value=None):
        return rpc(self.endpoints["authorization"], "owner", self.caps["owner"], action, value)

    def control(self, operation, action):
        status = self.owner("status", operation)
        payload = {"control": action, "nonce": status["nonce"], "head": status["head"]}
        issuer = private_bytes(self.keys / "issuer")
        return self.owner(
            "control",
            {
                "operation": operation,
                "message": {**payload, "mac": authenticate(issuer, "control", payload)},
            },
        )

    def health(self):
        if self.blocked or any(p.poll() is not None for p in self.processes.values()):
            return {
                "status": "blocked",
                "LIVE_PILOT_READY": False,
                "execution_allowed": False,
                "failure_boundary": "supervised_process",
                "dead_roles": [role for role, p in self.processes.items() if p.poll() is not None],
            }
        health_boundary = "authorization_health"
        try:
            report = self.owner("health")
            archive = {}
            for c in self.configs:
                health_boundary = "evidence_inspect"
                archive[c["operation"]] = rpc(
                    self.endpoints["evidence"],
                    "owner",
                    self.caps["owner"],
                    "inspect",
                    {"binding": digest(c), "arguments": {}},
                )
            report = dict(
                report,
                evidence=archive,
                supervised_processes=len(self.processes),
                LIVE_PILOT_READY=False,
                source_status="UNKNOWN",
                source_probe_performed=False,
            )
            if report.get("acquisition_in_flight") and any(
                b["pending"] for b in report["budgets"].values()
            ):
                from datetime import datetime

                live = all(not b["stopped"] for b in report["budgets"].values())
                for config in self.configs:
                    grant = (
                        metadata_grant_from(config["grant"])
                        if config["operation"] == "metadata"
                        else grant_from(config["grant"])
                    )
                    expiry = (
                        grant.expires_at
                        if config["operation"] == "metadata"
                        else grant.window.expires_at
                    )
                    live = live and utc_now() < min(
                        expiry, datetime.fromisoformat(config["limits"]["expires_at"])
                    )
                # Normal outstanding reservations are busy, NOT missing custody.
                # Orphaned pending reservations still deny fresh owner construction.
                if live:
                    report.update(status="busy", custody_available=True)
            if not report["custody_available"] or not all(a["available"] for a in archive.values()):
                report["status"] = "blocked"
            return report
        except Exception:  # noqa: BLE001 - no custody outage has a healthy fallback
            return {
                "status": "blocked",
                "LIVE_PILOT_READY": False,
                "execution_allowed": False,
                "failure_boundary": health_boundary,
            }

    def cutoff(self):
        with self.transition:
            return self._cutoff()

    def _cutoff(self):
        removed = self.egress_removed
        if self.gateway is not None and self.gateway.poll() is None:
            try:
                self.fabric.in_net(
                    self.gateway, ["nft", "flush", "chain", "inet", "orion", "egress"]
                )
                # The installed output-hook default DROP remains after rule removal.
                removed = True
                self.egress_removed = True
            except Exception:  # noqa: BLE001 - no command failure is a policy witness
                removed = False
        # One failed kill cannot skip the other kill or durable stop controls.
        termination_failed = False
        for process in (self.gateway, self.processes.get("acquisition")):
            try:
                terminate(process)
            except Exception:  # noqa: BLE001 - report actual poll state, never assume death
                termination_failed = True
        self.blocked = True
        return {
            "status": "blocked",
            "termination_failed": termination_failed,
            "kernel_egress_removed": removed,
            "acquisition_terminated": self.processes.get("acquisition") is None
            or self.processes["acquisition"].poll() is not None,
            "gateway_terminated": self.gateway is None or self.gateway.poll() is not None,
            "LIVE_PILOT_READY": False,
            "execution_allowed": False,
        }

    def stop(self, action="stop"):
        with self.transition:
            return self._stop(action)

    def _stop(self, action):
        # Remove source I/O authority FIRST, even when durable custody is lost.
        report = self._cutoff()
        controls = {}
        for operation in ("metadata", "read"):
            try:
                controls[operation] = self.control(operation, action)["status"]
            except Exception:  # noqa: BLE001 - do not claim unavailable durability
                controls[operation] = "unavailable"
        report.update(
            status=("stopped" if action == "stop" else "revoked")
            if all(value != "unavailable" for value in controls.values())
            and report["kernel_egress_removed"]
            and report["acquisition_terminated"]
            and report["gateway_terminated"]
            else "blocked",
            durable_controls=controls,
        )
        return report

    def restart(self):
        with self.transition:
            return self._restart()

    def _restart(self):
        try:
            unchanged = (
                artifact_identity()["record_sha256"] == self.manifest["artifact_record_sha256"]
            )
        except Exception:  # noqa: BLE001 - changed/missing artifacts remove existing authority
            unchanged = False
        if not unchanged:
            return self._cutoff()
        # Restart processes, NOT journals/indices/budgets; no authority is reissued.
        for process in self.processes.values():
            terminate(process)
        # Exact peer in the already validated private fabric, never host interfaces.
        # A bounded native HTTPS child may retain its namespace for <=1 second.
        subprocess.run(
            ["/usr/sbin/ip", "link", "delete", "broker0"],
            capture_output=True,
            timeout=2,
            check=False,
        )
        self.gateway = self.fabric.spawn("broker")
        self.fabric.connect(self.source, self.gateway, self.manifest["host"])
        self.processes = {}
        self.blocked = False
        self.egress_removed = False
        try:
            for role in ("audit", "evidence", "authorization", "gateway", "acquisition"):
                self.service(role)
            health = self.health()
            if health["status"] == "blocked":
                return self._cutoff()
            return {"status": "unarmed", "health": health, "LIVE_PILOT_READY": False}
        except Exception:  # noqa: BLE001 - pending/tampered recovery has no reset path
            return self.cutoff()

    def reason(self, message):
        command = process_command(
            MODULE,
            readonly=[
                (self.cap_files["reasoner"], "/private/reasoning-capability"),
                (self.endpoints["acquisition"].parent, "/acquisition"),
            ],
        )
        process = subprocess.run(
            command,
            input=_json({"role": "reasoner", "parent": self.parent, "message": message}) + "\n",
            capture_output=True,
            text=True,
            timeout=15,
            close_fds=True,
            env={"PATH": LAB_PATH},
            check=False,
        )
        if process.returncode or process.stderr:
            raise JournalDenied("reasoning process denied")
        value = decode(process.stdout.encode())
        if not all(value["reasoner_checks"].values()):
            raise JournalDenied("reasoning isolation failed")
        return value

    def dispatch(self, value):
        command = value.get("command")
        if command == "health":
            return self.health()
        if command == "arm":
            return self.control(value["operation"], "arm")
        if command == "read":
            return self.reason(value["message"])
        if command == "restart":
            return self.restart()
        if command in ("stop", "revoke"):
            return self.stop(command)
        if command == "cutoff":
            return self.cutoff()
        if command == "fail" and value.get("role") in ("audit", "authorization", "evidence"):
            terminate(self.processes[value["role"]])
            return self.cutoff()
        raise JournalDenied("fixed private operator command required")

    def close(self):
        self.monitor_stop.set()
        if self.monitor is not None:
            self.monitor.join(timeout=10)
        for process in self.processes.values():
            terminate(process)
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream and not stream.closed:
                    stream.close()
        if self.fabric is not None:
            self.fabric.close()

    def supervise(self):
        """Bounded independent polling continues while operator reads block."""
        while not self.monitor_stop.wait(0.5):
            if not self.transition.acquire(blocking=False):
                continue  # Startup/restart/stop owns the explicit transition.
            try:
                if not self.blocked and self.health()["status"] == "blocked":
                    self._cutoff()
            except Exception:  # noqa: BLE001 - supervision has no healthy fallback
                self.blocked = True
                terminate(self.gateway)
                terminate(self.processes.get("acquisition"))
            finally:
                self.transition.release()


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Installed synthetic-only runtime; never activate access."
    )
    parser.add_argument("--serve", metavar="PRIVATE_MANIFEST")
    parser.add_argument("--artifact", action="store_true")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--private-supervisor", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--baseline-net-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--baseline-user-fd", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not args.serve:
        try:
            report = (
                artifact_identity()
                if args.artifact
                else dict(release_report(), deployed=False, LIVE_PILOT_READY=False)
            )
            print(_json(report), flush=True)
            return 0 if args.artifact else 2
        except Exception:  # noqa: BLE001 - fail closed on missing/modified artifact
            print(
                _json(
                    {
                        "status": "startup_denied",
                        "LIVE_PILOT_READY": False,
                        "execution_allowed": False,
                    }
                ),
                flush=True,
            )
            return 2
    deployment = None
    try:
        if not args.private_supervisor:
            load_manifest(args.serve)
            mapping = Path("/proc/self/uid_map").read_text().split()
            if os.getuid() == 0 and (len(mapping) != 3 or mapping[2] != "1"):
                raise KernelUnavailable("unprivileged operator required")
            capability = subprocess.run(
                ["/usr/bin/unshare", "--user", "--map-root-user", "--net", "/usr/bin/true"],
                capture_output=True,
                timeout=5,
                check=False,
            )
            if capability.returncode:
                raise KernelUnavailable("required rootless kernel capabilities unavailable")
            descriptors = [
                os.open("/proc/self/ns/" + kind, os.O_RDONLY) for kind in ("net", "user")
            ]
            try:
                process = subprocess.Popen(
                    [
                        "/usr/bin/unshare",
                        "--user",
                        "--map-root-user",
                        "--net",
                        sys.executable,
                        "-I",
                        "-m",
                        "orion.pilot.deployment",
                        "--private-supervisor",
                        "--serve",
                        args.serve,
                        "--baseline-net-fd",
                        str(descriptors[0]),
                        "--baseline-user-fd",
                        str(descriptors[1]),
                    ],
                    close_fds=True,
                    pass_fds=descriptors,
                )
                for signum in (signal.SIGTERM, signal.SIGINT):
                    signal.signal(signum, lambda number, frame: process.send_signal(number))
                return process.wait()
            finally:
                for descriptor in descriptors:
                    os.close(descriptor)
        # Kernel namespace descriptors, never spoofable strings from config/env.
        protect_parent()
        # Close BEFORE loading keys or spawning custody; no host handles reach them.
        baseline_net = os.readlink("/proc/self/fd/" + str(args.baseline_net_fd))
        baseline_user = os.readlink("/proc/self/fd/" + str(args.baseline_user_fd))
        if not baseline_net.startswith("net:[") or not baseline_user.startswith("user:["):
            raise KernelUnavailable("kernel namespace baseline required")
        os.close(args.baseline_net_fd)
        os.close(args.baseline_user_fd)
        manifest = load_manifest(args.serve)
        deployment = Deployment(manifest, baseline_net=baseline_net, baseline_user=baseline_user)
        signal.signal(signal.SIGTERM, lambda *unused: (_ for _ in ()).throw(KeyboardInterrupt()))
        signal.signal(signal.SIGINT, lambda *unused: (_ for _ in ()).throw(KeyboardInterrupt()))
        print(_json(deployment.start()), flush=True)
        while True:
            if select.select([sys.stdin], [], [], 0.2)[0]:
                raw = sys.stdin.buffer.readline(MAX_FRAME + 1)
                if not raw:
                    break
                command = decode(raw)
                if command == {"command": "shutdown"}:
                    break
                try:
                    response = deployment.dispatch(command)
                except Exception:  # noqa: BLE001 - never print private contract values
                    response = {"status": "denied", "LIVE_PILOT_READY": False}
                print(_json(response), flush=True)
        return 0
    except (Exception, KeyboardInterrupt) as error:  # noqa: BLE001 - fixed startup/stop denial
        boundary = traceback.extract_tb(error.__traceback__)[-1]
        print(
            _json(
                {
                    "status": "BLOCKED",
                    "LIVE_PILOT_READY": False,
                    "execution_allowed": False,
                    "failure_type": type(error).__name__,
                    "failure_boundary": boundary.name,
                    "failure_line": boundary.lineno,
                    "kernel_operation": getattr(error, "operation", None),
                    "health_boundary": getattr(error, "health_boundary", None),
                    "dead_roles": getattr(error, "dead_roles", []),
                }
            ),
            flush=True,
        )
        return 2
    finally:
        if deployment is not None:
            try:
                deployment.stop()
            finally:
                deployment.close()


if __name__ == "__main__":
    raise SystemExit(main())
