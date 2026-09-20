"""Separately retained monotonic custody progress for one bounded deployment.

The witness stores only deployment, scope, sequence and chain-head digests.  It
does not store evidence, issue grants or decide whether custody content is true.
Its same-host composition detects rollback of the explicitly separate audit and
evidence directories only while this witness storage and key remain intact.
"""

import hmac
import json
import os
import sqlite3
import stat
import threading
from contextlib import contextmanager
from pathlib import Path

from .broker_contract import (
    authenticate,
    digest,
    exact,
    transition_binding,
    transition_initial_head,
    transition_policy_from,
)
from .journal import JournalDenied

WITNESS_VERSION = 1
WITNESS_FILENAME = "progress-witness.db"
ENROLLMENT_FILENAME = "witness-enrollment"
ZERO_HEAD = "0" * 64


def _reference(value):
    if (
        type(value) is not str
        or len(value) != 64
        or any(character not in "0123456789abcdef" for character in value)
    ):
        raise JournalDenied("witness digest reference required")
    return value


def _sequence(value):
    # Evidence custody admits at most two events per retained observation plus
    # its bounded semantic checkpoints and initial state (2 * 1000 + 100 + 1).
    if type(value) is not int or not 1 <= value <= 2101:
        raise JournalDenied("bounded witness sequence required")
    return value


def witness_streams(configs, transition=None):
    """Return the one fixed stream set derived from canonical custody scopes."""
    if type(configs) is not list or not 1 <= len(configs) <= 10:
        raise JournalDenied("bounded witness scopes required")
    bindings = [digest(config) for config in configs]
    if len(set(bindings)) != len(bindings):
        raise JournalDenied("duplicate witness scope denied")
    streams = [
        {
            "identity": digest(
                {"version": WITNESS_VERSION, "kind": "audit", "scope_binding": binding}
            ),
            "kind": "audit",
            "scope_binding": binding,
        }
        for binding in bindings
    ]
    transition_scope = None
    if transition is not None:
        transition = transition_policy_from(transition)
        transition_scope = transition_binding(transition)
        streams.append(
            {
                "identity": digest(
                    {
                        "version": WITNESS_VERSION,
                        "kind": "audit",
                        "scope_binding": transition_scope,
                    }
                ),
                "kind": "audit",
                "scope_binding": transition_scope,
            }
        )
    evidence_scope = digest(
        {"config_bindings": sorted(bindings)}
        if transition_scope is None
        else {
            "config_bindings": sorted(bindings),
            "transition_binding": transition_scope,
        }
    )
    streams.append(
        {
            "identity": digest(
                {
                    "version": WITNESS_VERSION,
                    "kind": "evidence",
                    "scope_binding": evidence_scope,
                }
            ),
            "kind": "evidence",
            "scope_binding": evidence_scope,
        }
    )
    return streams


def deployment_identity_for_manifest(manifest, artifact):
    """Bind one deployment without depending on the profile hash itself."""
    value = {
            "contract": "orion-progress-witness-v1",
            "manifest_version": manifest["version"],
            "mode": manifest["mode"],
            "artifact": {
                "name": artifact["name"],
                "version": artifact["version"],
                "record_sha256": artifact["record_sha256"],
            },
            "state_directory": str(Path(manifest["state_directory"]).absolute()),
            "keys_directory": str(Path(manifest["keys_directory"]).absolute()),
            "witness_directory": str(Path(manifest["witness_directory"]).absolute()),
            "host": manifest["host"],
            "streams": witness_streams(
                manifest["configs"], manifest.get("grant_transition")
            ),
            "semantic_sha256": (
                digest(manifest["semantic"]) if "semantic" in manifest else None
            ),
        }
    if "grant_transition" in manifest:
        value["grant_transition_sha256"] = digest(
            transition_policy_from(manifest["grant_transition"])
        )
    return digest(value)


def witness_identity(deployment_identity):
    return digest(
        {
            "version": WITNESS_VERSION,
            "deployment_identity": _reference(deployment_identity),
        }
    )


def witness_contract(manifest):
    """Return the exact immutable metadata stored at explicit enrollment."""
    return {
        "version": WITNESS_VERSION,
        "witness_identity": witness_identity(manifest["deployment_identity"]),
        "deployment_identity": _reference(manifest["deployment_identity"]),
        "artifact_record_sha256": _reference(manifest["artifact_record_sha256"]),
        "deployment_profile_sha256": _reference(manifest["deployment_profile_sha256"]),
        "streams": witness_streams(
            manifest["configs"], manifest.get("grant_transition")
        ),
        "lifecycle": "explicit_operator_bootstrap",
    }


def stream_for(configs, kind, binding=None, transition=None):
    streams = witness_streams(configs, transition)
    if kind == "audit":
        return next(
            stream
            for stream in streams
            if stream["kind"] == kind and stream["scope_binding"] == binding
        )
    return next(stream for stream in streams if stream["kind"] == kind)


def transition_initial_state(configs, transition):
    stream = stream_for(
        configs, "audit", transition_binding(transition), transition=transition
    )
    return progress_state(stream, 1, transition_initial_head(transition))


def progress_state(stream, sequence, head):
    return {
        "identity": stream["identity"],
        "kind": stream["kind"],
        "scope_binding": stream["scope_binding"],
        "sequence": _sequence(sequence),
        "head": _reference(head),
    }


def enrollment_receipt(contract):
    """Return the immutable marker that makes bootstrap non-repeatable."""
    contract = ProgressWitness._contract(contract)
    return {
        "version": WITNESS_VERSION,
        "witness_identity": contract["witness_identity"],
        "deployment_identity": contract["deployment_identity"],
        "contract_sha256": digest(contract),
        "lifecycle": "enrolled_no_automatic_replacement",
    }


def enrollment_path(state_directory):
    return Path(state_directory) / ENROLLMENT_FILENAME


def verify_enrollment_receipt(path, contract):
    """Require the exact protected enrollment marker before witness use."""
    path = Path(path)
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_uid != os.getuid()
        or stat.S_IMODE(info.st_mode) != 0o600
        or info.st_nlink != 1
        or path.is_symlink()
    ):
        raise JournalDenied("private witness enrollment receipt required")
    expected = ProgressWitness._json(enrollment_receipt(contract)).encode()
    if path.read_bytes() != expected:
        raise JournalDenied("witness enrollment receipt mismatch")
    return path


class ProgressWitness:
    """Single-writer conditional high-water marks outside custody rollback state."""

    def __init__(self, directory, key, contract, receipt):
        if type(key) is not bytes or len(key) < 32:
            raise JournalDenied("independent witness key required")
        self.directory = Path(directory)
        self.path = self.directory / WITNESS_FILENAME
        self.key = key
        self.contract = self._contract(contract)
        self.receipt = verify_enrollment_receipt(receipt, self.contract)
        self.lock = threading.RLock()
        self._private_directory()
        self._private_database()
        with self._connect() as database:
            self._verify(database)

    @classmethod
    def enroll(cls, directory, key, contract, states, enrollment_directory):
        """Explicit one-time bootstrap; never called by normal runtime startup."""
        directory = Path(directory)
        if type(key) is not bytes or len(key) < 32:
            raise JournalDenied("independent witness key required")
        info = directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or directory.is_symlink()
        ):
            raise JournalDenied("private witness directory required")
        path = directory / WITNESS_FILENAME
        receipt = enrollment_path(enrollment_directory)
        owner = cls.__new__(cls)
        owner.directory, owner.path, owner.key = directory, path, key
        owner.contract = owner._contract(contract)
        owner.lock = threading.RLock()
        expected = {stream["identity"]: stream for stream in owner.contract["streams"]}
        if type(states) is not list or len(states) != len(expected):
            raise JournalDenied("complete witness enrollment state required")
        normalized = {}
        for state in states:
            state = owner._state(state)
            stream = expected.get(state["identity"])
            if stream is None or any(state[name] != stream[name] for name in stream):
                raise JournalDenied("witness enrollment scope mismatch")
            if state["identity"] in normalized:
                raise JournalDenied("duplicate witness enrollment state")
            normalized[state["identity"]] = state
        try:
            path.lstat()
        except FileNotFoundError:
            pass
        else:
            raise FileExistsError("witness storage already exists")
        enrollment_directory = Path(enrollment_directory)
        info = enrollment_directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or enrollment_directory.is_symlink()
        ):
            raise JournalDenied("private enrollment directory required")
        receipt_descriptor = os.open(
            receipt, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
        )
        with os.fdopen(receipt_descriptor, "wb") as stream:
            stream.write(owner._json(enrollment_receipt(owner.contract)).encode())
            stream.flush()
            os.fsync(stream.fileno())
        directory_descriptor = os.open(
            enrollment_directory, os.O_RDONLY | os.O_DIRECTORY
        )
        try:
            os.fsync(directory_descriptor)
        finally:
            os.close(directory_descriptor)
        try:
            descriptor = os.open(
                path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600
            )
            os.close(descriptor)
            with owner._connect() as database:
                database.execute(
                    "CREATE TABLE metadata (singleton INTEGER PRIMARY KEY, body TEXT NOT NULL, mac TEXT NOT NULL)"
                )
                database.execute(
                    "CREATE TABLE streams (identity TEXT PRIMARY KEY, body TEXT NOT NULL, mac TEXT NOT NULL)"
                )
                body = owner._json(owner.contract)
                database.execute(
                    "INSERT INTO metadata VALUES (1,?,?)",
                    (body, authenticate(key, "progress_witness_metadata", owner.contract)),
                )
                for state in normalized.values():
                    body = dict(state, predecessor_sequence=None, predecessor_head=None)
                    encoded = owner._json(body)
                    database.execute(
                        "INSERT INTO streams VALUES (?,?,?)",
                        (
                            state["identity"],
                            encoded,
                            authenticate(key, "progress_witness_stream", body),
                        ),
                    )
            descriptor = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
            try:
                os.fsync(descriptor)
            finally:
                os.close(descriptor)
            return cls(directory, key, contract, receipt)
        except Exception:
            try:
                path.unlink()
            except OSError:
                pass
            raise

    @staticmethod
    def _json(value):
        return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)

    @staticmethod
    def _contract(value):
        value = exact(
            value,
            (
                "version",
                "witness_identity",
                "deployment_identity",
                "artifact_record_sha256",
                "deployment_profile_sha256",
                "streams",
                "lifecycle",
            ),
        )
        if value["version"] != WITNESS_VERSION or value["lifecycle"] != "explicit_operator_bootstrap":
            raise JournalDenied("fixed witness lifecycle required")
        for name in (
            "witness_identity",
            "deployment_identity",
            "artifact_record_sha256",
            "deployment_profile_sha256",
        ):
            _reference(value[name])
        if value["witness_identity"] != witness_identity(value["deployment_identity"]):
            raise JournalDenied("witness identity mismatch")
        streams = value["streams"]
        if type(streams) is not list or not 2 <= len(streams) <= 12:
            raise JournalDenied("bounded witness streams required")
        identities = set()
        for stream in streams:
            exact(stream, ("identity", "kind", "scope_binding"))
            _reference(stream["identity"])
            _reference(stream["scope_binding"])
            if stream["kind"] not in ("audit", "evidence"):
                raise JournalDenied("fixed witness stream kind required")
            if stream["identity"] in identities:
                raise JournalDenied("duplicate witness stream denied")
            identities.add(stream["identity"])
        return json.loads(ProgressWitness._json(value))

    @staticmethod
    def _state(value):
        value = exact(value, ("identity", "kind", "scope_binding", "sequence", "head"))
        _reference(value["identity"])
        _reference(value["scope_binding"])
        _sequence(value["sequence"])
        _reference(value["head"])
        if value["kind"] not in ("audit", "evidence"):
            raise JournalDenied("fixed witness stream kind required")
        return dict(value)

    def _private_directory(self):
        info = self.directory.lstat()
        if (
            not stat.S_ISDIR(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or self.directory.is_symlink()
        ):
            raise JournalDenied("private witness directory required")

    def _private_database(self):
        info = self.path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_nlink != 1
            or self.path.is_symlink()
        ):
            raise JournalDenied("private regular witness database required")

    @contextmanager
    def _connect(self):
        self._private_database()
        database = sqlite3.connect(self.path, timeout=1, isolation_level="IMMEDIATE")
        database.execute("PRAGMA synchronous=FULL")
        try:
            with database:
                yield database
        finally:
            database.close()

    def _decode_row(self, encoded, mac):
        if type(encoded) is not str or len(encoded) > 4096:
            raise JournalDenied("bounded witness row required")
        body = json.loads(encoded)
        if not hmac.compare_digest(
            mac, authenticate(self.key, "progress_witness_stream", body)
        ):
            raise JournalDenied("witness row authentication failed")
        state = self._state({name: body.get(name) for name in (
            "identity", "kind", "scope_binding", "sequence", "head"
        )})
        if set(body) != set(state) | {"predecessor_sequence", "predecessor_head"}:
            raise JournalDenied("witness row shape denied")
        predecessor_sequence = body["predecessor_sequence"]
        predecessor_head = body["predecessor_head"]
        if predecessor_sequence is None:
            if predecessor_head is not None:
                raise JournalDenied("witness predecessor mismatch")
        else:
            _sequence(predecessor_sequence)
            _reference(predecessor_head)
            if predecessor_sequence >= state["sequence"]:
                raise JournalDenied("witness predecessor order denied")
        return body

    def _verify(self, database):
        try:
            metadata = database.execute(
                "SELECT body,mac FROM metadata WHERE singleton=1"
            ).fetchall()
            rows = database.execute(
                "SELECT identity,body,mac FROM streams ORDER BY identity LIMIT 12"
            ).fetchall()
        except sqlite3.Error as error:
            raise JournalDenied("witness storage unavailable") from error
        if len(metadata) != 1 or len(rows) != len(self.contract["streams"]):
            raise JournalDenied("complete witness state required")
        encoded, mac = metadata[0]
        stored = json.loads(encoded)
        if (
            stored != self.contract
            or not hmac.compare_digest(
                mac, authenticate(self.key, "progress_witness_metadata", stored)
            )
        ):
            raise JournalDenied("witness deployment binding mismatch")
        expected = {stream["identity"]: stream for stream in self.contract["streams"]}
        decoded = {}
        for identity, encoded, mac in rows:
            body = self._decode_row(encoded, mac)
            stream = expected.get(identity)
            if (
                identity != body["identity"]
                or stream is None
                or any(body[name] != stream[name] for name in stream)
            ):
                raise JournalDenied("witness stream substitution denied")
            decoded[identity] = body
        return decoded

    def dispatch(self, role, action, value):
        if action in ("status", "snapshot"):
            if role != "owner" or value is not None:
                raise JournalDenied("witness status caller denied")
            with self.lock, self._connect() as database:
                rows = self._verify(database)
            if action == "snapshot":
                states = [
                    {name: row[name] for name in (
                        "identity", "kind", "scope_binding", "sequence", "head"
                    )}
                    for _, row in sorted(rows.items())
                ]
                return {"states": states, "sha256": digest(states)}
            return {
                "version": WITNESS_VERSION,
                "witness_identity": self.contract["witness_identity"],
                "deployment_identity": self.contract["deployment_identity"],
                "streams": len(rows),
                "available": True,
                "whole_host_rollback_protection": False,
            }
        if action not in ("verify", "advance") or role not in ("audit", "evidence"):
            raise JournalDenied("witness caller or action denied")
        request = exact(
            value,
            ("state",)
            if action == "verify"
            else ("expected_sequence", "expected_head", "state"),
        )
        state = self._state(request["state"])
        if state["kind"] != role:
            raise JournalDenied("witness caller scope denied")
        with self.lock, self._connect() as database:
            database.execute("BEGIN IMMEDIATE")
            rows = self._verify(database)
            current = rows.get(state["identity"])
            if current is None or any(
                state[name] != current[name] for name in ("kind", "scope_binding")
            ):
                raise JournalDenied("witness stream scope denied")
            if action == "verify":
                if any(state[name] != current[name] for name in ("sequence", "head")):
                    raise JournalDenied("custody rollback or witness disagreement")
                outcome = "verified"
            else:
                expected_sequence = _sequence(request["expected_sequence"])
                expected_head = _reference(request["expected_head"])
                duplicate = (
                    state["sequence"] == current["sequence"]
                    and state["head"] == current["head"]
                    and expected_sequence == current["predecessor_sequence"]
                    and expected_head == current["predecessor_head"]
                )
                if duplicate:
                    outcome = "duplicate"
                else:
                    if (
                        expected_sequence != current["sequence"]
                        or expected_head != current["head"]
                        or state["sequence"] <= current["sequence"]
                        or state["head"] == current["head"]
                    ):
                        raise JournalDenied("conditional witness advancement denied")
                    body = dict(
                        state,
                        predecessor_sequence=expected_sequence,
                        predecessor_head=expected_head,
                    )
                    encoded = self._json(body)
                    database.execute(
                        "UPDATE streams SET body=?,mac=? WHERE identity=?",
                        (
                            encoded,
                            authenticate(self.key, "progress_witness_stream", body),
                            state["identity"],
                        ),
                    )
                    outcome = "advanced"
        return {
            "status": outcome,
            "version": WITNESS_VERSION,
            "witness_identity": self.contract["witness_identity"],
            "sequence": state["sequence"],
            "head": state["head"],
        }
