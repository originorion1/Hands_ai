"""Fixed semantic lifecycle within the existing protected evidence owner.

The two admitted configurations supply structural metadata and bounded records,
not independently reviewed instruments or original collector lineage. Canonical
semantic evaluation therefore publishes UNKNOWN, never invented grounding.
One reference-only checkpoint is pinned in the existing authenticated custody
history. The trusted owner, clock and host remain dependencies; privileged
rollback of both its database and tip is not proven by this integration.
"""

import sqlite3
from dataclasses import asdict

from ..discovery.checkpoint import StudyCheckpointIntegrityError
from ..discovery.pilot_read import PilotRequest
from ..stores.sqlite_checkpoint import SQLiteStudyCheckpointStore
from ..understanding.role_checkpoint import _json, _plain, checkpoint_sha256
from ..understanding.role_study import RoleStudy
from ..understanding.semantic_checkpoint import checkpoint_semantic
from ..understanding.semantic_rules import RULES
from ..understanding.semantic_study import SEMANTIC_EVALUATOR_VERSION, SemanticStudy
from .broker_contract import (
    MAX_FRAME,
    digest,
    exact,
    grant_from,
    metadata_grant_from,
    observations_from,
)
from .journal import JournalDenied, grant_digest


def semantic_policy_sha256():
    return checkpoint_sha256(_json({"rules": [asdict(r) for r in RULES], "instruments": []}))


def validate_semantic_config(value):
    exact(value, ("version", "study_id", "evaluator_version", "policy_sha256"))
    if (type(value["version"]) is not int or value["version"] != 1
            or type(value["study_id"]) is not str or not value["study_id"].strip()
            or len(value["study_id"]) > 200
            or value["evaluator_version"] != SEMANTIC_EVALUATOR_VERSION
            or value["policy_sha256"] != semantic_policy_sha256()):
        raise ValueError("fixed bounded semantic policy required")
    return dict(value)


class RuntimeSemanticCustody:
    """No caller claims, archive selectors, source I/O or restored authority."""

    def __init__(self, evidence_owner, config):
        self.owner = evidence_owner
        self.config = validate_semantic_config(config)
        configs = list(evidence_owner.configs.values())
        if len(configs) != 2 or {c["operation"] for c in configs} != {"metadata", "read"}:
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

    def _unavailable(self, reason):
        return dict(self.scope, status="UNAVAILABLE", reason=reason,
                    epistemic_status="UNKNOWN", evaluator_version=SEMANTIC_EVALUATOR_VERSION,
                    policy_sha256=semantic_policy_sha256(), durable=False,
                    authority_restored=False, execution_allowed=False,
                    independent_grounding="BLOCKED: admitted instrument scopes and reviewed origins absent")

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
