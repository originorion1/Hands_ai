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
    INSTRUMENT_OPERATIONS,
    MAX_FRAME,
    authenticate,
    decode,
    digest,
    exact,
    grant_from,
    is_erpnext_candidate,
    metadata_grant_from,
    private_bytes,
    transition_policy_from,
    transition_record_config,
)
from .deployment_profile import (
    ENTRYPOINT_MODULE,
    profile_sha256,
    validate_profile,
    validate_secret_layout,
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
    validate_protected_paths,
)
from .journal import JournalDenied
from .progress_witness import (
    ENROLLMENT_FILENAME,
    ProgressWitness,
    enrollment_path,
    progress_state,
    stream_for,
    transition_initial_state,
    verify_enrollment_receipt,
    witness_contract,
)
from .readiness import release_report
from .semantic_runtime import validate_semantic_config

MODULE = "orion.pilot.services"
HOST_ENTRYPOINT = ENTRYPOINT_MODULE
HOST_WORKING_DIRECTORY = Path("/")
DENIED_LAUNCH_ENVIRONMENT = {"BASH_ENV", "CDPATH", "ENV", "SHELLOPTS"}


def validate_launch_invocation(
    operation, manifest_path, *, private_supervisor=False, baseline_fds=()
):
    """Require the one installed, isolated host launch before private input use."""
    if operation not in ("--enroll-witness", "--serve"):
        raise KernelUnavailable("approved runtime operation required")
    manifest_path = str(Path(manifest_path).resolve())
    executable = str(Path(sys.executable).absolute())
    expected = [executable, "-I", "-m", HOST_ENTRYPOINT]
    if private_supervisor:
        if (
            type(baseline_fds) is not tuple
            or len(baseline_fds) != 2
            or any(type(descriptor) is not int or descriptor < 0 for descriptor in baseline_fds)
        ):
            raise KernelUnavailable("exact namespace descriptors required")
        expected.extend(
            [
                "--private-supervisor",
                operation,
                manifest_path,
                "--baseline-net-fd",
                str(baseline_fds[0]),
                "--baseline-user-fd",
                str(baseline_fds[1]),
            ]
        )
    else:
        expected.extend((operation, manifest_path))
    environment_denied = any(
        name.startswith(("PYTHON", "LD_")) or name in DENIED_LAUNCH_ENVIRONMENT
        for name in os.environ
    )
    if (
        not sys.flags.isolated
        or sys.orig_argv != expected
        or Path.cwd() != HOST_WORKING_DIRECTORY
        or environment_denied
    ):
        raise KernelUnavailable("wheel-only launch controls required")
    return manifest_path


def artifact_identity():
    """Revalidate installed hashed RECORD files; not a signature or release grant."""
    distribution = importlib.metadata.distribution("orion-core")
    record = distribution.read_text("RECORD")
    if not record:
        raise ValueError("installed artifact RECORD required")
    prefix = Path(sys.prefix).absolute()
    resolved_prefix = prefix.resolve()
    running_module = Path(__file__).resolve()
    if not running_module.is_relative_to(resolved_prefix):
        raise ValueError("non-editable installed artifact required")
    verified = 0
    running_module_verified = False
    for filename, checksum, size in csv.reader(io.StringIO(record)):
        if not checksum:
            if not filename.endswith(("RECORD", ".pyc")):
                raise ValueError("unhashed installed artifact file")
            continue
        algorithm, separator, expected = checksum.partition("=")
        if not separator or algorithm != "sha256":
            raise ValueError("artifact hash policy required")
        installed_file = distribution.locate_file(filename).resolve()
        if not installed_file.is_relative_to(resolved_prefix):
            raise ValueError("installed artifact escaped its prefix")
        raw = installed_file.read_bytes()
        actual = base64.urlsafe_b64encode(hashlib.sha256(raw).digest()).rstrip(b"=").decode()
        if actual != expected or len(raw) != int(size):
            raise ValueError("installed artifact tampered")
        running_module_verified = running_module_verified or installed_file == running_module
        verified += 1
    if not running_module_verified:
        raise ValueError("executing module is not the verified artifact")
    return {
        "name": distribution.metadata["Name"],
        "version": distribution.version,
        "record_sha256": hashlib.sha256(record.encode()).hexdigest(),
        "verified_files": verified,
        "package_root": str(running_module.parents[1]),
        "installed_prefix": str(prefix),
        "interpreter": str(Path(sys.executable).absolute()),
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


def validate_deployment_inputs(value, *, manifest_path=None, enrollment=False):
    """Revalidate the installed profile and custody inputs without reading secrets."""
    artifact = artifact_identity()
    if artifact["record_sha256"] != value["artifact_record_sha256"]:
        raise ValueError("exact installed artifact required")
    if manifest_path is None:
        manifest_path = value["deployment_profile"]["entrypoint"]["manifest_path"]
    validate_profile(value["deployment_profile"], value, artifact, manifest_path)
    if value["deployment_profile_sha256"] != profile_sha256(
        value["deployment_profile"]
    ):
        raise ValueError("deployment profile digest required")
    private_directory(value["state_directory"])
    private_directory(value["keys_directory"])
    private_directory(value["witness_directory"])
    validate_protected_paths(
        value["state_directory"], value["keys_directory"], value["witness_directory"]
    )
    validate_secret_layout(value["deployment_profile"])
    if (
        hashlib.sha256(private_bytes(value["certificate"])).hexdigest()
        != value["certificate_sha256"]
    ):
        raise ValueError("pinned source trust required")
    if not enrollment:
        verify_enrollment_receipt(
            enrollment_path(value["state_directory"]), witness_contract(value)
        )
    return artifact


def load_manifest(path, *, enrollment=False):
    if not all(
        shutil.which(tool, path=LAB_PATH)
        for tool in ("unshare", "nsenter", "ip", "nft", "bwrap", "curl")
    ):
        raise KernelUnavailable("required installed runtime tools unavailable")
    raw = decode(private_bytes(path))
    value = exact(
        raw,
        (
            "version",
            "mode",
            "configs",
            "state_directory",
            "keys_directory",
            "witness_directory",
            "deployment_identity",
            "certificate",
            "certificate_sha256",
            "artifact_record_sha256",
            "host",
            "policy",
            "deployment_profile",
            "deployment_profile_sha256",
        )
        + (("semantic",) if type(raw) is dict and raw.get("version") in (2, 3) else ())
        + (("grant_transition",) if type(raw) is dict and raw.get("version") == 5 else ()),
    )
    if type(value["version"]) is not int or value["version"] not in (1, 2, 3, 4, 5):
        raise ValueError("explicit versioned deployment required")
    expected_mode = (
        "candidate_erpnext_post_discovery_read_only"
        if value["version"] == 5
        else "candidate_erpnext_read_only"
        if value["version"] == 4
        else "synthetic_read_only"
    )
    if value["mode"] != expected_mode:
        raise ValueError("explicit synthetic deployment only")
    if value["host"] not in (V4_APPROVED, V6_APPROVED):
        raise ValueError("fixed approved synthetic destination required")
    if type(value["configs"]) is not list or any(type(c) is not dict for c in value["configs"]):
        raise ValueError("separate canonical authorizations required")
    count = len(value["configs"])
    if (value["version"] in (1, 2, 4) and count != 2) or (
        value["version"] == 3 and not 3 <= count <= 10
    ) or (value["version"] == 5 and count != 1):
        raise ValueError("separate canonical authorizations required")
    operations = [c.get("operation") for c in value["configs"]]
    expected_prefix = ["metadata"] if value["version"] == 5 else ["metadata", "read"]
    if (any(type(op) is not str for op in operations)
            or operations[:len(expected_prefix)] != expected_prefix
            or len(set(operations)) != count
            or any(op not in INSTRUMENT_OPERATIONS for op in operations[len(expected_prefix):])):
        raise ValueError("metadata-first separate record configuration required")
    if value["version"] == 4:
        if (
            not all(is_erpnext_candidate(config) for config in value["configs"])
            or [config.get("protocol") for config in value["configs"]]
            != ["erpnext_metadata_v1", "erpnext_records_v1"]
        ):
            raise ValueError("exact ERPNext candidate protocols required")
    elif value["version"] == 5:
        metadata = value["configs"][0]
        policy = transition_policy_from(value["grant_transition"])
        grant = metadata_grant_from(metadata["grant"])
        if (
            not is_erpnext_candidate(metadata)
            or metadata.get("protocol") != "erpnext_metadata_v1"
            or (grant.request.tenant_id, grant.request.company, grant.request.source_id)
            != (policy["tenant_id"], policy["company"], policy["source_id"])
            or any(metadata.get(name) != policy[name] for name in (
                "caller", "secret_reference", "auth_reference"
            ))
        ):
            raise ValueError("metadata-only transition envelope mismatch")
    elif any(is_erpnext_candidate(config) for config in value["configs"]):
        raise ValueError("ERPNext candidate requires manifest version 4")
    if value["version"] in (2, 3):
        if type(value["semantic"]) is not dict or value["semantic"].get("version") != value["version"] - 1:
            raise ValueError("explicit compatible semantic configuration version required")
        if value["version"] == 3:
            validate_semantic_config(value["semantic"], configs=value["configs"])
        else:
            validate_semantic_config(value["semantic"])
    validate_deployment_inputs(value, manifest_path=path, enrollment=enrollment)
    # Policy validation occurs independently in the protected evidence owner.
    return value


def write_private(path, value):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, "w") as stream:
        stream.write(value)
        stream.flush()
        os.fsync(stream.fileno())


def enroll_witness(manifest):
    """Explicitly initialize local custody and its separately retained witness."""
    from .custody import AuditCustody
    from .evidence_custody import EvidenceCustody

    validate_deployment_inputs(manifest, enrollment=True)
    root, keys = Path(manifest["state_directory"]), Path(manifest["keys_directory"])
    for directory in (root / "audit", root / "evidence"):
        directory.mkdir(mode=0o700, exist_ok=True)
        private_directory(directory)
    audits = []
    for config in manifest["configs"]:
        directory = root / "audit" / config["operation"]
        directory.mkdir(mode=0o700, exist_ok=True)
        private_directory(directory)
        audits.append(
            (
                config,
                AuditCustody(directory, private_bytes(keys / "audit-signing"), config),
            )
        )
    evidence = EvidenceCustody(
        root / "evidence",
        private_bytes(keys / "evidence-signing"),
        manifest["configs"],
        policy=manifest["policy"],
        semantic_limit=100
        if manifest.get("semantic", {}).get("version") == 2
        else 1,
        transition=manifest.get("grant_transition"),
        deployment_identity=(
            manifest["deployment_identity"] if manifest.get("version") == 5 else None
        ),
    )
    states = []
    for config, owner in audits:
        sequence, head = owner.journal.progress()
        states.append(
            progress_state(
                stream_for(
                    manifest["configs"],
                    "audit",
                    digest(config),
                    transition=manifest.get("grant_transition"),
                ),
                sequence,
                head,
            )
        )
    with evidence._connect() as database:
        events, _, _, _ = evidence._check(database)
    states.append(
        progress_state(
            stream_for(
                manifest["configs"],
                "evidence",
                transition=manifest.get("grant_transition"),
            ),
            len(events),
            evidence.head,
        )
    )
    if manifest.get("version") == 5:
        states.append(
            transition_initial_state(
                manifest["configs"], manifest["grant_transition"]
            )
        )
    witness = ProgressWitness.enroll(
        manifest["witness_directory"],
        private_bytes(keys / "witness-signing"),
        witness_contract(manifest),
        states,
        root,
    )
    status = witness.dispatch("owner", "status", None)
    return dict(
        status,
        status="enrolled",
        rollback_set=[str(root / "audit"), str(root / "evidence")],
        witness_storage=str(Path(manifest["witness_directory"])),
        LIVE_PILOT_READY=False,
        execution_allowed=False,
    )


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
        self.witness_root = Path(manifest["witness_directory"])
        validate_protected_paths(self.root, self.keys, self.witness_root)
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
        self.enrolled_configs = manifest["configs"]
        self.configs = list(self.enrolled_configs)
        self.grant_transition = manifest.get("grant_transition")
        self.transition_state = None
        self.endpoints = {}
        self.cap_files = {}
        import secrets

        # Fresh role capabilities on each controller start; no persisted authority.
        self.caps = {
            name: secrets.token_hex(32).encode()
            for name in (
                "owner",
                "witness-owner",
                "witness-audit",
                "witness-evidence",
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
        for role in ("witness", "audit", "evidence", "authorization", "gateway", "acquisition"):
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
        boot = {
            "role": role,
            "parent": self.parent,
            "policy": self.manifest["policy"],
            "enrolled_configs": self.enrolled_configs,
        }
        if self.grant_transition is not None:
            boot.update(
                grant_transition=self.grant_transition,
                deployment_identity=self.manifest["deployment_identity"],
            )
        if role == "audit" and self.transition_state is not None:
            boot["transition_state"] = self.transition_state
        role_keys = {}
        if role == "witness":
            role_keys = {
                "owner": self.caps["witness-owner"],
                "audit": self.caps["witness-audit"],
                "evidence": self.caps["witness-evidence"],
            }
            readonly.append((self.keys / "witness-signing", "/private/signing-key"))
            readonly.append(
                (self.root / ENROLLMENT_FILENAME, "/private/witness-enrollment")
            )
            writable.append((self.witness_root, "/state"))
            boot["witness_contract"] = witness_contract(self.manifest)
        elif role == "audit":
            role_keys = {
                "supervisor": self.caps["audit"],
                "witness": self.caps["witness-audit"],
                "owner": self.caps["owner"],
            }
            readonly.append((self.keys / "audit-signing", "/private/signing-key"))
            readonly += [
                (self.endpoints["witness"].parent, "/witness"),
            ]
            writable.append((self.root / "audit", "/state"))
        elif role == "evidence":
            role_keys = {
                "supervisor": self.caps["evidence"],
                "owner": self.caps["owner"],
                "witness": self.caps["witness-evidence"],
            }
            readonly.append((self.keys / "evidence-signing", "/private/signing-key"))
            readonly.append((self.endpoints["witness"].parent, "/witness"))
            writable.append((self.root / "evidence", "/state"))
            if self.manifest.get("version") in (2, 3):
                boot["semantic"] = validate_semantic_config(self.manifest["semantic"])
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
            role_keys = {
                "acquisition": self.caps["gateway"],
                "owner": self.caps["owner"],
            }
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
            role_keys = {
                "reasoner": self.caps["reasoner"],
                "owner": self.caps["owner"],
            }
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
            failure = JournalDenied("independent service startup denied")
            failure.health_boundary = role + "_startup"
            raise failure
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
            if self.grant_transition is None:
                roles = ("witness", "audit", "evidence", "authorization", "gateway", "acquisition")
                for role in roles:
                    self.service(role)
            else:
                self.service("witness")
                self.service("evidence")
                self._restore_transition_config()
                for role in ("audit", "authorization", "gateway", "acquisition"):
                    self.service(role)
            health = self.health()
            if health["status"] == "blocked":
                failure = JournalDenied("startup custody health denied")
                failure.health_boundary = health.get("failure_boundary", "custody_status")
                failure.dead_roles = health.get("dead_roles", [])
                raise failure
            self.monitor = threading.Thread(target=self.supervise, daemon=True)
            self.monitor.start()
            result = {
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
                "deployment_profile_version": self.manifest["deployment_profile"]["version"],
                "deployment_profile_sha256": self.manifest["deployment_profile_sha256"],
                "health": health,
                "LIVE_PILOT_READY": False,
                "execution_allowed": False,
            }
            if self.manifest.get("version") in (2, 3):
                result["semantic_assessment"] = self.semantic("restore")
            return result
        except Exception:
            self.close()
            raise

    def owner(self, action, value=None):
        return rpc(self.endpoints["authorization"], "owner", self.caps["owner"], action, value)

    def _set_transition_config(self, config, state):
        if config is None:
            self.transition_state = state
            return
        transition_record_config(config, self.grant_transition)
        if len(self.configs) != 1 or self.configs[0] != self.enrolled_configs[0]:
            raise JournalDenied("grant transition configuration conflict")
        self.configs = [self.enrolled_configs[0], config]
        self.transition_state = state
        path = self.session / "configs-generation-1"
        if not path.exists():
            write_private(path, _json(self.configs))
        elif decode(private_bytes(path)) != self.configs:
            raise JournalDenied("grant transition session configuration changed")
        self.config_file = path

    def _restore_transition_config(self):
        state = rpc(
            self.endpoints["evidence"],
            "owner",
            self.caps["owner"],
            "transition",
            {"binding": digest(self.enrolled_configs[0]), "arguments": {}},
        )
        self._set_transition_config(state.get("config"), state)
        return state

    def transition_challenge(self):
        """Expose retained discovery lineage to the trusted controller only."""
        with self.transition:
            if self.grant_transition is None or self.transition_state is None:
                raise JournalDenied("grant transition unavailable")
            if self.transition_state.get("generation") != 0:
                raise JournalDenied("grant transition already committed")
            if self.health().get("status") != "healthy":
                raise JournalDenied("healthy unprovisioned custody required")
            witness = rpc(
                self.endpoints["witness"],
                "owner",
                self.caps["witness-owner"],
                "snapshot",
                None,
            )
            challenge = rpc(
                self.endpoints["evidence"],
                "owner",
                self.caps["owner"],
                "transition_challenge",
                {"binding": digest(self.enrolled_configs[0]), "arguments": {}},
            )
            return dict(
                challenge,
                expected_witness_sha256=witness["sha256"],
                status="approval_required",
                execution_allowed=False,
                LIVE_PILOT_READY=False,
            )

    def provision(self, request):
        """Commit one controller-authenticated transition through existing owners."""
        with self.transition:
            try:
                if self.grant_transition is None or self.transition_state is None:
                    raise JournalDenied("grant transition unavailable")
                if self.transition_state.get("generation") != 0:
                    raise JournalDenied("grant transition already committed")
                if self.health().get("status") != "healthy":
                    raise JournalDenied("healthy unprovisioned custody required")
                witness = rpc(
                    self.endpoints["witness"],
                    "owner",
                    self.caps["witness-owner"],
                    "snapshot",
                    None,
                )
                if request.get("expected_witness_sha256") != witness["sha256"]:
                    raise JournalDenied("stale grant transition witness denied")

                prepared = self.owner("transition_prepare", request)
                evidence = rpc(
                    self.endpoints["evidence"],
                    "owner",
                    self.caps["owner"],
                    "provision",
                    {
                        "binding": digest(self.enrolled_configs[0]),
                        "arguments": {"transition": request},
                    },
                )
                state = self._restore_transition_config()
                audit = rpc(
                    self.endpoints["audit"],
                    "owner",
                    self.caps["owner"],
                    "provision",
                    {
                        "config": request["config"],
                        "audit_provision": state["audit_provision"],
                    },
                )
                authorization = self.owner("transition_commit", request)
                gateway = rpc(
                    self.endpoints["gateway"],
                    "owner",
                    self.caps["owner"],
                    "provision",
                    request["config"],
                )
                acquisition = rpc(
                    self.endpoints["acquisition"],
                    "owner",
                    self.caps["owner"],
                    "provision",
                    request["config"],
                )
                binding = prepared["binding"]
                reference = prepared["transition_reference"]
                if (
                    prepared.get("status") != "validated_no_authority"
                    or evidence.get("status") != "evidence_committed"
                    or evidence.get("binding") != binding
                    or evidence.get("transition_reference") != reference
                    or state.get("generation") != 1
                    or state.get("config") != request["config"]
                    or state.get("audit_provision", {}).get("transition_reference")
                    != reference
                    or audit.get("status") != "audit_committed"
                    or audit.get("binding") != binding
                    or authorization.get("status") != "provisioned_unarmed"
                    or authorization.get("generation") != 1
                    or authorization.get("binding") != binding
                    or gateway != {"status": "gateway_committed", "binding": binding}
                    or acquisition
                    != {"status": "acquisition_committed", "binding": binding}
                ):
                    raise JournalDenied("grant transition acknowledgement mismatch")
                health = self.health()
                if (
                    health.get("status") != "healthy"
                    or health.get("record_authority_provisioned") is not True
                    or health.get("record_authority_armed") is not False
                ):
                    raise JournalDenied("unarmed grant transition health denied")
                return {
                    "status": "provisioned_unarmed",
                    "generation": 1,
                    "binding": binding,
                    "transition_reference": reference,
                    "commits": {
                        "evidence": evidence["head"],
                        "audit": audit["head"],
                        "authorization": authorization["status"],
                        "gateway": gateway["status"],
                        "acquisition": acquisition["status"],
                    },
                    "health": health,
                    "execution_allowed": False,
                    "LIVE_PILOT_READY": False,
                }
            except Exception:
                self._cutoff()
                raise

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
            health_boundary = "progress_witness"
            witness = rpc(
                self.endpoints["witness"],
                "owner",
                self.caps["witness-owner"],
                "status",
                None,
            )
            health_boundary = "authorization_health"
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
                witness=witness,
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
        for operation in (c["operation"] for c in self.configs):
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
            validate_deployment_inputs(self.manifest)
        except Exception:  # noqa: BLE001 - changed/missing custody removes existing authority
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
            if self.grant_transition is None:
                for role in ("witness", "audit", "evidence", "authorization", "gateway", "acquisition"):
                    self.service(role)
            else:
                self.configs = list(self.enrolled_configs)
                self.config_file = self.session / "configs"
                self.transition_state = None
                self.service("witness")
                self.service("evidence")
                self._restore_transition_config()
                for role in ("audit", "authorization", "gateway", "acquisition"):
                    self.service(role)
            health = self.health()
            if health["status"] == "blocked":
                return self._cutoff()
            result = {
                "status": "unarmed",
                "health": health,
                "deployment_profile_version": self.manifest["deployment_profile"]["version"],
                "deployment_profile_sha256": self.manifest["deployment_profile_sha256"],
                "LIVE_PILOT_READY": False,
                "execution_allowed": False,
            }
            if self.manifest.get("version") in (2, 3):
                result["semantic_assessment"] = self.semantic("restore")
            return result
        except Exception:  # noqa: BLE001 - pending/tampered recovery has no reset path
            return self.cutoff()

    def reason(self, message):
        # This supervisor is independent of hostile application code. Existing
        # accepted request references cannot be republished by fabricating stdout.
        exact(message, ("operation", "grant_token", "request_id", "request"))
        config = next((c for c in self.configs if c["operation"] == message["operation"]), None)
        if config is None or type(message["request_id"]) is not str:
            raise JournalDenied("reasoning request binding denied")
        binding = digest(config)
        reference = digest((config["caller"], message["request_id"]))
        before = rpc(
            self.endpoints["evidence"], "owner", self.caps["owner"], "inspect",
            {"binding": binding, "arguments": {}},
        )
        if not before["available"] or any(
            c["request_reference"] == reference for c in before["checkpoints"]
        ) or self.health()["status"] == "blocked":
            raise JournalDenied("reasoning authority or replay denied")
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
        return self.validate_reasoner_response(message, value)

    def validate_reasoner_response(self, message, value):
        """Untrusted stdout is not admission, provenance, or custody attestation.

        Process diagnostics remain self-reported; the fixed OS composition is the
        containment boundary. Only protected, exact-request-bound canonical data
        can become output. No application-supplied business/control fields survive.
        """
        exact(value, ("response", "reasoner_checks"))
        names = {
            "user_separated", "pid_separated", "mnt_separated", "capabilities_dropped",
            "network_confined", "environment_cleared", "issuer_inaccessible",
            "signing-key_inaccessible", "credential_inaccessible", "worker-secret_inaccessible",
            "custody_storage_inaccessible",
        }
        checks = value["reasoner_checks"]
        if type(checks) is not dict or set(checks) != names or any(v is not True for v in checks.values()):
            raise JournalDenied("reasoning diagnostics denied")
        response = value["response"]
        if type(response) is not dict:
            raise JournalDenied("reasoning response denied")
        if response.get("status") != "admitted":
            return {"reasoner_checks": checks, "response": {
                "status": "denied", "execution_allowed": False, "allow_live_customer_access": False,
            }}
        config = next((c for c in self.configs if c["operation"] == message.get("operation")), None)
        if config is None:
            raise JournalDenied("reasoning operation denied")
        checkpoint = exact(response.get("checkpoint"), ("checkpoint", "head", "payload_sha256"))
        resolved = rpc(
            self.endpoints["evidence"], "owner", self.caps["owner"], "resolve",
            {"binding": digest(config), "arguments": {
                "checkpoint": checkpoint["checkpoint"],
                "request_reference": digest((config["caller"], message.get("request_id"))),
            }},
        )
        if (
            resolved["request_sha256"] != digest(message)
            or checkpoint["head"] != resolved["head"]
            or checkpoint["payload_sha256"] != resolved["payload_sha256"]
            or response.get("head") != resolved["journal_head"]
            or response.get("observations") != resolved["observations"]
            or self.health()["status"] == "blocked"
        ):
            raise JournalDenied("independent admitted output verification denied")
        result = {"reasoner_checks": checks, "response": {
            "status": "admitted", "head": resolved["journal_head"], "checkpoint": checkpoint,
            "observations": resolved["observations"], "interpretation": "UNKNOWN",
            "interpretation_reason": "business_semantics_not_validated",
            "execution_allowed": False, "allow_live_customer_access": False,
        }}
        if getattr(self, "manifest", {}).get("version") in (2, 3):
            # A distinct, protected assessment; admitted transport remains admitted
            # even if semantic evaluation/storage is unavailable. No reasoner claims
            # or user-supplied archive/policy are forwarded to the evidence owner.
            result["response"]["semantic_assessment"] = self.semantic("evaluate")
        return result

    def semantic(self, mode):
        if self.manifest.get("version") not in (2, 3) or mode not in ("evaluate", "restore"):
            raise JournalDenied("fixed semantic deployment required")
        try:
            return rpc(self.endpoints["evidence"], "owner", self.caps["owner"],
                       "semantic", {"mode": mode})
        except Exception:  # noqa: BLE001 - never publish cached durable conclusions
            return {"status": "UNAVAILABLE", "epistemic_status": "UNKNOWN", "durable": False,
                    "reason": "semantic_custody_unavailable", "authority_restored": False,
                    "execution_allowed": False, "LIVE_PILOT_READY": False}

    def dispatch(self, value):
        command = value.get("command")
        if command == "health":
            return self.health()
        if command == "arm":
            return self.control(value["operation"], "arm")
        if command == "read":
            return self.reason(value["message"])
        if command == "transition_challenge":
            exact(value, ("command",))
            return self.transition_challenge()
        if command == "provision":
            exact(value, ("command", "transition"))
            return self.provision(value["transition"])
        if command == "semantic":
            exact(value, ("command", "mode"))
            return self.semantic(value["mode"])
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
    parser.add_argument("--enroll-witness", metavar="PRIVATE_MANIFEST")
    parser.add_argument("--artifact", action="store_true")
    parser.add_argument("--health", action="store_true")
    parser.add_argument("--start", action="store_true")
    parser.add_argument("--private-supervisor", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--baseline-net-fd", type=int, help=argparse.SUPPRESS)
    parser.add_argument("--baseline-user-fd", type=int, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if args.enroll_witness:
        try:
            if args.serve or args.private_supervisor:
                raise ValueError("exclusive witness enrollment required")
            manifest_path = validate_launch_invocation(
                "--enroll-witness", args.enroll_witness
            )
            report = enroll_witness(
                load_manifest(manifest_path, enrollment=True)
            )
            print(_json(report), flush=True)
            return 0
        except Exception as error:  # noqa: BLE001 - no bootstrap input is printed
            boundary = traceback.extract_tb(error.__traceback__)[-1]
            print(
                _json(
                    {
                        "status": "BLOCKED",
                        "LIVE_PILOT_READY": False,
                        "execution_allowed": False,
                        "failure_type": type(error).__name__,
                        "failure_boundary": boundary.name,
                    }
                ),
                flush=True,
            )
            return 2
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
            manifest_path = validate_launch_invocation("--serve", args.serve)
            load_manifest(manifest_path)
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
                        manifest_path,
                        "--baseline-net-fd",
                        str(descriptors[0]),
                        "--baseline-user-fd",
                        str(descriptors[1]),
                    ],
                    close_fds=True,
                    pass_fds=descriptors,
                    cwd=HOST_WORKING_DIRECTORY,
                    env={"PATH": os.defpath},
                )
                for signum in (signal.SIGTERM, signal.SIGINT):
                    signal.signal(signum, lambda number, frame: process.send_signal(number))
                return process.wait()
            finally:
                for descriptor in descriptors:
                    os.close(descriptor)
        # Kernel namespace descriptors, never spoofable strings from config/env.
        manifest_path = validate_launch_invocation(
            "--serve",
            args.serve,
            private_supervisor=True,
            baseline_fds=(args.baseline_net_fd, args.baseline_user_fd),
        )
        protect_parent()
        # Close BEFORE loading keys or spawning custody; no host handles reach them.
        baseline_net = os.readlink("/proc/self/fd/" + str(args.baseline_net_fd))
        baseline_user = os.readlink("/proc/self/fd/" + str(args.baseline_user_fd))
        if not baseline_net.startswith("net:[") or not baseline_user.startswith("user:["):
            raise KernelUnavailable("kernel namespace baseline required")
        os.close(args.baseline_net_fd)
        os.close(args.baseline_user_fd)
        manifest = load_manifest(manifest_path)
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
