"""Canonical semantic recovery within the existing protected evidence owner.

Version one retains structural UNKNOWN and one accepted checkpoint. Version two
composes separately authorized instrument originals with a reviewed collector
registry and bounded successive canonical checkpoints. Registry correctness,
policy, clock, custody and host remain trusted; integrity is not truth or real
collector independence, and privileged whole-store/tip rollback is not proven.
"""

import json
import sqlite3
from dataclasses import asdict

from ..contracts import EvidenceKind
from ..discovery.checkpoint import StudyCheckpointIntegrityError
from ..discovery.pilot_read import PilotRequest
from ..stores.sqlite_checkpoint import SQLiteStudyCheckpointStore
from ..understanding.role_checkpoint import _json, _plain, checkpoint_sha256, checkpoint_study
from ..understanding.role_study import RoleStudy
from ..understanding.semantic_checkpoint import checkpoint_semantic
from ..understanding.semantic_rules import RULES
from ..understanding.semantic_study import (
    AGGREGATE_FIELDS,
    ANCHOR_FIELDS,
    SEMANTIC_EVALUATOR_VERSION,
    Instrument,
    Origin,
    SemanticStudy,
)
from .broker_contract import (
    MAX_FRAME,
    digest,
    exact,
    grant_from,
    is_record_operation,
    metadata_grant_from,
    observations_from,
)
from .journal import JournalDenied, grant_digest


def semantic_policy_sha256(instruments=()):
    descriptors = [asdict(i) if type(i) is Instrument else i for i in instruments]
    return checkpoint_sha256(_json({"rules": [asdict(r) for r in RULES], "instruments": descriptors}))


def _identity(value, maximum=200):
    if type(value) is not str or not value.strip() or value != value.strip() or len(value) > maximum:
        raise ValueError("bounded reviewed identity required")
    return value


def _anchor(value):
    exact(value, ("source_id", "resource", "record_id"))
    return tuple(_identity(value[k]) for k in ("source_id", "resource", "record_id"))


def validate_semantic_config(value, *, configs=None):
    version = value.get("version") if type(value) is dict else None
    exact(value, ("version", "study_id", "evaluator_version", "policy_sha256")
          + (("instruments", "collector_registry") if version == 2 else ()))
    instruments = ()
    if version == 2:
        if type(value["instruments"]) is not list or not 1 <= len(value["instruments"]) <= 8:
            raise ValueError("bounded reviewed instruments required")
        items = []
        for descriptor in value["instruments"]:
            exact(descriptor, ("source_id", "resource", "provenance_source", "classes"))
            classes = descriptor["classes"]
            if (type(classes) is not list or not 1 <= len(classes) <= 5
                    or any(type(c) is not str for c in classes)
                    or len(set(classes)) != len(classes)
                    or not set(classes) <= {"process", "temporal", "relationship", "aggregate", "organizational"}):
                raise ValueError("reviewed instrument classes required")
            items.append(Instrument(*(_identity(descriptor[k]) for k in
                ("source_id", "resource", "provenance_source")), tuple(classes)))
        instruments = tuple(items)
        if len({i.source_id for i in instruments}) != len(instruments):
            raise ValueError("duplicate reviewed instrument source")
        registry = value["collector_registry"]
        if type(registry) is not list or not 1 <= len(registry) <= 100:
            raise ValueError("bounded reviewed collector registry required")
        anchors = {}
        for entry in registry:
            exact(entry, ("source_id", "resource", "record_id", "roots", "parents"))
            key = _anchor({k: entry[k] for k in ("source_id", "resource", "record_id")})
            instrument = next((i for i in instruments if i.source_id == key[0]), None)
            if instrument is None or instrument.resource != key[1] or key in anchors:
                raise ValueError("reviewed collector instrument scope mismatch")
            roots, parents = entry["roots"], entry["parents"]
            if (type(roots) is not list or not 1 <= len(roots) <= 8
                    or type(parents) is not list or len(parents) > 8):
                raise ValueError("bounded origin roots and parents required")
            normalized = []
            for root in roots:
                if type(root) is not list or len(root) != 2:
                    raise ValueError("reviewed collector root required")
                normalized.append(tuple(_identity(v, 128) for v in root))
            if len(set(normalized)) != len(normalized):
                raise ValueError("duplicate origin roots")
            refs = tuple(_anchor(p) for p in parents)
            if len(set(refs)) != len(refs):
                raise ValueError("duplicate origin parents")
            anchors[key] = (set(normalized), refs)

        def lineage(key, trail=()):
            if key not in anchors or key in trail or len(trail) > 8:
                raise ValueError("missing, cyclic or excessive reviewed lineage")
            roots, parents = anchors[key]
            inherited = set().union(*(lineage(p, (*trail, key)) for p in parents)) if parents else roots
            if roots != inherited:
                raise ValueError("derived collector cannot invent roots")
            return roots

        for key in anchors:
            lineage(key)
    if (type(value["version"]) is not int or value["version"] not in (1, 2)
            or type(value["study_id"]) is not str or not value["study_id"].strip()
            or len(value["study_id"]) > 200
            or value["evaluator_version"] != SEMANTIC_EVALUATOR_VERSION
            or value["policy_sha256"] != semantic_policy_sha256(instruments)):
        raise ValueError("fixed bounded semantic policy required")
    if version == 2 and configs is not None:
        _validate_bindings(instruments, configs)
    return dict(value)


def _validate_bindings(instruments, configs):
    structural = [c for c in configs if c["operation"] == "read"]
    metadata = [c for c in configs if c["operation"] == "metadata"]
    independent = [c for c in configs if c["operation"] not in ("metadata", "read")]
    if len(structural) != 1 or len(metadata) != 1 or not 1 <= len(independent) <= 8:
        raise JournalDenied("separate structural and instrument scopes required")
    base = grant_from(structural[0]["grant"])
    m = metadata_grant_from(metadata[0]["grant"]).request
    if (m.tenant_id, m.company, m.source_id) != (base.window.tenant_id, base.window.company, base.source_id):
        raise JournalDenied("structural tenant/company/source mismatch")
    if {grant_from(c["grant"]).source_id for c in independent} != {i.source_id for i in instruments}:
        raise JournalDenied("reviewed instrument sources must match acquisition grants")
    for config in independent:
        grant = grant_from(config["grant"])
        instrument = next(i for i in instruments if i.source_id == grant.source_id)
        if (not is_record_operation(config["operation"]) or instrument.source_id == base.source_id
                or (grant.window.tenant_id, grant.window.company) != (base.window.tenant_id, base.window.company)
                or grant.window.resource != instrument.resource
                or grant.provenance_source != instrument.provenance_source
                or grant.evidence_kind is not EvidenceKind.EXPERIMENT
                or not set(ANCHOR_FIELDS) <= set(grant.window.fields)
                or not set(grant.window.fields) <= set(ANCHOR_FIELDS + AGGREGATE_FIELDS)
                or grant.window.date_field != "on"
                or grant.identity_field != "id" or grant.company_field != "partition"):
            raise JournalDenied("reviewed independent acquisition contract mismatch")


class RuntimeSemanticCustody:
    """No caller claims, archive selectors, source I/O or restored authority."""

    def __init__(self, evidence_owner, config):
        self.owner = evidence_owner
        self.config = validate_semantic_config(config, configs=list(evidence_owner.configs.values()))
        configs = list(evidence_owner.configs.values())
        self.independent = self.config["version"] == 2
        if (sum(c["operation"] == "metadata" for c in configs) != 1
                or sum(c["operation"] == "read" for c in configs) != 1
                or (not self.independent and len(configs) != 2)):
            raise JournalDenied("separate metadata and record archive scopes required")
        self.metadata = next(c for c in configs if c["operation"] == "metadata")
        self.read = next(c for c in configs if c["operation"] == "read")
        grant = grant_from(self.read["grant"])
        w = grant.window
        self.request = PilotRequest(w.tenant_id, w.company, grant.source_id,
                                   w.resource, w.fields, w.date_field, w.start,
                                   w.end, grant.max_records)
        m = metadata_grant_from(self.metadata["grant"]).request
        if (m.tenant_id, m.company, m.source_id) != (w.tenant_id, w.company, grant.source_id):
            raise JournalDenied("semantic archive scope mismatch")
        self.scope = {"tenant_id": w.tenant_id, "company": w.company,
                      "source_id": grant.source_id, "study_id": self.config["study_id"]}
        self.binding = digest({"scope": self.scope, "config": self.config,
                               "archive_bindings": sorted(evidence_owner.configs)})
        self.instruments = tuple(Instrument(i["source_id"], i["resource"], i["provenance_source"],
                                           tuple(i["classes"])) for i in self.config.get("instruments", []))
        self.instrument_configs = tuple(c for c in configs if c["operation"] not in ("metadata", "read"))
        if self.independent and self.owner.semantic_limit != 100:
            raise JournalDenied("separate reviewed instrument acquisition scopes required")
        self.store = SQLiteStudyCheckpointStore(evidence_owner.path)

    def _load(self):
        lookup = {}
        groups = []
        for config in (self.metadata, self.read):
            loaded = self.owner.dispatch("owner", "load", {
                "binding": digest(config), "arguments": {}})
            values = loaded["observations"]
            if len(values) > 100:
                raise ValueError("semantic original evidence budget exceeded")
            observations = tuple(o for i in range(0, len(values), 25)
                                 for o in observations_from(values[i:i + 25]))
            for observation in observations:
                key = observation.evidence.evidence_id
                if key in lookup:
                    raise ValueError("ambiguous original evidence identity")
                lookup[key] = observation
                if config["operation"] == "read":
                    p = observation.evidence.payload["provenance"]
                    grant = grant_from(config["grant"])
                    if (p["authorization_id"] != grant.authorization_id
                            or p["scope_sha256"] != grant_digest(grant)
                            or p["source_id"] != self.request.source_id):
                        raise ValueError("original record acquisition scope mismatch")
                    lookup[("scope", key)] = self.request
            groups.append(observations)
        return lookup.get, groups

    def _pin(self):
        with self.owner._connect() as db:
            events, _, _, _ = self.owner._check(db)
        pins = [e for e in events if e["event"] == "semantic_checkpoint"]
        if pins and (pins[0].get("binding") != self.binding
                     or pins[0].get("sequence_index") != 1):
            raise ValueError("semantic study scope or reviewed policy changed")
        return pins[0] if pins else None

    def _load_independent(self):
        originals, groups, batches, anchors = {}, [], [], {}
        for config in (self.metadata, self.read, *self.instrument_configs):
            loaded = self.owner.dispatch("owner", "load", {
                "binding": digest(config), "arguments": {}})
            values = loaded["observations"]
            if len(values) > 100:
                raise ValueError("original evidence budget exceeded")
            observations = tuple(o for i in range(0, len(values), 25)
                                 for o in observations_from(values[i:i + 25]))
            for observation in observations:
                key = observation.evidence.evidence_id
                if key in originals:
                    raise ValueError("ambiguous evidence identity")
                originals[key] = observation
                if is_record_operation(config["operation"]):
                    grant = grant_from(config["grant"])
                    w = grant.window
                    scope = PilotRequest(w.tenant_id, w.company, grant.source_id, w.resource,
                                         w.fields, w.date_field, w.start, w.end, grant.max_records)
                    provenance = observation.evidence.payload["provenance"]
                    if (provenance["authorization_id"] != grant.authorization_id
                            or provenance["scope_sha256"] != grant_digest(grant)
                            or provenance["source_id"] != grant.source_id):
                        raise ValueError("original acquisition grant mismatch")
                    originals[("scope", key)] = scope
                    if config["operation"] != "read":
                        anchor = (grant.source_id, w.resource, provenance["source_record_id"])
                        anchors.setdefault(anchor, []).append(key)
            groups.append(observations)
            if config["operation"] not in ("metadata", "read"):
                by_id = {str(o.evidence.evidence_id): o for o in observations}
                for checkpoint in loaded["checkpoints"]:
                    if not checkpoint["retained"]:
                        continue
                    batch = tuple(by_id[r["evidence_id"]] for r in checkpoint["references"])
                    if not 1 <= len(batch) <= 25:
                        raise ValueError("bounded original acquisition batch required")
                    batches.append((checkpoint["sequence"], batch))
        if sum(len(batch) for _, batch in batches) > 100:
            raise ValueError("semantic observation budget exceeded")
        registry = {tuple(e[k] for k in ("source_id", "resource", "record_id")): e
                    for e in self.config["collector_registry"]}
        for anchor, identities in anchors.items():
            if anchor not in registry:
                raise ValueError("original collector anchor unavailable")
            entry = registry[anchor]
            parents = []
            for reference in entry["parents"]:
                candidates = anchors.get(_anchor(reference), [])
                if len(candidates) != 1:
                    raise ValueError("original derivation parent unavailable or ambiguous")
                parents.append(candidates[0])
            for identity in identities:
                originals[("origin", identity)] = Origin(tuple(tuple(r) for r in entry["roots"]),
                                                          tuple(parents))
        return originals.get, groups[:2], tuple(batch for _, batch in sorted(batches))

    def _accepted_rows(self):
        with self.owner._connect() as db:
            events, _, _, _ = self.owner._check(db)
        pins = [e for e in events if e["event"] == "semantic_checkpoint"]
        connection = self.store._connect()
        try:
            rows = self.store._semantic_rows(connection, tuple(self.scope.values()))
        finally:
            connection.close()
        if len(rows) != len(pins):
            raise ValueError("semantic append missing independent custody acceptance")
        previous = None
        for index, (pin, row) in enumerate(zip(pins, rows), 1):
            envelope = json.loads(row[3])
            expected = {"binding": self.binding, "sequence_index": index,
                        "scope": self.scope, "checkpoint_sha256": row[2],
                        "envelope_sha256": row[4], "predecessor_sha256": previous,
                        "policy_sha256": self.config["policy_sha256"],
                        "registry_sha256": digest(self.config["collector_registry"]),
                        "evaluator_version": SEMANTIC_EVALUATOR_VERSION,
                        "dependencies_sha256": envelope["dependencies_sha256"]}
            if any(pin.get(k) != v for k, v in expected.items()):
                raise ValueError("semantic acceptance scope, lineage or chain mismatch")
            previous = row[4]
        return rows

    def _publish_independent(self, study, sequence):
        result = self._publish(study)
        claims = study.claims()
        states = {c.hypothesis.status.upper() for c in claims}
        graph = study.world_model()
        report = dict(study.report())
        # Current claim details and the canonical graph are published once.
        # The report indexes those claims instead of duplicating raw payloads.
        report_index = {k: report[k] for k in ("what_i_validated", "what_i_invalidated",
                                               "what_remains_unknown", "not_allowed")}
        result.update(epistemic_status=next(iter(states)) if len(states) == 1 else "MIXED",
                      reason="canonical_reviewed_rules_evaluated", durable_checkpoint_sequence=sequence,
                      policy_sha256=self.config["policy_sha256"],
                      independent_grounding="reviewed_synthetic_registry; real_collector_independence_NOT_PROVEN",
                      claims=[_plain(asdict(c)) for c in claims],
                      history=[_plain({"revision_id": r.revision_id, "previous": r.previous,
                          "evidence_ids": r.evidence_ids,
                          "claim_nodes": sorted(str(n.node_id) for n in graph._nodes.values()
                              if n.attributes.get("historical") and n.attributes.get("revision") == r.revision_id)})
                          for r in study.history],
                      report=_plain(report_index),
                      required_next_evidence=_plain(report["required_next_evidence"]),
                      authorization_required="separate_instrument_read_grant",
                      lineage=[{"evidence_id": str(o.evidence.evidence_id),
                                "origin": _plain(asdict(study._lookup(("origin", o.evidence.evidence_id)))),
                                "request_sha256": digest(_plain(asdict(study._lookup(("scope", o.evidence.evidence_id)))))}
                               for o in study.evidence_snapshot()],
                      acquisition_scopes={digest(_plain(asdict(study._lookup(("scope", o.evidence.evidence_id))))):
                                          _plain(asdict(study._lookup(("scope", o.evidence.evidence_id))))
                                          for o in study.evidence_snapshot()})
        result["world_model"]["relationships"] = [_plain(asdict(r)) for r in
            sorted(graph._relationships.values(), key=lambda r: str(r.relationship_id))]
        # Explicit wire projection factors shared/default domain fields only;
        # every canonical node, edge, attribute and provenance reference stays.
        # These are output indexes, never persisted graph-as-truth inputs.
        model = result["world_model"]
        model.update(encoding_version=2, node_key_default="node_id",
                     node_defaults={"tenant_id": study.base.tenant, "schema_version": 1, "confidence": None},
                     relationship_defaults={"tenant_id": study.base.tenant, "schema_version": 1,
                                            "confidence": None, "valid_from": None, "valid_until": None})
        for node in model["nodes"]:
            for name in ("tenant_id", "schema_version"):
                if node[name] == model["node_defaults"][name]:
                    del node[name]
            if node["key"] == node["node_id"]:
                del node["key"]
            if node["confidence"] is None:
                del node["confidence"]
        for edge in model["relationships"]:
            for name in ("tenant_id", "schema_version"):
                if edge[name] == model["relationship_defaults"][name]:
                    del edge[name]
            for name in ("confidence", "valid_from", "valid_until"):
                if edge[name] is None:
                    del edge[name]
        result["evidence_references"] = sorted(set(result["evidence_references"]) |
                                              {str(o.evidence.evidence_id) for o in study.evidence_snapshot()})
        if len(_json(result).encode()) > MAX_FRAME:
            raise ValueError("bounded semantic assessment exceeded")
        return result

    def _dispatch_independent(self, mode):
        rows = self._accepted_rows()
        lookup, (schemas, records), batches = self._load_independent()
        restored = self.store.restore_semantic(**self.scope, instruments=self.instruments,
                                               rules=RULES, evidence_lookup=lookup)
        if not schemas or not records:
            return self._unavailable("admitted_metadata_and_records_required")
        if len(schemas) != 1:
            return self._unavailable("structural_base_changed_new_study_required")
        base = RoleStudy(schemas[0], evidence_lookup=lookup)
        for record in records:
            base.observe((record,), request=self.request)
        if restored is not None and checkpoint_study(base) != checkpoint_study(restored.base):
            return self._unavailable("structural_base_changed_new_study_required")
        if mode == "restore":
            if restored is None:
                return self._unavailable("semantic_checkpoint_absent")
            if {o.evidence.evidence_id for batch in batches for o in batch} != {
                    o.evidence.evidence_id for o in restored.evidence_snapshot()}:
                return self._unavailable("admitted_independent_evidence_pending_acceptance")
            return self._publish_independent(restored, len(rows))
        study = restored or SemanticStudy(base, instruments=self.instruments, rules=RULES,
                                         evidence_lookup=lookup)
        old = {o.evidence.evidence_id for o in study.evidence_snapshot()}
        for batch in batches:
            additions = tuple(o for o in batch if o.evidence.evidence_id not in old)
            if additions:
                study.observe(additions)
                old.update(o.evidence.evidence_id for o in additions)
        payload = checkpoint_semantic(study)
        if rows and payload == rows[-1][1]:
            return self._publish_independent(study, len(rows))
        sequence = len(rows) + 1
        if sequence > self.owner.semantic_limit:
            raise ValueError("semantic acceptance capacity exhausted")
        result = self._publish_independent(study, sequence)
        self.store.append_semantic(study, study_id=self.config["study_id"], sequence=sequence)
        connection = self.store._connect()
        try:
            row = self.store._semantic_rows(connection, tuple(self.scope.values()))[-1]
        finally:
            connection.close()
        envelope = json.loads(row[3])
        with self.owner._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            self.owner._check(db)
            self.owner._append_event(db, {"event": "semantic_checkpoint", "binding": self.binding,
                "scope": self.scope, "sequence_index": sequence, "checkpoint_sha256": row[2],
                "envelope_sha256": row[4], "predecessor_sha256": rows[-1][4] if rows else None,
                "policy_sha256": self.config["policy_sha256"],
                "registry_sha256": digest(self.config["collector_registry"]),
                "evaluator_version": SEMANTIC_EVALUATOR_VERSION,
                "dependencies_sha256": envelope["dependencies_sha256"],
                "at": self.owner._now().isoformat()})
        self.owner._pin()
        self._accepted_rows()
        return result

    def _unavailable(self, reason):
        return dict(self.scope, status="UNAVAILABLE", reason=reason,
                    epistemic_status="UNKNOWN", evaluator_version=SEMANTIC_EVALUATOR_VERSION,
                    policy_sha256=self.config["policy_sha256"], durable=False,
                    authority_restored=False, execution_allowed=False,
                    independent_grounding=("reviewed_registry_dependency_unavailable" if self.independent else
                        "BLOCKED: admitted instrument scopes and reviewed origins absent"))

    def _publish(self, study):
        graph = study.world_model()
        payload = checkpoint_semantic(study)
        result = dict(self.scope, status="AVAILABLE", epistemic_status="UNKNOWN",
                    reason="independent_grounding_unavailable", durable=True,
                    evaluator_version=SEMANTIC_EVALUATOR_VERSION,
                    policy_sha256=semantic_policy_sha256(), durable_checkpoint_sequence=1,
                    durable_checkpoint_sha256=checkpoint_sha256(payload),
                    semantic_revision_ids=[r.revision_id for r in study.history],
                    world_model={"nodes": [_plain({
                        "node_id": n.node_id, "node_type": n.node_type,
                        "tenant_id": n.tenant_id, "key": n.key,
                        "status": n.status, "attributes": n.attributes,
                        "confidence": n.confidence, "schema_version": n.schema_version,
                        "provenance_ids": n.provenance_ids})
                        for n in sorted(graph._nodes.values(), key=lambda n: str(n.node_id))],
                        "relationships": []},
                    evidence_references=sorted(str(o.evidence.evidence_id) for o in
                        (study.base.schema, *(o for o, _ in study.base.evidence_snapshot()))),
                    authority_restored=False, execution_allowed=False,
                    independent_grounding="BLOCKED: admitted instrument scopes and reviewed origins absent")
        if len(_json(result).encode()) > MAX_FRAME:
            raise ValueError("semantic assessment publication bound exceeded")
        return result

    def dispatch(self, role, action, value):
        if (role, action) != ("owner", "semantic"):
            raise JournalDenied("semantic caller or operation denied")
        exact(value, ("mode",))
        if value["mode"] not in ("evaluate", "restore"):
            raise JournalDenied("semantic lifecycle mode denied")
        with self.owner.lock:
            try:
                if self.independent:
                    return self._dispatch_independent(value["mode"])
                pin = self._pin()
                lookup, (schemas, records) = self._load()
                restored = self.store.restore_semantic(**self.scope, instruments=(),
                                                        rules=RULES, evidence_lookup=lookup)
                if pin:
                    if (restored is None or checkpoint_sha256(checkpoint_semantic(restored))
                            != pin["checkpoint_sha256"]):
                        raise ValueError("accepted semantic checkpoint missing or changed")
                elif restored is not None:
                    raise ValueError("semantic append not accepted by protected custody")
                if value["mode"] == "restore" and restored is None:
                    return self._unavailable("semantic_checkpoint_absent")
                if not schemas or not records:
                    return self._unavailable("admitted_metadata_and_records_required")
                if len(schemas) != 1:
                    return self._unavailable("structural_base_changed_new_study_required")
                base = RoleStudy(schemas[0], evidence_lookup=lookup)
                for record in records:
                    base.observe((record,), request=self.request)
                study = SemanticStudy(base, instruments=(), rules=RULES, evidence_lookup=lookup)
                study.claims()
                payload = checkpoint_semantic(study)
                if restored is not None and payload != checkpoint_semantic(restored):
                    return self._unavailable("structural_base_changed_new_study_required")
                result = self._publish(study)  # Bound the result before durable append.
                if value["mode"] == "restore":
                    return result
                self.store.append_semantic(study, study_id=self.config["study_id"], sequence=1)
                if pin is None:
                    with self.owner._connect() as db:
                        db.execute("BEGIN IMMEDIATE")
                        self.owner._check(db)
                        self.owner._append_event(db, {"event": "semantic_checkpoint",
                            "binding": self.binding, "sequence_index": 1,
                            "checkpoint_sha256": checkpoint_sha256(payload),
                            "at": self.owner._now().isoformat()})
                    self.owner._pin()
                return result
            except (ValueError, TypeError, KeyError, AttributeError,
                    OSError, sqlite3.Error, StudyCheckpointIntegrityError):
                return self._unavailable("semantic_recovery_or_publication_unavailable")
