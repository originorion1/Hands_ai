"""Protected admission archive and bounded checkpoint index, not authorization.

The canonical historical batch deliberately rejects admitted provenance payloads.
This owner therefore indexes canonical admitted observations without changing that
contract or AttemptJournal. A separate deployment protects this owner's key,
database and accepted tip from acquisition/reasoning. When configured by the
installed deployment, an independently retained progress witness rejects rollback
of this database plus its accepted tip before custody use. That same-host
composition is not whole-host or snapshot rollback protection. Payload expiry is
logical SQLite erasure, not a claim about forensic recovery from storage hardware
or independently retained backups.
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
    is_erpnext_candidate,
    is_record_operation,
    metadata_grant_from,
    observations_from,
    private_bytes,
    transition_policy_from,
    transition_record_config,
    transition_request_from,
)
from .broker_metadata import erpnext_proposal_from, proposal_from
from .journal import JournalDenied, grant_digest
from .progress_witness import progress_state


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

    def __init__(
        self,
        directory,
        key,
        configs,
        policy=None,
        *,
        clock=utc_now,
        semantic_limit=1,
        witness=None,
        witness_stream=None,
        transition=None,
        deployment_identity=None,
    ):
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
        self.enrolled_scopes = sorted(self.configs)
        self.transition_policy = (
            transition_policy_from(transition) if transition is not None else None
        )
        if (self.transition_policy is None) != (deployment_identity is None):
            raise JournalDenied("complete grant transition custody required")
        if deployment_identity is not None:
            _reference(deployment_identity)
        self.deployment_identity = deployment_identity
        self.key, self.clock, self.policy = key, clock, dict(policy)
        self.semantic_limit = semantic_limit
        if (witness is None) != (witness_stream is None):
            raise JournalDenied("complete evidence witness required")
        self.witness, self.witness_stream = witness, witness_stream
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
                        "scopes": self.enrolled_scopes,
                        "policy": self.policy,
                        "at": self._now().isoformat(),
                        **(
                            {"grant_transition_sha256": digest(self.transition_policy)}
                            if self.transition_policy is not None
                            else {}
                        ),
                        **({"semantic_limit": semantic_limit} if semantic_limit != 1 else {}),
                    },
                )
            else:
                events, checkpoints, _, payloads = self._verify(db)
                transitioned = self._transition_from(events, checkpoints, payloads)
                if transitioned is not None:
                    self.configs[digest(transitioned)] = transitioned
        if new:
            self._pin()
        self._verify_witness()

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

    def _transition_from(self, events, checkpoints, payloads):
        transitions = [event for event in events if event["event"] == "grant_transition"]
        if not transitions:
            return None
        if self.transition_policy is None or len(transitions) != 1:
            raise JournalDenied("grant transition history denied")
        event = exact(
            transitions[0],
            (
                "event", "generation", "binding", "config", "transition_reference",
                "metadata_checkpoint", "metadata_binding", "metadata_evidence_head",
                "metadata_payload_sha256", "metadata_observation_id",
                "metadata_evidence_id", "expected_witness_sha256", "at",
                "sequence", "previous",
            ),
        )
        config = json.loads(_json(event["config"]))
        transition_record_config(config, self.transition_policy)
        for name in (
            "binding", "transition_reference", "metadata_binding",
            "metadata_evidence_head", "metadata_payload_sha256",
            "expected_witness_sha256",
        ):
            _reference(event[name])
        checkpoint = checkpoints.get(event["metadata_checkpoint"])
        references = [] if checkpoint is None else checkpoint["references"]
        if (
            event["generation"] != 1
            or event["binding"] != digest(config)
            or event["previous"] != event["metadata_evidence_head"]
            or checkpoint is None
            or checkpoint["binding"] != event["metadata_binding"]
            or checkpoint["payload_sha256"] != event["metadata_payload_sha256"]
            or event["metadata_checkpoint"] not in payloads
            or len(references) != 1
            or references[0]["observation_id"] != event["metadata_observation_id"]
            or references[0]["evidence_id"] != event["metadata_evidence_id"]
        ):
            raise JournalDenied("grant transition metadata lineage denied")
        datetime.fromisoformat(event["at"])
        return config

    def _verify(self, db):
        if (
            db.execute("SELECT 1 FROM events WHERE length(body)>? LIMIT 1", (MAX_FRAME,)).fetchone()
            or db.execute(
                "SELECT 1 FROM payloads WHERE length(body)>? LIMIT 1", (MAX_FRAME,)
            ).fetchone()
        ):
            raise JournalDenied("oversized evidence storage entry")
        transition_allowance = 1 if self.transition_policy is not None else 0
        rows = db.execute(
            "SELECT sequence,body,mac FROM events ORDER BY sequence LIMIT ?",
            (2 * self.policy["max_entries"] + self.semantic_limit + transition_allowance + 2,),
        ).fetchall()
        if (not rows or len(rows)
                > 2 * self.policy["max_entries"] + self.semantic_limit + transition_allowance + 1):
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
            elif body["event"] not in (
                "configure", "append", "semantic_checkpoint", "grant_transition"
            ):
                raise JournalDenied("evidence checkpoint event denied")
            events.append(body)
            previous = mac
        if sum(e["event"] == "semantic_checkpoint" for e in events) > self.semantic_limit:
            raise JournalDenied("bounded semantic custody pin required")
        if previous != self.head:
            raise JournalDenied("evidence history rollback detected")
        if (
            events[0]["event"] != "configure"
            or events[0]["scopes"] != self.enrolled_scopes
            or events[0]["policy"] != self.policy
            or events[0].get("semantic_limit", 1) != self.semantic_limit
            or events[0].get("grant_transition_sha256") != (
                digest(self.transition_policy) if self.transition_policy is not None else None
            )
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
        self._transition_from(events, checkpoints, payloads)
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

    def _progress(self, events=None):
        if self.witness_stream is None:
            return None
        if events is None:
            with self._connect() as database:
                events, _, _, _ = self._check(database)
        return progress_state(self.witness_stream, len(events), self.head)

    def _verify_witness(self, state=None):
        if self.witness is not None:
            self.witness("verify", {"state": state or self._progress()})

    def _advance_witness(self, previous, current=None):
        if self.witness is None:
            return
        current = current or self._progress()
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

    def append_semantic_event(self, value):
        """Commit one accepted semantic pin before witness and publication."""
        with self.lock:
            with self._connect() as database:
                database.execute("BEGIN IMMEDIATE")
                events, _, _, _ = self._check(database)
                previous = self._progress(events)
                self._verify_witness(previous)
                sequence = self._append_event(database, value)
            self._pin()
            self._advance_witness(previous)
            return sequence

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
                declarations = (
                    []
                    if proposal["interpretation"] is None
                    else [
                        candidate["declaration"]
                        for candidate in proposal["interpretation"]["candidates"]
                    ]
                )
                if is_erpnext_candidate(config):
                    rebuilt = erpnext_proposal_from(
                        proposal["resource"],
                        proposal["fields"],
                        proposal["date_fields"],
                        declarations,
                    )
                else:
                    rebuilt = (
                        ScopeProposal(
                            proposal["resource"],
                            tuple(proposal["fields"]),
                            tuple(proposal["date_fields"]),
                        )
                        if proposal["interpretation"] is None
                        else proposal_from(proposal["resource"], declarations)
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

    def _metadata_transition_challenge(self, binding, events, checkpoints, expired, payloads):
        if self.transition_policy is None or any(
            event["event"] == "grant_transition"
            for event in events
        ):
            raise JournalDenied("grant transition unavailable")
        candidates = [
            checkpoint
            for checkpoint in checkpoints.values()
            if checkpoint["binding"] == binding
            and checkpoint["sequence"] not in expired
            and checkpoint["sequence"] in payloads
        ]
        if not candidates:
            raise JournalDenied("retained admitted metadata required")
        checkpoint = max(candidates, key=lambda value: value["sequence"])
        references = checkpoint["references"]
        if len(references) != 1:
            raise JournalDenied("exact metadata transition reference required")
        reference = references[0]
        return {
            "binding": binding,
            "checkpoint": checkpoint["sequence"],
            "evidence_head": self.head,
            "payload_sha256": checkpoint["payload_sha256"],
            "journal_head": checkpoint["journal_head"],
            "request_reference": checkpoint["request_reference"],
            "observation_id": reference["observation_id"],
            "evidence_id": reference["evidence_id"],
        }

    def _validate_transition_scope(self, request, payloads):
        grant = transition_record_config(request["config"], self.transition_policy)
        metadata = request["metadata"]
        values = json.loads(payloads[metadata["checkpoint"]])
        if type(values) is not list or len(values) != 1:
            raise JournalDenied("one retained metadata observation required")
        payload = values[0]["evidence"]["payload"]
        proposals = [
            proposal for proposal in payload["proposals"]
            if proposal["resource"] == grant.window.resource
        ]
        if (
            len(proposals) != 1
            or tuple(proposals[0]["fields"]) != grant.window.fields
            or grant.window.date_field not in proposals[0]["date_fields"]
            or grant.identity_field not in proposals[0]["fields"]
            or grant.company_field not in proposals[0]["fields"]
        ):
            raise JournalDenied("record grant not supported by retained metadata")
        return grant

    def dispatch(self, role, action, value):
        if (role, action) not in {
            ("supervisor", "append"),
            ("supervisor", "availability"),
            ("supervisor", "transition"),
            ("owner", "inspect"),
            ("owner", "load"),
            ("owner", "prune"),
            ("owner", "resolve"),
            ("owner", "transition_challenge"),
            ("owner", "provision"),
            ("owner", "transition"),
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
            else ("transition",)
            if action == "provision"
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
                events, _, _, _ = self._check(db)
                previous = self._progress(events)
                self._verify_witness(previous)
                self._prune(db)
            # Retention acknowledgements are durable even if the next request is
            # rejected. Never roll back erased payloads as part of a denied read.
            self._pin()
            self._advance_witness(previous)
            with self._connect() as db:
                db.execute("BEGIN IMMEDIATE")
                events, checkpoints, expired, payloads = self._check(db)
                previous = self._progress(events)
                self._verify_witness(previous)
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
                elif action == "transition_challenge":
                    result = {
                        "version": self.transition_policy["version"],
                        "deployment_identity": self.deployment_identity,
                        "generation": 1,
                        "predecessor_generation": 0,
                        "metadata": self._metadata_transition_challenge(
                            binding, events, checkpoints, expired, payloads
                        ),
                    }
                elif action == "provision":
                    request = transition_request_from(
                        args["transition"], self.transition_policy, self.deployment_identity
                    )
                    challenge = self._metadata_transition_challenge(
                        binding, events, checkpoints, expired, payloads
                    )
                    if request["metadata"] != challenge:
                        raise JournalDenied("stale metadata transition challenge denied")
                    self._validate_transition_scope(request, payloads)
                    reference = digest({
                        name: request[name] for name in request if name != "mac"
                    })
                    self._append_event(
                        db,
                        {
                            "event": "grant_transition",
                            "generation": 1,
                            "binding": digest(request["config"]),
                            "config": request["config"],
                            "transition_reference": reference,
                            "metadata_checkpoint": challenge["checkpoint"],
                            "metadata_binding": challenge["binding"],
                            "metadata_evidence_head": challenge["evidence_head"],
                            "metadata_payload_sha256": challenge["payload_sha256"],
                            "metadata_observation_id": challenge["observation_id"],
                            "metadata_evidence_id": challenge["evidence_id"],
                            "expected_witness_sha256": request["expected_witness_sha256"],
                            "at": self._now().isoformat(),
                        },
                    )
                    self.configs[digest(request["config"])] = json.loads(
                        _json(request["config"])
                    )
                    result = {
                        "status": "evidence_committed",
                        "generation": 1,
                        "binding": digest(request["config"]),
                        "transition_reference": reference,
                        "head": self.head,
                    }
                elif action == "transition":
                    config = self._transition_from(events, checkpoints, payloads)
                    transition_event = next(
                        (event for event in events if event["event"] == "grant_transition"),
                        None,
                    )
                    result = {
                        "status": "provisioned" if config is not None else "unprovisioned",
                        "generation": 1 if config is not None else 0,
                        "config": config,
                        "head": self.head,
                        "audit_provision": (
                            {
                                "transition_reference": transition_event["transition_reference"],
                                "metadata_checkpoint": transition_event["metadata_checkpoint"],
                                "metadata_evidence_head": transition_event[
                                    "metadata_evidence_head"
                                ],
                                "predecessor_generation": 0,
                            }
                            if transition_event is not None
                            else None
                        ),
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
            self._advance_witness(previous)
            return result
