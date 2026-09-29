"""Bounded authenticated AF_UNIX control IPC; never pickle or IP networking.

Caller capabilities authorize roles, not grant signing. Every request and reply
is purpose-bound to a fresh connection challenge. Custody owns action scope.
"""

import hmac
import os
import queue
import secrets
import stat
import threading
from multiprocessing.connection import Client, Listener
from pathlib import Path

from ..understanding.role_checkpoint import _json
from .broker_contract import MAX_FRAME, authenticate, decode, exact
from .journal import JournalDenied

DEADLINE = 8
MAX_CALLERS = 16
_callers = threading.BoundedSemaphore(MAX_CALLERS)


def receive(peer):
    if not peer.poll(DEADLINE):
        raise JournalDenied("custody deadline exceeded")
    return decode(peer.recv_bytes(MAX_FRAME))


def send(peer, value):
    raw = _json(value).encode()
    if not 1 <= len(raw) <= MAX_FRAME:
        raise ValueError("bounded custody frame required")
    peer.send_bytes(raw)


def _request(path, role, key, action, value, capture):
    # Linux sun_path is bounded independently of filesystem path length. Resolve
    # the same local socket inode through a pinned private directory descriptor;
    # never change cwd globally or fall back to TCP/abstract sockets.
    path = Path(path)
    descriptor = os.open(path.parent, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try:
        info = os.fstat(descriptor)
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise JournalDenied("private custody directory required")
        return _exchange(
            f"/proc/self/fd/{descriptor}/{path.name}", role, key, action, value, capture
        )
    finally:
        os.close(descriptor)


def _exchange(path, role, key, action, value, capture):
    with Client(path, family="AF_UNIX", authkey=None) as peer:
        challenge = exact(receive(peer), ("challenge",))["challenge"]
        if type(challenge) is not str or len(challenge) != 64:
            raise JournalDenied("invalid custody challenge")
        body = {"challenge": challenge, "role": role, "action": action, "value": value}
        envelope = {"body": body, "mac": authenticate(key, "custody_request", body)}
        if capture is not None:
            capture.append(envelope)
        send(peer, envelope)
        reply = exact(receive(peer), ("body", "mac"))
        result = exact(reply["body"], ("challenge", "ok", "value"))
        if (
            result["challenge"] != challenge
            or type(result["ok"]) is not bool
            or type(reply["mac"]) is not str
            or not hmac.compare_digest(reply["mac"], authenticate(key, "custody_reply", result))
            or not result["ok"]
        ):
            raise JournalDenied("custody unavailable or denied")
        return result["value"]


def rpc(path, role, key, action, value=None, *, capture=None):
    """Fixed-role local call; blocked connects consume bounded slots then deny.

    AF_UNIX connect can block on a hostile full backlog. A finite worker bound
    prevents repeated timeouts from accumulating unbounded threads or authority.
    """
    if not _callers.acquire(blocking=False):
        raise JournalDenied("custody caller capacity exhausted")
    result = queue.Queue(maxsize=1)

    def request():
        try:
            result.put((True, _request(path, role, key, action, value, capture)))
        except Exception:  # noqa: BLE001 - never disclose private boundary details
            result.put((False, None))
        finally:
            _callers.release()

    threading.Thread(target=request, daemon=True).start()
    try:
        ok, reply = result.get(timeout=DEADLINE)
    except queue.Empty:
        raise JournalDenied("custody deadline exceeded") from None
    if not ok:
        raise JournalDenied("custody unavailable or denied")
    return reply


class Endpoint:
    """Private endpoint with bounded handlers and authenticated JSON dispatch."""

    def __init__(self, path, keys, dispatch):
        path = Path(path)
        info = path.parent.stat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or path.parent.is_symlink()
            or path.exists()
            or path.is_symlink()
        ):
            raise JournalDenied("private custody endpoint required")
        if (
            type(keys) is not dict
            or not keys
            or any(
                type(role) is not str or type(key) is not bytes or len(key) < 32
                for role, key in keys.items()
            )
        ):
            raise ValueError("fixed role capabilities required")
        self.keys, self.dispatch = dict(keys), dispatch
        self.listener = Listener(str(path), family="AF_UNIX", backlog=8, authkey=None)
        # CPython's AF_UNIX Listener has no public accept timeout. The fixed
        # local listener (never an IP socket) needs one for bounded shutdown.
        self.listener._listener._socket.settimeout(0.1)
        path.chmod(0o600)
        self.stop = threading.Event()
        self.slots = threading.BoundedSemaphore(8)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.stop.is_set():
            try:
                peer = self.listener.accept()
            except TimeoutError:
                continue
            except (OSError, EOFError):
                return
            if self.stop.is_set():
                peer.close()
                return
            if not self.slots.acquire(blocking=False):
                peer.close()
                continue
            threading.Thread(target=self.bounded_handle, args=(peer,), daemon=True).start()

    def bounded_handle(self, peer):
        timer = threading.Timer(DEADLINE, peer.close)
        timer.daemon = True
        timer.start()
        try:
            self.handle(peer)
        finally:
            timer.cancel()
            peer.close()
            self.slots.release()

    def handle(self, peer):
        challenge, key = secrets.token_hex(32), None
        try:
            send(peer, {"challenge": challenge})
            envelope = exact(receive(peer), ("body", "mac"))
            body = exact(envelope["body"], ("challenge", "role", "action", "value"))
            key = self.keys.get(body["role"])
            if (
                key is None
                or body["challenge"] != challenge
                or type(envelope["mac"]) is not str
                or not hmac.compare_digest(
                    envelope["mac"], authenticate(key, "custody_request", body)
                )
            ):
                raise JournalDenied("caller authentication denied")
            reply = {
                "challenge": challenge,
                "ok": True,
                "value": self.dispatch(body["role"], body["action"], body["value"]),
            }
        except Exception:  # noqa: BLE001 - fixed denial without private values
            reply = {"challenge": challenge, "ok": False, "value": None}
        try:
            send(
                peer,
                {
                    "body": reply,
                    "mac": authenticate(
                        key or b"unknown-role-denial-key-0000000000", "custody_reply", reply
                    ),
                },
            )
        except (OSError, ValueError):
            pass

    def close(self):
        self.stop.set()
        self.listener.close()
        self.thread.join(timeout=0.2)
