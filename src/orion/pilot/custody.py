"""Protected custody state owners for the single supervised read-only runtime.

These compose the canonical Broker, admission and AttemptJournal. Deploy outside
acquisition/reasoning processes, behind independently authenticated private IPC.
No transport selector, network opener, alternate grants or alternate history store.
The trusted deployment owns process isolation, role authentication and private keys.
"""

import hashlib
import hmac
import os
import queue
import secrets
import tempfile
import threading
from datetime import datetime
from pathlib import Path

from ..contracts import utc_now
from .broker import Broker
from .broker_contract import MAX_FRAME, authenticate, digest, exact
from .journal import AttemptJournal, JournalDenied, TransportLimits


class AuditCustody:
    """One journal owner. Tip acknowledgement is durable before any RPC success.

    The protected tip is not a second history store. A crash between ledger commit
    and tip commit is intentionally BLOCKED on restart, never automatically repaired.
    Both storage and the anchor are outside the hostile broker's writable namespace.
    """

    def __init__(self, directory, key, config):
        self.directory = Path(directory)
        self.anchor = self.directory / "accepted-head"
        self.binding = digest(config)
        self.caller = digest(config["caller"])
        limits = dict(config["limits"])
        limits["expires_at"] = datetime.fromisoformat(limits["expires_at"])
        self.journal = AttemptJournal(
            self.directory / "broker.db",
            key=key,
            binding=self.binding,
            limits=TransportLimits(**limits),
            expected_head=self.anchor.read_text() if self.anchor.exists() else None,
        )
        self.lock = threading.RLock()
        self.pin()

    def pin(self):
        temporary = self.directory / "next-head"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(self.journal.head)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.anchor)
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def dispatch(self, role, action, value):
        if role != "supervisor":
            raise JournalDenied("audit caller denied")
        exact(value, ("binding", "arguments"))
        if value["binding"] != self.binding:
            raise JournalDenied("audit scope denied")
        args = value["arguments"]
        with self.lock:
            if action == "inspect":
                exact(args, ())
                result = self.journal.inspect()
            elif action == "records":
                exact(args, ())
                result = self.journal.lifecycle_records()
            elif action == "lifecycle":
                exact(args, ("event", "at", "references"))
                if (
                    args["references"].get("scope") != self.binding
                    or args["references"].get("caller") != self.caller
                ):
                    raise JournalDenied("audit reference scope denied")
                self.journal.lifecycle(
                    args["event"],
                    at=datetime.fromisoformat(args["at"]),
                    references=args["references"],
                )
                result = None
            elif action == "begin":
                exact(args, ("now", "request_bytes"))
                self.journal.begin(datetime.fromisoformat(args["now"]), args["request_bytes"])
                result = None
            elif action == "check_active":
                exact(args, ("now",))
                self.journal.check_active(datetime.fromisoformat(args["now"]))
                result = None
            elif action == "finish":
                exact(args, ("success", "received_bytes"))
                self.journal.finish(**args)
                result = None
            elif action == "stop":
                exact(args, ())
                self.journal.stop()
                result = None
            else:
                raise JournalDenied("audit operation denied")
            self.pin()
            return result


class RemoteJournal:
    """Trusted AttemptJournal-shaped composition, not another persistence engine."""

    def __init__(self, client, binding):
        self.client, self.binding = client, binding

    def call(self, action, **arguments):
        return self.client(action, {"binding": self.binding, "arguments": arguments})

    @property
    def head(self):
        return self.inspect()["head"]

    def inspect(self):
        return self.call("inspect")

    def lifecycle_records(self):
        return self.call("records")

    def lifecycle(self, event, *, at, references):
        self.call("lifecycle", event=event, at=at.isoformat(), references=references)

    def begin(self, now, request_bytes):
        self.call("begin", now=now.isoformat(), request_bytes=request_bytes)

    def check_active(self, now):
        self.call("check_active", now=now.isoformat())

    def finish(self, *, success, received_bytes):
        self.call("finish", success=success, received_bytes=received_bytes)

    def stop(self):
        self.call("stop")


class AuthorizationCustody(Broker):
    """Canonical supervisor, never the hostile HTTPS acquisition process.

    Source receipts bind scope, a pending durable reservation and a fresh nonce.
    Source redemption is online, one-use, and checks expiry/control/audit liveness
    before releasing bytes. A supervisor crash leaves the canonical pending attempt
    in custody: restart refuses it, rather than reissuing a usable receipt.
    """

    def __init__(self, config, audit):
        self.offer = None
        self.lock = threading.RLock()
        self.offers = queue.Queue(maxsize=1)
        self.results = queue.Queue(maxsize=1)
        self.reading = False
        super().__init__(config, "/unused", journal_factory=lambda *a, **kw: audit)

    def _worker(self, bootstrap, *, timeout=5):
        with self.lock:
            payload = {
                "binding": self.binding,
                "head": self.journal.head,
                "nonce": secrets.token_hex(32),
            }
            receipt = self.binding + authenticate(self.key, "source_receipt", payload)
            self.offer = {
                "receipt": receipt,
                "payload": payload,
                "redeemed": False,
                "body": queue.Queue(maxsize=1),
            }
            self.offers.put({"status": "offered", "receipt": receipt})
        try:
            raw = self.offer["body"].get(timeout=timeout)
            if (
                type(raw) is not bytes
                or len(raw) > MAX_FRAME // 2
                or hashlib.sha256(raw).hexdigest() != self.config["source_digest"]
            ):
                raise ValueError("source digest denied")
            with tempfile.TemporaryDirectory(prefix="orion-custody-received-") as temporary:
                path = Path(temporary) / "received.json"
                path.write_bytes(raw)
                path.chmod(0o600)
                result = super()._worker(dict(bootstrap, path=str(path)), timeout=timeout)
            if len(raw) + len(result.stdout) > self.limits.response_bytes:
                raise ValueError("combined response budget denied")
            return result
        finally:
            with self.lock:
                self.offer = None

    def run_read(self, message):
        try:
            self.results.put(self.handle(message))
        except Exception:  # noqa: BLE001 - custody loss has no successful fallback
            self.results.put({"status": "denied"})

    def dispatch(self, role, action, value):
        if role == "owner" and action == "status":
            return self.status("unarmed")
        if role == "owner" and action == "control":
            with self.lock:
                return self.control(value)
        if role == "source" and action == "redeem":
            exact(value, ("receipt", "binding"))
            with self.lock:
                offer = self.offer
                if (
                    offer is None
                    or offer["redeemed"]
                    or value["binding"] != self.binding
                    or value["receipt"] != offer["receipt"]
                    or not self.armed
                    or self.stopped()
                    or utc_now() >= self.expires_at
                    or not hmac.compare_digest(
                        value["receipt"][64:],
                        authenticate(self.key, "source_receipt", offer["payload"]),
                    )
                ):
                    raise JournalDenied("source redemption denied")
                self.journal.check_active(utc_now())
                # Consume BEFORE reply. Replay cannot release a second response.
                offer["redeemed"] = True
                return {"authorized": True}
        if role != "broker":
            raise JournalDenied("authorization role denied")
        if action == "begin":
            with self.lock:
                if self.reading:
                    raise JournalDenied("concurrent broker request denied")
                self.reading = True
                threading.Thread(target=self.run_read, args=(value,), daemon=True).start()
            # The supervisor performs canonical authorization + durable reservation
            # before offering any receipt. Failed authorization has no source I/O.
            for _ in range(500):
                try:
                    return self.offers.get(timeout=0.01)
                except queue.Empty:
                    if not self.results.empty():
                        with self.lock:
                            self.reading = False
                        return self.results.get_nowait()
            raise JournalDenied("supervisor deadline")
        if action == "complete":
            exact(value, ("receipt", "body"))
            with self.lock:
                if (
                    self.offer is None
                    or not self.offer["redeemed"]
                    or value["receipt"] != self.offer["receipt"]
                    or type(value["body"]) is not str
                    or len(value["body"]) > MAX_FRAME
                ):
                    raise JournalDenied("broker completion denied")
                self.offer["body"].put_nowait(bytes.fromhex(value["body"]))
            # Metadata's canonical launcher may offer the next bounded schema
            # acquisition. It is not a retry and consumes its own reservation.
            for _ in range(500):
                try:
                    return self.offers.get(timeout=0.01)
                except queue.Empty:
                    if not self.results.empty():
                        with self.lock:
                            self.reading = False
                        return self.results.get_nowait()
            raise JournalDenied("supervisor completion deadline")
        raise JournalDenied("authorization operation denied")


class RuntimeCustody:
    """Single integrated endpoint; metadata cannot be bypassed by calling records."""

    def __init__(self, runtime):
        self.runtime = runtime
        self.active = None
        self.lock = threading.RLock()

    def dispatch(self, role, action, value):
        with self.lock:
            if role == "owner" and action == "health":
                return self.runtime.health()
            if role == "owner" and action == "status":
                owner = {"metadata": self.runtime.metadata, "read": self.runtime.records}[value]
                return owner.status("unarmed")
            if role == "owner" and action == "control":
                exact(value, ("operation", "message"))
                return self.runtime.control(value["operation"], value["message"])
            if role == "source" and action == "redeem":
                if self.active is None:
                    raise JournalDenied("no runtime acquisition")
                self.runtime.owner_for({"operation": self.active.operation})
                self.active.dispatch(role, action, value)
                return {"authorized": True, "binding": self.active.binding}
            if role != "broker" or action not in ("begin", "complete"):
                raise JournalDenied("runtime caller or action denied")
            if action == "begin":
                if self.active is not None:
                    raise JournalDenied("runtime acquisition already pending")
                self.active = self.runtime.owner_for(value)
            if self.active is None:
                raise JournalDenied("no runtime acquisition")
            owner = self.active
        # Do not hold the runtime lock while a source independently redeems or
        # the operator stops an in-flight acquisition.
        result = owner.dispatch(role, action, value)
        if result.get("status") != "offered":
            with self.lock:
                result = self.runtime.accept(owner, result)
                self.active = None
        return result
