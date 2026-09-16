"""Protected admission archive and bounded checkpoint index, not authorization.

The canonical historical batch deliberately rejects admitted provenance payloads.
This owner therefore indexes canonical admitted observations without changing that
contract or AttemptJournal. A separate deployment protects this owner's key,
database and accepted tip from acquisition/reasoning. Privileged rollback of BOTH
the database and its protected tip needs an external monotonic witness; this owner
does not claim that property. Payload expiry is logical SQLite erasure, not a claim
about forensic recovery from storage hardware or independently retained backups.
"""

import hmac
import json
import os
import sqlite3
import stat
import threading
from contextlib import contextmanager
from dataclasses import asdict, replace
from datetime import datetime, timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, UUID, uuid5

from ..contracts import EvidenceKind, ObservationMode, utc_now
from ..discovery.pilot_metadata import ScopeProposal
from ..discovery.pilot_read import PilotRequest, _admit, _text
from ..discovery.read_window import ReviewedReadWindow
from ..history.evidence import _observation_to_data
from .broker_contract import (
    MAX_FRAME,
    authenticate,
    digest,
    exact,
    grant_from,
    is_record_operation,
    metadata_grant_from,
    observations_from,
    private_bytes,
)
from .broker_metadata import proposal_from
from .journal import JournalDenied, grant_digest


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _reference(value):
    if (
        type(value) is not str
        or len(value) != 64
        or any(c not in "0123456789abcdef" for c in value)
    ):
        raise JournalDenied("digest reference required")
    return value


def _request_seen(checkpoints, binding, reference):
    return any(
        e["binding"] == binding and e["request_reference"] == reference
        for e in checkpoints.values()
    )


class EvidenceCustody:
    """One protected single-writer archive; restart never grants acquisition.

    dispatch uses {'binding': config digest, 'arguments': {...}}. Supervisor may
    append/availability; availability accepts {} or a digest request_reference
    to reject accepted request replay before credential use. Owner may
    inspect/load/prune/resolve. Resolve binds one accepted checkpoint to its
    expected scope and request reference, never merely the latest reply. Append
    may additionally authenticate request_sha256 for exact-message validation;
    legacy checkpoints without it are not exact-message proof. Caller's capability is
    authenticated by the private IPC owner, never by trusting a JSON role field.
    Checkpoints are never removed or overwritten; capacity exhaustion denies new
    acquisition even after content expires. Clock and policy are trusted inputs.
    """

    def __init__(self, directory, key, configs, policy=None, *, clock=utc_now, semantic_limit=1):
        if type(key) is not bytes or len(key) < 32 or not callable(clock):
            raise JournalDenied("protected evidence key and clock required")
        policy = (
            {"max_entries": 100, "max_bytes": 2097152, "ttl_seconds": 3600}
            if policy is None
            else policy
        )
        exact(policy, ("max_entries", "max_bytes", "ttl_seconds"))
        for name, maximum in (
            ("max_entries", 1000),
            ("max_bytes", 20971520),
            ("ttl_seconds", 86400),
        ):
            if type(policy[name]) is not int or not 1 <= policy[name] <= maximum:
                raise JournalDenied("bounded evidence policy required")
        if type(semantic_limit) is not int or semantic_limit not in (1, 100):
            raise JournalDenied("fixed semantic checkpoint budget required")
        if type(configs) not in (tuple, list) or not 1 <= len(configs) <= (2 if semantic_limit == 1 else 10):
            raise JournalDenied("explicit bounded custody scopes required")
        if any(c.get("operation") != "metadata" and not is_record_operation(c.get("operation"))
               for c in configs):
            raise JournalDenied("fixed evidence operations required")
        self.configs = {digest(c): json.loads(_json(c)) for c in configs}
        if len(self.configs) != len(configs):
            raise JournalDenied("duplicate evidence scopes denied")
        self.key, self.clock, self.policy = key, clock, dict(policy)
        self.semantic_limit = semantic_limit
        self.directory = Path(directory)
        self.path, self.anchor = (
            self.directory / "evidence.db",
            self.directory / "accepted-evidence-head",
        )
        self.lock = threading.RLock()
        info = self.directory.stat()
        if info.st_uid != os.getuid() or stat.S_IMODE(info.st_mode) & 0o077:
            raise JournalDenied("private evidence directory required")
        new = not self.path.exists()
        if new:
            if self.anchor.exists():
                raise JournalDenied("evidence missing at accepted checkpoint")
            fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
            os.close(fd)
            self.head = "0" * 64
        else:
            self.head = _reference(private_bytes(self.anchor, 64).decode())
        self._private_database()
        with self._connect() as db:
            if new:
                db.execute(
                    "CREATE TABLE events (sequence INTEGER PRIMARY KEY, body TEXT NOT NULL, mac TEXT NOT NULL)"
                )
                db.execute(
                    "CREATE TABLE payloads (sequence INTEGER PRIMARY KEY, body TEXT NOT NULL)"
                )
                self._append_event(
                    db,
                    {
                        "event": "configure",
                        "scopes": sorted(self.configs),
                        "policy": self.policy,
                        "at": self._now().isoformat(),
                        **({"semantic_limit": semantic_limit} if semantic_limit != 1 else {}),
                    },
                )
            else:
                self._verify(db)
        if new:
            self._pin()

    def _now(self):
        now = self.clock()
        ReviewedReadWindow.check_time_type(now)
        return now

    def _private_database(self):
        info = self.path.lstat()
        if (
            not stat.S_ISREG(info.st_mode)
            or info.st_uid != os.getuid()
            or stat.S_IMODE(info.st_mode) & 0o077
            or info.st_nlink != 1
        ):
            raise JournalDenied("private regular evidence database required")

    @contextmanager
    def _connect(self):
        self._private_database()
        db = sqlite3.connect(self.path, timeout=1, isolation_level="IMMEDIATE")
        db.execute("PRAGMA synchronous=FULL")
        db.execute("PRAGMA secure_delete=ON")
        try:
            with db:
                yield db
        finally:
            db.close()

    def _pin(self):
        temporary = self.directory / "next-evidence-head"
        fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW, 0o600)
        with os.fdopen(fd, "w") as stream:
            stream.write(self.head)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, self.anchor)
        fd = os.open(self.directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)

    def _append_event(self, db, value):
        sequence = db.execute("SELECT COUNT(*) FROM events").fetchone()[0] + 1
        body = dict(value, sequence=sequence, previous=self.head)
        encoded = _json(body)
        mac = authenticate(self.key, "admitted_evidence_checkpoint", body)
        db.execute("INSERT INTO events VALUES (?,?,?)", (sequence, encoded, mac))
        self.head = mac
        return sequence

    def _verify(self, db):
        if (
            db.execute("SELECT 1 FROM events WHERE length(body)>? LIMIT 1", (MAX_FRAME,)).fetchone()
            or db.execute(
                "SELECT 1 FROM payloads WHERE length(body)>? LIMIT 1", (MAX_FRAME,)
            ).fetchone()
        ):
            raise JournalDenied("oversized evidence storage entry")
        rows = db.execute(
            "SELECT sequence,body,mac FROM events ORDER BY sequence LIMIT ?",
            (2 * self.policy["max_entries"] + self.semantic_limit + 2,),
        ).fetchall()
        if not rows or len(rows) > 2 * self.policy["max_entries"] + self.semantic_limit + 1:
            raise JournalDenied("evidence checkpoint bound invalid")
        events, previous, expired = [], "0" * 64, set()
        for sequence, encoded, mac in rows:
            body = json.loads(encoded)
            if (
                sequence != len(events) + 1
                or body.get("sequence") != sequence
                or body.get("previous") != previous
                or not hmac.compare_digest(
                    mac, authenticate(self.key, "admitted_evidence_checkpoint", body)
                )
            ):
                raise JournalDenied("evidence checkpoint integrity mismatch")
            if body["event"] == "expire":
                if body["checkpoint"] in expired:
                    raise JournalDenied("duplicate retention tombstone")
                expired.add(body["checkpoint"])
            elif body["event"] not in ("configure", "append", "semantic_checkpoint"):
                raise JournalDenied("evidence checkpoint event denied")
            events.append(body)
            previous = mac
        if sum(e["event"] == "semantic_checkpoint" for e in events) > self.semantic_limit:
            raise JournalDenied("bounded semantic custody pin required")
        if previous != self.head:
            raise JournalDenied("evidence history rollback detected")
        if (
            events[0]["event"] != "configure"
            or events[0]["scopes"] != sorted(self.configs)
            or events[0]["policy"] != self.policy
            or events[0].get("semantic_limit", 1) != self.semantic_limit
        ):
            raise JournalDenied("evidence configuration changed")
        checkpoints = {e["sequence"]: e for e in events if e["event"] == "append"}
        if not expired <= checkpoints.keys() or len(checkpoints) > self.policy["max_entries"]:
            raise JournalDenied("evidence index mismatch")
        payloads = dict(
            db.execute(
                "SELECT sequence,body FROM payloads LIMIT ?", (self.policy["max_entries"] + 1,)
            ).fetchall()
        )
        if set(payloads) != checkpoints.keys() - expired:
            raise JournalDenied("evidence payload deletion or resurrection detected")
        for sequence, encoded in payloads.items():
            if (
                len(encoded.encode()) > MAX_FRAME
                or digest(json.loads(encoded)) != checkpoints[sequence]["payload_sha256"]
            ):
                raise JournalDenied("admitted payload integrity mismatch")
        return events, checkpoints, expired, payloads

    def _check(self, db):
        if private_bytes(self.anchor, 64).decode() != self.head:
            raise JournalDenied("protected evidence tip changed")
        result = self._verify(db)
        if self._now() < datetime.fromisoformat(result[0][-1]["at"]):
            raise JournalDenied("evidence clock rollback denied")
        return result

    def _prune(self, db):
        _, checkpoints, expired, _ = self._check(db)
        now = self._now()
        removed = 0
        for sequence, checkpoint in checkpoints.items():
            if sequence not in expired and now >= datetime.fromisoformat(checkpoint["expires_at"]):
                db.execute("DELETE FROM payloads WHERE sequence=?", (sequence,))
                self._append_event(
                    db,
                    {
                        "event": "expire",
                        "checkpoint": sequence,
                        "payload_sha256": checkpoint["payload_sha256"],
                        "at": now.isoformat(),
                    },
                )
                removed += 1
        return removed

    def _validate(self, binding, values):
        config = self.configs[binding]
        observations = observations_from(values)
        now = self._now()
        if is_record_operation(config["operation"]):
            grant = grant_from(config["grant"])
            w = grant.window
            request = PilotRequest(
                w.tenant_id,
                w.company,
                grant.source_id,
                w.resource,
                w.fields,
                w.date_field,
                w.start,
                w.end,
                grant.max_records,
            )
            upstream = []
            for observation in observations:
                payload = observation.evidence.payload
                if set(payload) != {"resource", "record", "provenance"}:
                    raise JournalDenied("admitted record payload required")
                provenance = payload["provenance"]
                upstream.append(
                    replace(
                        observation,
                        observation_id=UUID(provenance["upstream_observation_id"]),
                        evidence=replace(
                            observation.evidence,
                            evidence_id=UUID(provenance["upstream_evidence_id"]),
                            payload={"resource": payload["resource"], "record": payload["record"]},
                        ),
                    )
                )
            admitted = _admit(tuple(upstream), request, grant, now)
            if [_observation_to_data(o) for o in admitted] != values:
                raise JournalDenied("canonical record admission mismatch")
            company, source = w.company, grant.source_id
        elif config["operation"] == "metadata":
            grant = metadata_grant_from(config["grant"])
            if len(observations) != 1:
                raise JournalDenied("canonical metadata batch required")
            observation = observations[0]
            evidence, payload = observation.evidence, values[0]["evidence"]["payload"]
            exact(
                payload,
                (
                    "catalog",
                    "catalog_complete",
                    "schema_targets",
                    "proposals",
                    "company_context",
                    "site_schema_scope",
                    "authorization_id",
                    "scope_sha256",
                    "source_id",
                    "review_required",
                    "record_reads_allowed",
                ),
            )
            catalog, targets = payload["catalog"], payload["schema_targets"]
            expected_targets = [n for n in catalog if n not in grant.excluded_resources][
                : grant.max_schemas
            ]
            if (
                observation.mode is not ObservationMode.READ_ONLY
                or evidence.kind is not EvidenceKind.METADATA
                or evidence.source != "pilot-metadata-discovery"
                or evidence.confidence is not None
                or evidence.tenant_id != grant.request.tenant_id
                or evidence.observed_at > now
                or payload["company_context"] != grant.request.company
                or payload["source_id"] != grant.request.source_id
                or payload["authorization_id"] != grant.authorization_id
                or payload["scope_sha256"] != grant_digest(grant)
                or payload["site_schema_scope"] is not True
                or payload["review_required"] is not True
                or payload["record_reads_allowed"] is not False
                or type(catalog) is not list
                or catalog != sorted(set(catalog))
                or len(catalog) > grant.max_catalog_entries
                or type(payload["catalog_complete"]) is not bool
                or targets != expected_targets
            ):
                raise JournalDenied("canonical metadata scope mismatch")
            for resource in catalog:
                _text(resource)
            for proposal in payload["proposals"]:
                if proposal["resource"] not in targets:
                    raise JournalDenied("metadata proposal scope mismatch")
                rebuilt = (
                    ScopeProposal(
                        proposal["resource"],
                        tuple(proposal["fields"]),
                        tuple(proposal["date_fields"]),
                    )
                    if proposal["interpretation"] is None
                    else proposal_from(
                        proposal["resource"],
                        [c["declaration"] for c in proposal["interpretation"]["candidates"]],
                    )
                )
                if json.loads(_json(asdict(rebuilt))) != proposal:
                    raise JournalDenied("metadata interpretation mismatch")
            identity = json.dumps(
                {
                    "scope": payload["scope_sha256"],
                    "catalog": catalog,
                    "complete": payload["catalog_complete"],
                    "examined": targets,
                    "proposals": payload["proposals"],
                    "observed_at": evidence.observed_at.isoformat(),
                },
                sort_keys=True,
            )
            if evidence.evidence_id != uuid5(
                NAMESPACE_URL, "orion:metadata:evidence:" + identity
            ) or observation.observation_id != uuid5(
                NAMESPACE_URL, "orion:metadata:observation:" + identity
            ):
                raise JournalDenied("canonical metadata identity mismatch")
            company, source = grant.request.company, grant.request.source_id
        else:
            raise JournalDenied("explicit evidence operation required")
        return observations, company, source

    def dispatch(self, role, action, value):
        if (role, action) not in {
            ("supervisor", "append"),
            ("supervisor", "availability"),
            ("owner", "inspect"),
            ("owner", "load"),
            ("owner", "prune"),
            ("owner", "resolve"),
        }:
            raise JournalDenied("evidence caller or operation denied")
        exact(value, ("binding", "arguments"))
        binding, args = value["binding"], value["arguments"]
        if binding not in self.configs:
            raise JournalDenied("evidence scope denied")
        exact(
            args,
            ("observations", "journal_head", "request_reference")
            + (("request_sha256",) if type(args) is dict and "request_sha256" in args else ())
            if action == "append"
            else ("checkpoint", "request_reference")
            if action == "resolve"
            else ("request_reference",)
            if action == "availability" and args != {}
            else (),
        )
        if action == "availability" and args:
            _reference(args["request_reference"])
        if action == "append" and "request_sha256" in args:
            _reference(args["request_sha256"])
        if action == "resolve":
            _reference(args["request_reference"])
            if (
                type(args["checkpoint"]) is not int
                or not 2 <= args["checkpoint"] <= 2 * self.policy["max_entries"] + self.semantic_limit + 1
            ):
                raise JournalDenied("bounded accepted checkpoint reference required")
        with self.lock:
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                self._prune(db)
            # Retention acknowledgements are durable even if the next request is
            # rejected. Never roll back erased payloads as part of a denied read.
            self._pin()
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                _, checkpoints, expired, payloads = self._check(db)
                if action == "append":
                    _reference(args["journal_head"])
                    _reference(args["request_reference"])
                    if _request_seen(checkpoints, binding, args["request_reference"]):
                        raise JournalDenied("evidence request replay denied")
                    observations, company, source = self._validate(binding, args["observations"])
                    accepted_ids = {
                        r["observation_id"] for e in checkpoints.values() for r in e["references"]
                    }
                    if any(str(o.observation_id) in accepted_ids for o in observations):
                        raise JournalDenied("admitted observation replay denied")
                    encoded = _json(args["observations"])
                    if (
                        len(encoded.encode()) > MAX_FRAME
                        or len(checkpoints) >= self.policy["max_entries"]
                        or sum(len(p.encode()) for p in payloads.values()) + len(encoded.encode())
                        > self.policy["max_bytes"]
                    ):
                        raise JournalDenied("evidence capacity exhausted")
                    now = self._now()
                    references = [
                        {
                            "observation_id": str(o.observation_id),
                            "evidence_id": str(o.evidence.evidence_id),
                            "tenant_id": o.evidence.tenant_id,
                            "kind": o.evidence.kind.value,
                            "source": o.evidence.source,
                            "observed_at": o.evidence.observed_at.isoformat(),
                            "provenance": args["observations"][i]["evidence"]["payload"].get(
                                "provenance"
                            ),
                            "resource": o.evidence.payload.get("resource"),
                        }
                        for i, o in enumerate(observations)
                    ]
                    for reference in references:
                        record_id = (reference["provenance"] or {}).get("source_record_id")
                        reference["revision"] = 1 + sum(
                            r["resource"] == reference["resource"]
                            and (r["provenance"] or {}).get("source_record_id") == record_id
                            and r["tenant_id"] == reference["tenant_id"]
                            and e["company"] == company
                            and e["source_id"] == source
                            for e in checkpoints.values()
                            for r in e["references"]
                        )
                    sequence = self._append_event(
                        db,
                        {
                            "event": "append",
                            "binding": binding,
                            "company": company,
                            "source_id": source,
                            "request_reference": args["request_reference"],
                            "journal_head": args["journal_head"],
                            "payload_sha256": digest(args["observations"]),
                            "references": references,
                            "at": now.isoformat(),
                            "expires_at": (
                                now + timedelta(seconds=self.policy["ttl_seconds"])
                            ).isoformat(),
                            **(
                                {"request_sha256": args["request_sha256"]}
                                if "request_sha256" in args
                                else {}
                            ),
                        },
                    )
                    db.execute("INSERT INTO payloads VALUES (?,?)", (sequence, encoded))
                    result = {
                        "checkpoint": sequence,
                        "head": self.head,
                        "payload_sha256": digest(args["observations"]),
                    }
                elif action == "resolve":
                    checkpoint = checkpoints.get(args["checkpoint"])
                    if (
                        checkpoint is None
                        or checkpoint["binding"] != binding
                        or checkpoint["request_reference"] != args["request_reference"]
                        or args["checkpoint"] in expired
                    ):
                        raise JournalDenied("accepted retained checkpoint unavailable")
                    result = {
                        "checkpoint": args["checkpoint"],
                        "head": authenticate(self.key, "admitted_evidence_checkpoint", checkpoint),
                        "custody_head": self.head,
                        "binding": binding,
                        "operation": self.configs[binding]["operation"],
                        "request_reference": checkpoint["request_reference"],
                        "request_sha256": checkpoint.get("request_sha256"),
                        "journal_head": checkpoint["journal_head"],
                        "payload_sha256": checkpoint["payload_sha256"],
                        "references": checkpoint["references"],
                        "observations": json.loads(payloads[args["checkpoint"]]),
                        "authority_restored": False,
                    }
                else:
                    if (
                        action == "availability"
                        and args
                        and _request_seen(checkpoints, binding, args["request_reference"])
                    ):
                        raise JournalDenied("evidence request replay denied")
                    scoped = [
                        dict(e, retained=e["sequence"] not in expired)
                        for e in checkpoints.values()
                        if e["binding"] == binding
                    ]
                    used = sum(len(p.encode()) for p in payloads.values())
                    capacity = (
                        len(checkpoints) < self.policy["max_entries"]
                        and used + MAX_FRAME <= self.policy["max_bytes"]
                    )
                    result = {
                        "head": self.head,
                        "checkpoints": scoped,
                        "retained_bytes": used,
                        "index_entries": len(checkpoints),
                        "available": capacity,
                        "authority_restored": False,
                        "interpretation": "UNKNOWN",
                    }
                    if action == "availability" and not capacity:
                        raise JournalDenied("evidence capacity unavailable")
                    if action == "load":
                        result["observations"] = [
                            o
                            for e in scoped
                            if e["retained"]
                            for o in json.loads(payloads[e["sequence"]])
                        ]
            self._pin()
            return result
