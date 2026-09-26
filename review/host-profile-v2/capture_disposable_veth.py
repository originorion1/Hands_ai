#!/usr/bin/env python3
"""Capture raw veth JSON only inside an empty disposable user/network namespace.

Invoke with: unshare --user --map-root-user --net python3 capture_disposable_veth.py OUTPUT_DIR
The enclosing namespace disappears when this process exits. No named netns or
host link is created.
"""

import hashlib
import json
import os
import subprocess
import sys
import time
from pathlib import Path

IP = "/usr/sbin/ip"
UNSHARE = "/usr/bin/unshare"
NSENTER = "/usr/bin/nsenter"
H = "orionp0"
P = "orionp1"
FILES = ("before_host", "before_peer", "after_host", "after_peer")


def output(*args):
    return subprocess.check_output(args, stderr=subprocess.PIPE)


def require_empty(ip_prefix):
    links = json.loads(output(*ip_prefix, "-j", "link", "show"))
    routes4 = json.loads(output(*ip_prefix, "-j", "route", "show", "table", "all"))
    routes6 = json.loads(output(*ip_prefix, "-j", "-6", "route", "show", "table", "all"))
    if len(links) != 1 or links[0].get("ifname") != "lo" or routes4 or routes6:
        raise RuntimeError("DISPOSABLE_NAMESPACE_NOT_EMPTY")


def main():
    if len(sys.argv) != 2:
        raise RuntimeError("OUTPUT_DIR_REQUIRED")
    destination = Path(sys.argv[1]).resolve()
    if not destination.is_dir() or any((destination / f"{name}.json").exists() for name in FILES):
        raise RuntimeError("CAPTURE_OUTPUT_COLLISION")
    require_empty((IP,))
    parent_ns = os.readlink("/proc/self/ns/net")
    child = subprocess.Popen((UNSHARE, "--net", "/usr/bin/sleep", "30"),
                             stdout=subprocess.DEVNULL, stderr=subprocess.PIPE)
    try:
        child_ns_path = Path(f"/proc/{child.pid}/ns/net")
        for _ in range(100):
            if child.poll() is not None:
                raise RuntimeError("DISPOSABLE_CHILD_EXITED")
            try:
                child_ns = os.readlink(child_ns_path)
            except FileNotFoundError:
                child_ns = parent_ns
            if child_ns != parent_ns:
                break
            time.sleep(0.02)
        else:
            raise RuntimeError("DISPOSABLE_CHILD_NAMESPACE_MISSING")
        child_ip = (NSENTER, "--target", str(child.pid), "--net", IP)
        require_empty(child_ip)
        subprocess.run((IP, "link", "add", H, "type", "veth", "peer", "name", P), check=True)
        captures = {
            "before_host": output(IP, "-j", "-details", "link", "show", "dev", H),
            "before_peer": output(IP, "-j", "-details", "link", "show", "dev", P),
        }
        subprocess.run((IP, "link", "set", P, "netns", str(child.pid)), check=True)
        captures["after_host"] = output(IP, "-j", "-details", "link", "show", "dev", H)
        captures["after_peer"] = output(*child_ip, "-j", "-details", "link", "show", "dev", P)
        if json.loads(output(*child_ip, "-j", "route", "show", "table", "all")) or json.loads(
                output(*child_ip, "-j", "-6", "route", "show", "table", "all")):
            raise RuntimeError("DISPOSABLE_ROUTE_APPEARED")
        metadata = {
            "schema": "orion.veth_ip_link.disposable_raw_capture.v1",
            "provenance": "Raw ip -j -details link stdout from nested disposable user/network namespaces",
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
