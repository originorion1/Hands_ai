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
from ..understanding.role_checkpoint import _json
from .broker import Broker
from .broker_contract import (
    MAX_FRAME,
    authenticate,
    decode,
    digest,
    exact,
    is_erpnext_candidate,
)
from .journal import AttemptJournal, JournalDenied, TransportLimits
from .progress_witness import progress_state


class AuditCustody:
    """One journal owner. Tip acknowledgement is durable before any RPC success.

    The protected tip is not a second history store. A crash between ledger commit
    and tip commit is intentionally BLOCKED on restart, never automatically repaired.
    Both storage and the anchor are outside the hostile broker's writable namespace.
    """

    def __init__(self, directory, key, config, *, witness=None, witness_stream=None):
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
        if (witness is None) != (witness_stream is None):
            raise JournalDenied("complete audit witness required")
        self.witness, self.witness_stream = witness, witness_stream
        self.lock = threading.RLock()
        self._verify_witness()
        self.pin()

    def _progress(self):
        if self.witness_stream is None:
            return None
        sequence, head = self.journal.progress()
        return progress_state(self.witness_stream, sequence, head)

    def _verify_witness(self):
        if self.witness is not None:
            self.witness("verify", {"state": self._progress()})

    def _advance_witness(self, previous):
        if self.witness is None:
            return
        current = self._progress()
        if current == previous:
            self.witness("verify", {"state": current})
            return
        self.witness(
            "advance",
            {
                "expected_sequence": previous["sequence"],
                "expected_head": previous["head"],
                "state": current,
            },
        )

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
            previous = self._progress()
            self._verify_witness()
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
            self._advance_witness(previous)
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

    def __init__(self, config, audit, *, received_transform=None, installed_worker=False):
        self.offer = None
        self.lock = threading.RLock()
        self.offers = queue.Queue(maxsize=1)
        self.results = queue.Queue(maxsize=1)
        self.reading = False
        self.received_transform = received_transform
        self.installed_worker = installed_worker
        super().__init__(config, "/unused", journal_factory=lambda *a, **kw: audit)

    def _worker(self, bootstrap, *, timeout=5):
        source_request = None
        if is_erpnext_candidate(self.config):
            from .gateway import erpnext_source_request

            source_request = decode(_json({"request": bootstrap["request"]}).encode())
            if self.operation == "metadata":
                source_request["target"] = bootstrap["target"]
            # Validate before exposing a receipt. The gateway repeats this exact
            # encoder after one-use redemption; no URL enters over application IPC.
            erpnext_source_request(self.config, source_request)
        with self.lock:
            payload = {
                "binding": self.binding,
                "head": self.journal.head,
                "nonce": secrets.token_hex(32),
            }
            if source_request is not None:
                payload["source_request_sha256"] = digest(source_request)
            receipt = self.binding + authenticate(self.key, "source_receipt", payload)
            self.offer = {
                "receipt": receipt,
                "payload": payload,
                "redeemed": False,
                "body": queue.Queue(maxsize=1),
            }
            offered = {"status": "offered", "receipt": receipt}
            if source_request is not None:
                offered["source_request"] = source_request
            self.offers.put(offered)
        try:
            raw = self.offer["body"].get(timeout=timeout)
            if (
                type(raw) is not bytes
                or len(raw) > MAX_FRAME // 2
                or (
                    not is_erpnext_candidate(self.config)
                    and hashlib.sha256(raw).hexdigest() != self.config["source_digest"]
                )
            ):
                raise ValueError("source digest denied")
            with tempfile.TemporaryDirectory(prefix="orion-custody-received-") as temporary:
                path = Path(temporary) / "received.json"
                normalized = self.received_transform(raw, self.secret) if self.received_transform else raw
                if type(normalized) is not bytes or len(normalized) > MAX_FRAME // 2:
                    raise ValueError("normalization budget denied")
                path.write_bytes(normalized)
                path.chmod(0o600)
                prepared = dict(bootstrap, path=str(path),
                                source_digest=hashlib.sha256(normalized).hexdigest())
                result = super()._worker(prepared, timeout=timeout)
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
            candidate = is_erpnext_candidate(self.config)
            exact(
                value,
                ("receipt", "binding", "source_request_sha256")
                if candidate
                else ("receipt", "binding"),
            )
            with self.lock:
                offer = self.offer
                if (
                    offer is None
                    or offer["redeemed"]
                    or value["binding"] != self.binding
                    or value["receipt"] != offer["receipt"]
                    or (
                        candidate
                        and value["source_request_sha256"]
                        != offer["payload"]["source_request_sha256"]
                    )
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

    def _before_begin(self, owner, value):
        """Trusted deployment hook under the single runtime admission lock."""

    def _before_redeem(self, owner, value):
        """Trusted independently protected storage liveness hook."""

    def _accepted(self, owner, result):
        return self.runtime.accept(owner, result)

    def dispatch(self, role, action, value):
        with self.lock:
            if role == "owner" and action == "health":
                return dict(self.runtime.health(), acquisition_in_flight=self.active is not None)
            if role == "owner" and action == "status":
                if type(value) is not str or value not in self.runtime.owners:
                    raise JournalDenied("runtime status scope denied")
                owner = self.runtime.owners[value]
                return owner.status("unarmed")
            if role == "owner" and action == "control":
                exact(value, ("operation", "message"))
                return self.runtime.control(value["operation"], value["message"])
            if role == "source" and action == "redeem":
                if self.active is None:
                    raise JournalDenied("no runtime acquisition")
                self.runtime.owner_for({"operation": self.active.operation})
                self._before_redeem(self.active, value)
                self.active.dispatch(role, action, value)
                return {"authorized": True, "binding": self.active.binding}
            if role != "broker" or action not in ("begin", "complete"):
                raise JournalDenied("runtime caller or action denied")
            if action == "begin":
                if self.active is not None:
                    raise JournalDenied("runtime acquisition already pending")
                owner = self.runtime.owner_for(value)
                self._before_begin(owner, value)
                self.active = owner
            if self.active is None:
                raise JournalDenied("no runtime acquisition")
            owner = self.active
        # Do not hold the runtime lock while a source independently redeems or
        # the operator stops an in-flight acquisition.
        result = owner.dispatch(role, action, value)
        if result.get("status") != "offered":
            with self.lock:
                result = self._accepted(owner, result)
                self.active = None
        return result
