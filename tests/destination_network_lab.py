"""Synthetic raw connection witnesses and legacy script jail composition.

The network namespace and nft policy owner is the installed runtime module.
"""

import socket
import subprocess

from orion.pilot.isolation import (
    LAB_PATH,
    PORT,
    V4_APPROVED,
    V4_UNAPPROVED,
    V6_APPROVED,
    V6_UNAPPROVED,
    Fabric,
    KernelUnavailable,
    checked,
    counter_for,
    denied_tuples,
    firewall,
    net_child,
    prerequisites,
)

__all__ = (
    "LAB_PATH",
    "PORT",
    "V4_APPROVED",
    "V4_UNAPPROVED",
    "V6_APPROVED",
    "V6_UNAPPROVED",
    "Fabric",
    "KernelUnavailable",
    "checked",
    "counter_for",
    "denied_tuples",
    "firewall",
    "jail_command",
    "net_child",
    "prerequisites",
    "subprocess",
    "targets",
)


def targets(ipv6, approved_host):
    items = [
        ("ipv4-unapproved", socket.AF_INET, V4_UNAPPROVED, PORT),
        ("ipv4-alternate-port", socket.AF_INET, V4_APPROVED, PORT + 1),
        ("ipv4-proxy", socket.AF_INET, V4_UNAPPROVED, PORT + 2),
    ]
    if approved_host != V4_APPROVED:
        items.append(("ipv4-other-family", socket.AF_INET, V4_APPROVED, PORT))
    if ipv6:
        items.extend(
            [
                ("ipv6-unapproved", socket.AF_INET6, V6_UNAPPROVED, PORT),
                ("ipv6-alternate-port", socket.AF_INET6, V6_APPROVED, PORT + 1),
                ("ipv6-proxy", socket.AF_INET6, V6_UNAPPROVED, PORT + 2),
                ("ipv4-mapped-ipv6", socket.AF_INET6, "::ffff:" + V4_UNAPPROVED, PORT),
            ]
        )
        if approved_host != V6_APPROVED:
            items.append(("ipv6-other-family", socket.AF_INET6, V6_APPROVED, PORT))
    return items


def jail_command(script, readonly, *, state=None):
    from isolation_lab import namespace_command

    command = namespace_command(script, readonly=readonly)
    # Inherit ONLY the already-created, configured private child network namespace.
    # Bubblewrap 0.9 has no --netns FD option. Explicitly unshare every other
    # supported namespace; neither --share-net nor a host netns descriptor is used.
    index = command.index("--unshare-all")
    command[index : index + 1] = [
        "--unshare-user",
        "--unshare-pid",
        "--unshare-ipc",
        "--unshare-uts",
        "--unshare-cgroup-try",
    ]
    for option in ("--uid", "--gid"):
        command[command.index(option) + 1] = "0"
    # UID 0 here maps to the unprivileged host owner, not initial-namespace root.
    if state is not None:
        index = command.index("--chdir")
        command[index:index] = ["--bind", str(state), "/state"]
    return command
