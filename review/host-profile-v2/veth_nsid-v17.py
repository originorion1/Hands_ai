"""Resolve peer network namespace IDs against open namespace descriptors.

RTM_GETNSID IDs are relative to the socket's network namespace. No name or
numeric interface index is treated as a namespace identity.
"""

import os
import socket
import stat
import struct

_NLMSG = struct.Struct("=IHHII")
_ATTR = struct.Struct("=HH")
_RTM_NEWNSID = 88
_RTM_GETNSID = 90
_NLMSG_ERROR = 2
_NLM_F_REQUEST = 1
_NETNSA_NSID = 1
_NETNSA_FD = 3


class NsidError(Exception):
    """A namespace ID could not be resolved unambiguously."""


def _align(value):
    return (value + 3) & ~3


def _query_nsid(peer_fd):
    """Ask rtnetlink for this socket namespace's ID of an open peer netns."""
    body = b"\0" * 4 + _ATTR.pack(8, _NETNSA_FD) + struct.pack("=I", peer_fd)
    request = _NLMSG.pack(_NLMSG.size + len(body), _RTM_GETNSID,
                          _NLM_F_REQUEST, 1, 0) + body
    try:
        with socket.socket(socket.AF_NETLINK, socket.SOCK_RAW, socket.NETLINK_ROUTE) as sock:
            sock.settimeout(3)
            sock.bind((0, 0))
            port_id = sock.getsockname()[0]
            sock.send(request)
            data, _ancillary, flags, address = sock.recvmsg(65536)
            if flags & socket.MSG_TRUNC or address[0] != 0:
                raise NsidError("NSID_REPLY_UNVERIFIED")
    except (OSError, TimeoutError) as exc:
        raise NsidError("NSID_QUERY_FAILED") from exc
    if len(data) < _NLMSG.size:
        raise NsidError("NSID_REPLY_UNVERIFIED")
    length, kind, _flags, sequence, sender = _NLMSG.unpack_from(data)
    if (length != len(data) or sequence != 1 or sender != port_id or
            kind == _NLMSG_ERROR or kind != _RTM_NEWNSID or length < _NLMSG.size + 4):
        raise NsidError("NSID_REPLY_UNVERIFIED")
    attributes = data[_NLMSG.size + 4:]
    matches = []
    offset = 0
    while offset < len(attributes):
        if len(attributes) - offset < _ATTR.size:
            raise NsidError("NSID_REPLY_UNVERIFIED")
        size, attr_kind = _ATTR.unpack_from(attributes, offset)
        if size < _ATTR.size or offset + size > len(attributes):
            raise NsidError("NSID_REPLY_UNVERIFIED")
        if attr_kind == _NETNSA_NSID:
            if size != 8:
                raise NsidError("NSID_REPLY_UNVERIFIED")
            matches.append(struct.unpack_from("=i", attributes, offset + 4)[0])
        offset += _align(size)
    if offset != len(attributes) or len(matches) != 1 or matches[0] < 0:
        raise NsidError("NSID_REPLY_UNVERIFIED")
    return matches[0]


def _query_from_pinned_namespace(source_fd, peer_fd):
    """Use a child so the caller never changes its own network namespace."""
    read_fd, write_fd = os.pipe()
    try:
        pid = os.fork()
        if pid == 0:
            os.close(read_fd)
            try:
                os.setns(source_fd, os.CLONE_NEWNET)
                result = str(_query_nsid(peer_fd)).encode("ascii")
                os.write(write_fd, result)
                os._exit(0)
            except (OSError, NsidError, AttributeError):
                os._exit(1)
        os.close(write_fd)
        write_fd = -1
        result = os.read(read_fd, 64)
        _, status = os.waitpid(pid, 0)
        if not os.WIFEXITED(status) or os.WEXITSTATUS(status) != 0 or not result.isdigit():
            raise NsidError("NSID_PEER_QUERY_FAILED")
        return int(result)
    except OSError as exc:
        raise NsidError("NSID_PEER_QUERY_FAILED") from exc
    finally:
        os.close(read_fd)
        if write_fd >= 0:
            os.close(write_fd)


def resolve_pair(peer_path, expected_peer_inode):
    """Return host-to-peer and peer-to-host NSIDs for pinned namespace FDs."""
    try:
        host_fd = os.open("/proc/self/ns/net", os.O_RDONLY | os.O_CLOEXEC)
        try:
            peer_fd = os.open(peer_path, os.O_RDONLY | os.O_CLOEXEC)
            try:
                host_stat, peer_stat = os.fstat(host_fd), os.fstat(peer_fd)
                if (not stat.S_ISREG(peer_stat.st_mode) or
                        f"net:[{peer_stat.st_ino}]" != expected_peer_inode or
                        os.readlink(peer_path) != expected_peer_inode or
                        host_stat.st_ino == peer_stat.st_ino):
                    raise NsidError("NSID_NAMESPACE_UNVERIFIED")
                host_to_peer = _query_nsid(peer_fd)
                peer_to_host = _query_from_pinned_namespace(peer_fd, host_fd)
                if (os.readlink(peer_path) != expected_peer_inode or
                        os.stat(peer_path).st_ino != peer_stat.st_ino):
                    raise NsidError("NSID_NAMESPACE_UNVERIFIED")
                return host_to_peer, peer_to_host
            finally:
                os.close(peer_fd)
        finally:
            os.close(host_fd)
    except OSError as exc:
        raise NsidError("NSID_NAMESPACE_UNVERIFIED") from exc
