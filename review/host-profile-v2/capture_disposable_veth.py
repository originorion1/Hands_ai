#!/usr/bin/env python3
"""Capture raw veth JSON only inside an empty disposable user/network namespace.

Invoke with baseline mount/network namespace IDs in the environment, using
unshare --user --map-root-user --mount --net. The program refuses to mount
sysfs unless both namespaces differ from the supplied baseline IDs.
The enclosing namespace disappears when this process exits. No named netns or
host link is created.
"""

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path

IP = "/usr/sbin/ip"
UNSHARE = "/usr/bin/unshare"
NSENTER = "/usr/bin/nsenter"
MOUNT = "/usr/bin/mount"
H = "orionp0"
P = "orionp1"
FILES = ("before_host", "before_peer", "after_host", "after_peer",
         "before_namespace_listing", "after_namespace_listing", "sysfs_indexes")


def output(*args):
    return subprocess.check_output(args, stderr=subprocess.PIPE)


def require_empty(ip_prefix):
    links = json.loads(output(*ip_prefix, "-j", "link", "show"))
    routes4 = json.loads(output(*ip_prefix, "-j", "route", "show", "table", "all"))
    routes6 = json.loads(output(*ip_prefix, "-j", "-6", "route", "show", "table", "all"))
    if len(links) != 1 or links[0].get("ifname") != "lo" or routes4 or routes6:
        raise RuntimeError("DISPOSABLE_NAMESPACE_NOT_EMPTY")


def sysfs_indexes(name, *, child_pid=None):
    """Preserve the endpoint's raw sysfs text in its own network namespace."""
    values = {}
    for field in ("ifindex", "iflink"):
        path = f"/sys/class/net/{name}/{field}"
        raw = (output(NSENTER, "--target", str(child_pid), "--net", "--mount", "/usr/bin/cat", path)
               if child_pid is not None else Path(path).read_bytes())
        values[field] = raw.decode("ascii")
    return values


def main():
    if len(sys.argv) != 2:
        raise RuntimeError("OUTPUT_DIR_REQUIRED")
    destination = Path(sys.argv[1]).resolve()
    if not destination.is_dir() or any((destination / f"{name}.json").exists() for name in FILES):
        raise RuntimeError("CAPTURE_OUTPUT_COLLISION")
    parent_ns = os.readlink("/proc/self/ns/net")
    parent_mount_ns = os.readlink("/proc/self/ns/mnt")
    if (not os.environ.get("ORION_BASELINE_NET_NAMESPACE") or
            not os.environ.get("ORION_BASELINE_MOUNT_NAMESPACE") or
            parent_ns == os.environ["ORION_BASELINE_NET_NAMESPACE"] or
            parent_mount_ns == os.environ["ORION_BASELINE_MOUNT_NAMESPACE"]):
        raise RuntimeError("DISPOSABLE_NAMESPACE_NOT_PROVEN")
    subprocess.run((MOUNT, "-t", "sysfs", "-o", "ro", "sysfs", "/sys"), check=True)
    require_empty((IP,))
    if sorted(path.name for path in Path("/sys/class/net").iterdir()) != ["lo"]:
        raise RuntimeError("DISPOSABLE_SYSFS_NOT_EMPTY")
    child = subprocess.Popen((
        UNSHARE, "--net", "--mount", "--propagation", "private", "/bin/sh", "-c",
        "/usr/bin/mount -t sysfs -o ro sysfs /sys && printf R && exec /usr/bin/sleep 30"),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        if child.stdout.read(1) != b"R":
            raise RuntimeError("DISPOSABLE_CHILD_SYSFS_MOUNT_FAILED")
        child_ns_path = Path(f"/proc/{child.pid}/ns/net")
        child_ns = os.readlink(child_ns_path)
        if (child.poll() is not None or child_ns == parent_ns or
                os.readlink(f"/proc/{child.pid}/ns/mnt") == parent_mount_ns):
            raise RuntimeError("DISPOSABLE_CHILD_NAMESPACE_MISSING")
        child_ip = (NSENTER, "--target", str(child.pid), "--net", "--mount", IP)
        require_empty(child_ip)
        if sorted(output(NSENTER, "--target", str(child.pid), "--net", "--mount",
                         "/usr/bin/ls", "/sys/class/net").decode().split()) != ["lo"]:
            raise RuntimeError("DISPOSABLE_CHILD_SYSFS_NOT_EMPTY")
        subprocess.run((IP, "link", "add", H, "type", "veth", "peer", "name", P), check=True)
        before_sysfs = {"host": sysfs_indexes(H), "peer": sysfs_indexes(P)}
        captures = {
            "before_host": output(IP, "-j", "-details", "link", "show", "dev", H),
            "before_peer": output(IP, "-j", "-details", "link", "show", "dev", P),
            "before_namespace_listing": output(*child_ip, "-j", "link", "show"),
        }
        subprocess.run((IP, "link", "set", P, "netns", str(child.pid)), check=True)
        captures["after_host"] = output(IP, "-j", "-details", "link", "show", "dev", H)
        captures["after_peer"] = output(*child_ip, "-j", "-details", "link", "show", "dev", P)
        captures["after_namespace_listing"] = output(*child_ip, "-j", "link", "show")
        after_sysfs = {"host": sysfs_indexes(H),
                       "peer": sysfs_indexes(P, child_pid=child.pid)}
        for stage, host_raw, peer_raw, sysfs in (
            ("before", captures["before_host"], captures["before_peer"], before_sysfs),
            ("after", captures["after_host"], captures["after_peer"], after_sysfs),
        ):
            host_idx = json.loads(host_raw)[0]["ifindex"]
            peer_idx = json.loads(peer_raw)[0]["ifindex"]
            if (int(sysfs["host"]["ifindex"].strip()) != host_idx or
                    int(sysfs["host"]["iflink"].strip()) != peer_idx or
                    int(sysfs["peer"]["ifindex"].strip()) != peer_idx or
                    int(sysfs["peer"]["iflink"].strip()) != host_idx):
                raise RuntimeError("DISPOSABLE_SYSFS_LINK_JSON_MISMATCH_" + stage.upper())
        captures["sysfs_indexes"] = (json.dumps({
            "before": before_sysfs, "after": after_sysfs,
            "network_namespaces": {"before_host": parent_ns, "before_peer": parent_ns,
                                   "after_host": parent_ns, "after_peer": child_ns},
        }, sort_keys=True, indent=2) + "\n").encode("ascii")
        if json.loads(output(*child_ip, "-j", "route", "show", "table", "all")) or json.loads(
                output(*child_ip, "-j", "-6", "route", "show", "table", "all")):
            raise RuntimeError("DISPOSABLE_ROUTE_APPEARED")
        metadata = {
            "schema": "orion.veth_ip_link.disposable_raw_capture.v2",
            "provenance": "Raw ip -j -details link stdout and sysfs index text from nested disposable user/network namespaces",
            "host_network_modified": False,
            "uplink_present": False,
            "default_route_present": False,
            "customer_traffic": False,
            "placement": {"before": "same_disposable_network_namespace",
                          "after": "peer_in_nested_disposable_network_namespace"},
            "file_sha256": {f"{name}.json": hashlib.sha256(data).hexdigest()
                            for name, data in captures.items()},
        }
        for name, data in captures.items():
            with (destination / f"{name}.json").open("xb") as stream:
                stream.write(data)
        with (destination / "capture_metadata.json").open("x") as stream:
            json.dump(metadata, stream, sort_keys=True, indent=2)
            stream.write("\n")
    finally:
        child.terminate()
        child.wait(timeout=5)


if __name__ == "__main__":
    main()
