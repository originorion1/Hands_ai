"""Offline originals exercise the deployed custody composition, not an E2E oracle."""

import copy
import json
import sqlite3
from dataclasses import asdict, replace
from datetime import timedelta

import pytest
from independent_evidence_support import build_independent_configs
from test_broker_metadata import MetadataHarness
from test_evidence_custody import KEY
from test_supervised_broker import Harness

from orion.contracts import Evidence, Observation, utc_now
from orion.discovery.pilot_metadata import launch_pilot_metadata
from orion.discovery.pilot_read import PilotRequest, _admit
from orion.history.evidence import _observation_to_data
from orion.pilot.broker_contract import digest, grant_from, metadata_grant_from
from orion.pilot.broker_metadata import proposal_from
from orion.pilot.evidence_custody import EvidenceCustody
from orion.pilot.journal import JournalDenied
from orion.pilot.semantic_runtime import RuntimeSemanticCustody, validate_semantic_config
from orion.understanding.role_checkpoint import _json, _plain
from orion.understanding.semantic_rules import RULES


@pytest.fixture
def independent(tmp_path):
    r, m = Harness(tmp_path / "r"), MetadataHarness(tmp_path / "m")
    bodies = {"metadata": json.dumps({"schemas": {"r_01": [
        {"name": f, "kind": k, "classification": "public"} for f, k in
        (("f_a", "reference"), ("f_b", "reference"), ("f_c", "date"), ("f_d", "number"))]}}),
        "read": json.dumps({"resource": "r_01", "rows": r.rows})}
    configs, bodies, semantic = build_independent_configs([m.config, r.config], bodies,
                                                         "independent_revision")
    semantic = json.loads(_json(semantic))
    directory = tmp_path / "evidence"
    directory.mkdir(mode=0o700)
    now = [utc_now()]
    policy = {"max_entries": 20, "max_bytes": 1048576, "ttl_seconds": 60}
    owner = EvidenceCustody(directory, KEY, configs, policy, clock=lambda: now[0], semantic_limit=100)
    metadata = metadata_grant_from(configs[0]["grant"])
    schemas = json.loads(bodies["metadata"])["schemas"]

    class Adapter:
        source_id = metadata.request.source_id

        def catalog(self, permit, maximum):
            permit.claim_io(self.source_id)
            return tuple(schemas), True

        def schema(self, permit, resource):
            permit.claim_io(self.source_id)
            return proposal_from(resource, [{"resource": resource, "name": f["name"], "kind": f["kind"],
                                             "source_type": "synthetic_v1"} for f in schemas[resource]
                                           if f["kind"] in ("number", "date", "reference")])

    result = launch_pilot_metadata(metadata.request, authorization_id=metadata.authorization_id,
                                  lookup=lambda _: metadata, adapter=Adapter(), clock=lambda: now[0])
    observations = [_observation_to_data(replace(o, evidence=replace(o.evidence,
                    payload=json.loads(_json(o.evidence.payload))))) for o in result.observations]

    def append(index, rows=None):
        config = configs[index]
        now[0] += timedelta(seconds=1)
        if index == 0:
            values = observations
        else:
            grant = grant_from(config["grant"])
            w = grant.window
            request = PilotRequest(w.tenant_id, w.company, grant.source_id, w.resource, w.fields,
                                   w.date_field, w.start, w.end, grant.max_records)
            rows = json.loads(bodies[config["operation"]])["rows"] if rows is None else rows
            raw = tuple(Observation(Evidence(grant.evidence_kind, grant.provenance_source,
                        {"resource": w.resource, "record": row}, observed_at=now[0],
                        tenant_id=w.tenant_id)) for row in rows)
            values = [_observation_to_data(o) for o in _admit(raw, request, grant, now[0])]
        return owner.dispatch("supervisor", "append", {"binding": digest(config), "arguments": {
            "observations": values, "journal_head": "a" * 64,
            "request_reference": f"{index + int(now[0].timestamp()):064x}"}})

    append(0)
    append(1)
    runtime = RuntimeSemanticCustody(owner, semantic)
    return runtime, append, now, configs, semantic, directory, policy


def run(runtime, mode="evaluate"):
    return runtime.dispatch("owner", "semantic", {"mode": mode})


def role(result, field="f_d"):
    return next(c for c in result["claims"] if c["field"] == field and c["rule"]["role"] == "monetary_measure")


def test_successive_convergence_replacement_and_owner_restart(independent):
    runtime, append, now, configs, semantic, directory, policy = independent
    unknown = run(runtime)
    assert unknown["status"] == "AVAILABLE", unknown
    assert all(c["hypothesis"]["status"] == "unknown" for c in unknown["claims"])
    append(2)
    first = run(runtime)
    assert first["status"] == "AVAILABLE", first
    assert role(first)["hypothesis"]["status"] == "unknown"
    append(3)
    convergence = run(runtime)
    assert convergence["status"] == "AVAILABLE", convergence
    assert role(convergence)["hypothesis"]["status"] == "validated"
    assert role(convergence, "f_e")["hypothesis"]["status"] == "invalidated"
    append(4)
    assert run(runtime)["status"] == "AVAILABLE"
    append(5)
    revision = run(runtime)
    assert revision["status"] == "AVAILABLE", revision
    assert role(revision)["hypothesis"]["status"] == "invalidated"
    assert role(revision, "f_e")["hypothesis"]["status"] == "validated"
    assert len(revision["history"]) == 4 and revision["durable_checkpoint_sequence"] == 5
    assert len(revision["evidence_references"]) > len(convergence["evidence_references"])
    assert run(runtime) == revision
    restored = RuntimeSemanticCustody(EvidenceCustody(directory, KEY, configs, policy,
        clock=lambda: now[0], semantic_limit=100), semantic)
    assert run(restored, "restore") == revision
    assert revision["execution_allowed"] is False and revision["authority_restored"] is False
    assert len(_json(revision).encode()) <= 65536
    lookup, _, _ = runtime._load_independent()
    study = runtime.store.restore_semantic(**runtime.scope, instruments=runtime.instruments,
                                           rules=RULES, evidence_lookup=lookup)
    canonical = runtime._publish(study)["world_model"]
    graph = study.world_model()
    model = revision["world_model"]
    expanded = [dict(model["node_defaults"], **n) for n in model["nodes"]]
    for node in expanded:
        node.setdefault("key", node["node_id"])
    assert expanded == canonical["nodes"]
    assert [dict(model["relationship_defaults"], **e) for e in model["relationships"]] == [
        _plain(asdict(r)) for r in sorted(graph._relationships.values(), key=lambda r: str(r.relationship_id))]


@pytest.mark.parametrize("mutation", ["missing", "changed", "expired"])
def test_original_loss_never_publishes_stale_validated_graph(independent, mutation):
    runtime, append, now, *_ = independent
    run(runtime)
    append(2)
    append(3)
    assert run(runtime)["status"] == "AVAILABLE"
    if mutation == "expired":
        now[0] += timedelta(seconds=61)
    else:
        with sqlite3.connect(runtime.owner.path) as db:
            if mutation == "missing":
                db.execute("DELETE FROM payloads WHERE sequence=(SELECT MAX(sequence) FROM payloads)")
            else:
                db.execute("UPDATE payloads SET body='[]' WHERE sequence=(SELECT MAX(sequence) FROM payloads)")
    result = run(runtime, "restore")
    assert result["status"] == "UNAVAILABLE" and "world_model" not in result


@pytest.mark.parametrize("boundary", ["append", "accept"])
def test_failed_durable_boundary_cannot_report_success(independent, monkeypatch, boundary):
    runtime, append, *_ = independent
    assert run(runtime)["status"] == "AVAILABLE"
    append(2)

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic durable failure")

    if boundary == "append":
        monkeypatch.setattr(runtime.store, "append_semantic", fail)
    else:
        monkeypatch.setattr(runtime.owner, "_append_event", fail)
    denied = run(runtime)
    assert denied["status"] == "UNAVAILABLE" and "world_model" not in denied
    if boundary == "accept":
        assert run(runtime, "restore")["status"] == "UNAVAILABLE"
    else:
        result = run(runtime, "restore")
        assert result["status"] == "UNAVAILABLE" and "world_model" not in result


@pytest.mark.parametrize("mutation", ["tenant", "resource", "class", "root", "parent", "policy"])
def test_reviewed_registry_and_grant_binding_rejects_inconsistency(independent, mutation):
    _, _, _, configs, semantic, *_ = independent
    configs, semantic = copy.deepcopy(configs), copy.deepcopy(semantic)
    if mutation == "tenant":
        configs[2]["grant"]["window"]["tenant_id"] = "wrong"
    elif mutation == "resource":
        configs[2]["grant"]["window"]["resource"] = "wrong"
    elif mutation == "class":
        semantic["instruments"][0]["classes"] = ["arbitrary"]
    elif mutation == "root":
        semantic["collector_registry"][0]["roots"] = []
    elif mutation == "parent":
        semantic["collector_registry"][0]["parents"] = [
            {k: "missing" for k in ("source_id", "resource", "record_id")}]
    else:
        semantic["policy_sha256"] = "0" * 64
    with pytest.raises((ValueError, JournalDenied)):
        validate_semantic_config(semantic, configs=configs)


@pytest.mark.parametrize("derived", [False, True])
def test_copied_and_derived_facts_cannot_manufacture_independence(independent, derived):
    original, append, _, _, config, *_ = independent
    config = copy.deepcopy(config)
    first = {e["record_id"].split("-", 1)[1]: e for e in config["collector_registry"]
             if e["record_id"].startswith("a0-")}
    for entry in config["collector_registry"]:
        if entry["source_id"].endswith("collector-1.synthetic.test"):
            parent = first[entry["record_id"].split("-", 1)[1]]
            entry["roots"] = parent["roots"]
            if derived and entry["record_id"].startswith("a1-"):
                entry["parents"] = [{k: parent[k] for k in ("source_id", "resource", "record_id")}]
    runtime = RuntimeSemanticCustody(original.owner, config)
    append(2)
    append(3)
    result = run(runtime)
    assert result["status"] == "AVAILABLE", result
    assert role(result)["hypothesis"]["status"] == "unknown"
    assert role(result)["missing"]


def test_missing_reviewed_anchor_denies_evaluation_without_checkpoint(independent):
    original, append, _, _, config, *_ = independent
    config = copy.deepcopy(config)
    config["collector_registry"] = config["collector_registry"][1:]
    runtime = RuntimeSemanticCustody(original.owner, config)
    append(2)
    result = run(runtime)
    assert result["status"] == "UNAVAILABLE" and "world_model" not in result
    with sqlite3.connect(runtime.owner.path) as db:
        assert db.execute("SELECT COUNT(*) FROM orion_semantic_checkpoints").fetchone() == (0,)


def test_same_fact_changed_without_replacement_is_not_a_revision(independent):
    runtime, append, *_ = independent
    append(2)
    append(3)
    original = run(runtime)
    assert original["status"] == "AVAILABLE"
    rows = [{"id": "a0-x_1", "partition": "c_01", "on": "2024-06-04",
             "subject_source": runtime.request.source_id, "subject_resource": "r_01",
             "subject_id": "x_1", "evidence_class": "process", "channel": "settled_transfer",
             "dimension": "currency", "value": 3, "related_resource": "", "replaces": ""}]
    append(2, rows)
    result = run(runtime)
    assert result["status"] == "UNAVAILABLE" and "world_model" not in result
    with sqlite3.connect(runtime.owner.path) as db:
        assert db.execute("SELECT COUNT(*) FROM orion_semantic_checkpoints").fetchone() == (1,)


def test_accepted_policy_or_registry_identity_cannot_change(independent):
    runtime, _, _, _, config, *_ = independent
    assert run(runtime)["status"] == "AVAILABLE"
    changed = copy.deepcopy(config)
    changed["collector_registry"][0]["roots"][0][0] = "different-reviewed-domain"
    restored = RuntimeSemanticCustody(runtime.owner, changed)
    result = run(restored, "restore")
    assert result["status"] == "UNAVAILABLE" and "world_model" not in result
