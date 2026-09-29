"""Fixed synthetic custody services; no production daemon or transport selector.

The existing Broker becomes the protected authorization/admission supervisor.
Only the acquisition process has the kernel-approved HTTPS route. AttemptJournal
remains the sole ledger, owned by a separate process with an independent key/tip.
Capabilities authenticate roles, not signing authority. Fresh connection challenges
bind requests AND replies and make captured RPC frames unusable after restart.
"""

import hmac
import json
import secrets
import socket
import threading
from pathlib import Path

from orion.pilot.broker_contract import MAX_FRAME, authenticate, decode, exact
from orion.pilot.custody import AuditCustody, AuthorizationCustody, RemoteJournal, RuntimeCustody
from orion.pilot.journal import JournalDenied
from orion.understanding.role_checkpoint import _json

__all__ = (
    "AuditCustody",
    "AuthorizationCustody",
    "Endpoint",
    "RemoteJournal",
    "RuntimeCustody",
    "capability_keys",
    "receive",
    "rpc",
    "send",
)


def receive(stream):
    raw = stream.readline(MAX_FRAME + 1)
    if len(raw) > MAX_FRAME or not raw.endswith(b"\n"):
        raise ValueError("custody framing denied")
    return decode(raw)


def send(stream, value):
    raw = (_json(value) + "\n").encode()
    if len(raw) > MAX_FRAME:
        raise ValueError("custody frame oversized")
    stream.write(raw)
    stream.flush()


def rpc(path, role, key, action, value=None, *, capture=None):
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        peer.settimeout(8)
        peer.connect(str(path))
        with peer.makefile("rwb") as stream:
            challenge = exact(receive(stream), ("challenge",))["challenge"]
            body = {"challenge": challenge, "role": role, "action": action, "value": value}
            envelope = {"body": body, "mac": authenticate(key, "custody_request", body)}
            if capture is not None:
                capture.append(envelope)
            send(stream, envelope)
            reply = exact(receive(stream), ("body", "mac"))
            result = exact(reply["body"], ("challenge", "ok", "value"))
            if (
                result["challenge"] != challenge
                or type(result["ok"]) is not bool
                or not hmac.compare_digest(reply["mac"], authenticate(key, "custody_reply", result))
                or not result["ok"]
            ):
                raise JournalDenied("custody unavailable or denied")
            return result["value"]


class Endpoint:
    """Authenticated bounded local RPC; no peer-selected modules/files/callables."""

    def __init__(self, path, keys, dispatch):
        self.keys, self.dispatch = keys, dispatch
        self.listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.listener.bind(str(path))
        Path(path).chmod(0o600)
        self.listener.listen(8)
        self.listener.settimeout(0.1)
        self.stop = threading.Event()
        self.slots = threading.BoundedSemaphore(8)
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.stop.is_set():
            try:
                peer, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            if not self.slots.acquire(blocking=False):
                peer.close()
                continue
            threading.Thread(target=self.bounded_handle, args=(peer,), daemon=True).start()

    def bounded_handle(self, peer):
        try:
            self.handle(peer)
        finally:
            self.slots.release()

    def handle(self, peer):
        with peer:
            peer.settimeout(8)
            with peer.makefile("rwb") as stream:
                challenge = secrets.token_hex(32)
                key = None
                try:
                    send(stream, {"challenge": challenge})
                    envelope = exact(receive(stream), ("body", "mac"))
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
                    result = self.dispatch(body["role"], body["action"], body["value"])
                    reply = {"challenge": challenge, "ok": True, "value": result}
                except Exception:  # noqa: BLE001 - fixed denial without private details
                    reply = {"challenge": challenge, "ok": False, "value": None}
                try:
                    # Unknown roles cannot receive an authenticated successful reply.
                    send(
                        stream,
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
        self.thread.join(timeout=2)


def capability_keys(path):
    values = json.loads(Path(path).read_text())
    return {name: value.encode() for name, value in values.items()}
