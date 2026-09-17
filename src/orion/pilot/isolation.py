"""Disposable rootless private fabric; never joins or configures host networking.

Only the trusted fixture setup owns CAP_NET_ADMIN in its new user namespace.
Its children start in distinct network namespaces before any listener or broker.
"""

import ctypes
import json
import os
import select
import shutil
import signal
import subprocess
import sys
from pathlib import Path

V4_APPROVED = "192.0.2.2"
V4_UNAPPROVED = "192.0.2.3"
V6_APPROVED = "fd42:6f72:696f::2"
V6_UNAPPROVED = "fd42:6f72:696f::3"
PORT = 44443
LAB_PATH = "/usr/sbin:/usr/bin:/sbin:/bin"
_SYSTEM_MOUNTS = tuple(Path(p) for p in ("/usr", "/lib", "/lib64", "/bin"))


class KernelUnavailable(Exception):
    """A missing kernel prerequisite cannot become an application-level proof."""


def ambient_mount_roots():
    """The exact read-only runtime trees exposed to every process role.

    Preserve lexical mount targets such as /lib, while protected-path checks
    compare their resolved locations. No caller-provided mount selectors.
    """
    roots = [directory for directory in _SYSTEM_MOUNTS if directory.exists()]
    for location in (Path(sys.base_prefix).resolve(), Path(sys.prefix).absolute()):
        if location == Path("/"):
            raise KernelUnavailable("whole filesystem runtime mount denied")
        if not location.is_relative_to(Path("/usr")) and location not in roots:
            roots.append(location)
    return tuple(roots)


def validate_protected_paths(*paths):
    """Deny custody roots exposed through any ambient interpreter/system tree.

    Both containment directions are unsafe: a private root cannot live inside an
    exposed tree or contain one. Reject symlink traversal before resolution so
    safe-looking lexical paths cannot alias an exposed location or be retargeted.
    Ownership and directory permissions remain the deployment owner's contract.
    """
    try:
        exposed = tuple(root.resolve(strict=True) for root in ambient_mount_roots())
        protected = []
        for value in paths:
            path = Path(value).absolute()
            if any(parent.is_symlink() for parent in (path, *path.parents)):
                raise KernelUnavailable("symlink custody placement denied")
            actual = path.resolve(strict=True)
            if not actual.is_dir() or any(
                actual.is_relative_to(root) or root.is_relative_to(actual) for root in exposed
            ):
                raise KernelUnavailable("ambient custody exposure denied")
            protected.append(actual)
        if any(
            left.is_relative_to(right) or right.is_relative_to(left)
            for index, left in enumerate(protected)
            for right in protected[index + 1 :]
        ):
            raise KernelUnavailable("overlapping custody roots denied")
        return tuple(protected)
    except OSError:
        raise KernelUnavailable("protected custody placement unavailable") from None


def checked(argv, *, input=None):
    argv = [shutil.which(argv[0], path=LAB_PATH) or argv[0], *argv[1:]]
    completed = subprocess.run(
        argv,
        input=input,
        capture_output=True,
        text=True,
        timeout=5,
        check=False,
        env={"PATH": LAB_PATH, "LANG": "C.UTF-8"},
    )
    if completed.returncode:
        failure = KernelUnavailable("private namespace operation unavailable")
        failure.operation = [Path(argv[0]).name, *argv[1:3]]
        raise failure
    return completed.stdout


def counter_for(name):
    return "deny_" + ("ipv4_unapproved" if name == "ipv4-mapped-ipv6" else name.replace("-", "_"))


def denied_tuples(ipv6):
    items = [
        ("ipv4-unapproved", "ip", V4_UNAPPROVED, PORT),
        ("ipv4-alternate-port", "ip", V4_APPROVED, PORT + 1),
        ("ipv4-proxy", "ip", V4_UNAPPROVED, PORT + 2),
        ("ipv4-other-family", "ip", V4_APPROVED, PORT),
    ]
    if ipv6:
        items.extend(
            [
                ("ipv6-unapproved", "ip6", V6_UNAPPROVED, PORT),
                ("ipv6-alternate-port", "ip6", V6_APPROVED, PORT + 1),
                ("ipv6-proxy", "ip6", V6_UNAPPROVED, PORT + 2),
                ("ipv6-other-family", "ip6", V6_APPROVED, PORT),
            ]
        )
    return items


def firewall(host, *, ipv6=False):
    if host not in (V4_APPROVED, V6_APPROVED) or (host == V6_APPROVED and not ipv6):
        raise ValueError("fixed synthetic destination required")
    family = "ip6" if ":" in host else "ip"
    # No broad established/related, loopback, DNS, UDP or IPv6 exception.
    # Permanent fixture neighbors remove any need to authorize ND egress.
    counters = "".join(
        f" counter {counter_for(name)} {{}}\n" for name, _, _, _ in denied_tuples(ipv6)
    )
    rules = "".join(
        f" {af} daddr {address} tcp dport {port} counter name {counter_for(name)}"
        " reject with icmpx type admin-prohibited\n"
        for name, af, address, port in denied_tuples(ipv6)
    )
    return (
        "table inet orion {\n counter approved {}\n counter denied {}\n"
        + counters
        + " chain egress {\n type filter hook output priority 0; policy drop;\n"
        f" {family} daddr {host} tcp dport {PORT} counter name approved accept\n"
        + rules
        + " counter name denied reject with icmpx type admin-prohibited\n }\n}\n"
    )


class Fabric:
    def __init__(self, script=None, *, module=None):
        if (script is None) == (module is None):
            raise ValueError("exactly one trusted child entry point required")
        self.script, self.module = script, module
        self.processes = []
        self.ipv6 = False

    def initialize(self):
        if os.getuid() != 0 or os.readlink("/proc/self/ns/user") == self.initial_user:
            raise KernelUnavailable("private mapped user namespace required")
        checked(["ip", "link", "set", "lo", "up"])
        checked(["ip", "link", "add", "fabric0", "type", "bridge"])
        checked(["ip", "link", "set", "fabric0", "up"])
        checked(["ip", "addr", "add", "192.0.2.1/24", "dev", "fabric0"])
        disabled = Path("/proc/sys/net/ipv6/conf/all/disable_ipv6")
        self.ipv6 = disabled.exists() and disabled.read_text().strip() == "0"
        if self.ipv6:
            checked(["ip", "-6", "addr", "add", "fd42:6f72:696f::1/64", "dev", "fabric0", "nodad"])
        if any(
            route.get("dst") == "default"
            for family in ("-4", "-6")
            for route in json.loads(checked(["ip", family, "-j", "route"]))
        ):
            raise KernelUnavailable("unexpected private default route")

    def spawn(self, role):
        if role not in ("source", "broker"):
            raise ValueError("fixed private fabric role required")
        process = subprocess.Popen(
            [
                "unshare",
                "--net",
                sys.executable,
                "-I",
                *(["-m", self.module] if self.module else [str(self.script)]),
                "--net-child",
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            close_fds=True,
            env={
                "PATH": LAB_PATH,
                "ORION_ISOLATION_CANARY": "synthetic",
                "HTTPS_PROXY": f"http://{V4_UNAPPROVED}:{PORT + 2}",
                "HTTP_PROXY": f"http://{V4_UNAPPROVED}:{PORT + 2}",
                "ALL_PROXY": f"socks5://{V4_UNAPPROVED}:{PORT + 2}",
            },
        )
        self.processes.append(process)
        if not select.select([process.stdout], [], [], 5)[0]:
            raise KernelUnavailable("child namespace handshake deadline")
        ready = process.stdout.readline(4097)
        try:
            value = json.loads(ready)
            if value != {"pid": process.pid, "net": os.readlink(f"/proc/{process.pid}/ns/net")}:
                raise ValueError("namespace handshake")
            if value["net"] == os.readlink("/proc/self/ns/net"):
                raise ValueError("private child namespace")
        except (OSError, ValueError):
            raise KernelUnavailable("child namespace unavailable") from None
        outer, inner = role + "0", role + "1"
        checked(["ip", "link", "add", outer, "type", "veth", "peer", "name", inner])
        checked(["ip", "link", "set", inner, "netns", str(process.pid)])
        checked(["ip", "link", "set", outer, "master", "fabric0"])
        checked(["ip", "link", "set", outer, "up"])
        self.in_net(process, ["ip", "link", "set", "lo", "up"])
        self.in_net(process, ["ip", "link", "set", inner, "up"])
        suffix = "2" if role == "source" else "4"
        self.in_net(process, ["ip", "addr", "add", "192.0.2." + suffix + "/24", "dev", inner])
        if role == "source":
            self.in_net(process, ["ip", "addr", "add", V4_UNAPPROVED + "/24", "dev", inner])
        if self.ipv6:
            for suffix6 in [suffix, "3"] if role == "source" else [suffix]:
                self.in_net(
                    process,
                    [
                        "ip",
                        "-6",
                        "addr",
                        "add",
                        "fd42:6f72:696f::" + suffix6 + "/64",
                        "dev",
                        inner,
                        "nodad",
                    ],
                )
        process.private_net = value["net"]
        process.interface = inner
        process.mac = json.loads(self.in_net(process, ["ip", "-j", "link", "show", inner]))[0][
            "address"
        ]
        return process

    @staticmethod
    def in_net(process, argv, *, input=None):
        return checked(
            ["nsenter", "--net=/proc/" + str(process.pid) + "/ns/net", *argv], input=input
        )

    def connect(self, source, broker, host):
        for process, addresses, mac in (
            (broker, [V4_APPROVED, V4_UNAPPROVED], source.mac),
            (source, ["192.0.2.4"], broker.mac),
        ):
            if self.ipv6:
                addresses += (
                    [V6_APPROVED, V6_UNAPPROVED] if process is broker else ["fd42:6f72:696f::4"]
                )
            for address in addresses:
                family = "-6" if ":" in address else "-4"
                self.in_net(
                    process,
                    [
                        "ip",
                        family,
                        "neigh",
                        "replace",
                        address,
                        "lladdr",
                        mac,
                        "nud",
                        "permanent",
                        "dev",
                        process.interface,
                    ],
                )
        self.in_net(broker, ["nft", "-f", "-"], input=firewall(host, ipv6=self.ipv6))
        return self.counters(broker)

    def counters(self, process):
        value = json.loads(self.in_net(process, ["nft", "-j", "list", "table", "inet", "orion"]))
        counters = {
            item["counter"]["name"]: item["counter"]["packets"]
            for item in value["nftables"]
            if "counter" in item
        }
        if set(counters) != {"approved", "denied"} | {
            counter_for(name) for name, _, _, _ in denied_tuples(self.ipv6)
        }:
            raise ValueError("kernel counters unavailable")
        return counters

    @staticmethod
    def boot(process, command, bootstrap):
        process.stdin.write(json.dumps({"command": command}) + "\n")
        process.stdin.write(json.dumps(bootstrap) + "\n")
        process.stdin.flush()

    def close(self):
        for process in reversed(self.processes):
            if process.poll() is None:
                process.kill()
            process.wait(timeout=5)
            for stream in (process.stdin, process.stdout, process.stderr):
                if not stream.closed:
                    stream.close()


def prerequisites():
    return all(
        shutil.which(tool, path=LAB_PATH)
        for tool in ("unshare", "nsenter", "ip", "nft", "bwrap", "openssl")
    )


def protect_parent():
    """Fixed Linux parent-death boundary; no syscall or signal selectors.

    The trusted launcher/controller chain and pre-bubblewrap fabric holders
    must terminate on parent death. Bubblewrap adds its --die-with-parent guard.
    """
    parent = os.getppid()
    if parent <= 1:
        os.kill(os.getpid(), signal.SIGKILL)
    if ctypes.CDLL(None, use_errno=True).prctl(1, signal.SIGKILL, 0, 0, 0) != 0:
        raise KernelUnavailable("parent-death containment unavailable")
    # The parent can disappear between getppid and prctl; never publish readiness
    # in that race or accept a setup command after authority has disappeared.
    if os.getppid() != parent:
        os.kill(os.getpid(), signal.SIGKILL)


def net_child():
    protect_parent()
    print(json.dumps({"pid": os.getpid(), "net": os.readlink("/proc/self/ns/net")}), flush=True)
    # Never read ahead into the bootstrap consumed by the exec'd jail.
    raw = bytearray()
    while len(raw) <= 65536:
        byte = os.read(0, 1)
        if not byte:
            raise ValueError("private setup EOF")
        raw += byte
        if byte == b"\n":
            break
    value = json.loads(raw)
    command = value["command"]
    # Private trusted setup pipe only, never exposed through broker application API.
    os.execv(command[0], command)


def process_command(module, *, readonly=(), writable=(), network=False):
    """Trusted installed-process composition; no public mount or code selectors.

    network=True inherits only an already-created private fabric namespace.
    The service verifies its expected namespace before accepting any request.
    """
    if type(module) is not str or module != "orion.pilot.services":
        raise ValueError("fixed installed service module required")
    bwrap = shutil.which("bwrap", path=LAB_PATH)
    executable = Path(sys.executable).absolute()
    prefix = Path(sys.prefix).absolute()
    base = Path(sys.base_prefix).resolve()
    if bwrap is None or prefix == Path("/") or not executable.resolve().is_relative_to(base):
        raise KernelUnavailable("installed namespace runtime unavailable")
    command = [
        bwrap,
        "--unshare-all",
        "--die-with-parent",
        "--new-session",
        "--cap-drop",
        "ALL",
        "--uid",
        "0",
        "--gid",
        "0",
        "--clearenv",
    ]
    if network:
        index = command.index("--unshare-all")
        command[index : index + 1] = [
            "--unshare-user",
            "--unshare-pid",
            "--unshare-ipc",
            "--unshare-uts",
            "--unshare-cgroup-try",
        ]
    ambient = ambient_mount_roots()
    for directory in ambient:
        if directory in _SYSTEM_MOUNTS:
            command.extend(("--ro-bind", str(directory), str(directory)))
    # The artifact may be installed below /tmp. Mount private tmpfs first so
    # the subsequent read-only venv mount is not hidden by that filesystem.
    command.extend(("--proc", "/proc", "--dev", "/dev", "--tmpfs", "/tmp", "--dir", "/work"))
    for location in ambient:
        if location not in _SYSTEM_MOUNTS:
            command.extend(("--ro-bind", str(location), str(location)))
    targets_seen = set()
    for option, mounts in (("--ro-bind", readonly), ("--bind", writable)):
        for source, target in mounts:
            target = str(target)
            if (
                not Path(target).is_absolute()
                or target in ("/", "/usr", "/lib", "/lib64", "/bin", "/proc", "/dev", "/tmp")
                or target in targets_seen
            ):
                raise ValueError("distinct scoped process mounts required")
            targets_seen.add(target)
            command.extend((option, str(Path(source).absolute()), target))
    command.extend(("--chdir", "/work", str(executable), "-I", "-m", module, "--child"))
    return command
