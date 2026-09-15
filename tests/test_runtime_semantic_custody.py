"""Offline admitted originals -> canonical semantics -> the existing database."""

import copy
import json
import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest
import test_evidence_custody
from test_evidence_custody import append, call, reopen, values

from orion.discovery.pilot_metadata import launch_pilot_metadata
from orion.history.evidence import _observation_to_data
from orion.pilot.broker_metadata import proposal_from
from orion.pilot.journal import JournalDenied
from orion.pilot.semantic_runtime import (
    RuntimeSemanticCustody,
    semantic_policy_sha256,
    validate_semantic_config,
)
from orion.understanding.role_checkpoint import _json
from orion.understanding.semantic_study import SEMANTIC_EVALUATOR_VERSION


@pytest.fixture
def archive(tmp_path):
    return test_evidence_custody.archive.__wrapped__(tmp_path)


@pytest.fixture
def semantic(archive):
    r, m, _, now, _, owner = archive

    class Metadata:
        source_id = m.request.source_id

        def catalog(self, permit, maximum):
            permit.claim_io(self.source_id)
            return (r.request.resource,), True

        def schema(self, permit, resource):
            permit.claim_io(self.source_id)
            return proposal_from(resource, [{"resource": resource, "name": field,
                "kind": "number" if field == "f_d" else "date",
                "source_type": "synthetic_v1"} for field in ("f_c", "f_d")])

    result = launch_pilot_metadata(m.request, authorization_id=m.grant.authorization_id,
                                  lookup=lambda _: m.grant, adapter=Metadata(),
                                  clock=lambda: now[0])
    metadata = [_observation_to_data(replace(o, evidence=replace(o.evidence,
        payload=json.loads(_json(o.evidence.payload))))) for o in result.observations]
    append(owner, m, metadata)
    append(owner, r, values(r, now[0], 9), "2" * 64)
    config = {"version": 1, "study_id": "offline-study-169",
              "evaluator_version": SEMANTIC_EVALUATOR_VERSION,
              "policy_sha256": semantic_policy_sha256()}
    return archive, config, RuntimeSemanticCustody(owner, config)


def run(runtime, mode="evaluate"):
    return runtime.dispatch("owner", "semantic", {"mode": mode})


def test_admitted_unknown_persists_reference_only_in_existing_archive(semantic):
    archive, _, runtime = semantic
    result = run(runtime)
    assert result["status"] == "AVAILABLE"
    assert result["epistemic_status"] == "UNKNOWN"
    assert result["semantic_revision_ids"] == []  # Do not manufacture instrument revisions.
    assert result["durable_checkpoint_sequence"] == 1
    assert result["authority_restored"] is False and result["execution_allowed"] is False
    assert result["world_model"]["nodes"]
    assert all(n["status"] == "unknown" for n in result["world_model"]["nodes"]
               if n["node_type"] == "knowledge")
    with sqlite3.connect(archive[-1].path) as db:
        assert db.execute("SELECT COUNT(*) FROM orion_semantic_checkpoints").fetchone() == (1,)
        payload = db.execute("SELECT payload_json FROM orion_semantic_checkpoints").fetchone()[0]
        assert '"record"' not in json.loads(payload)["base"]
    assert {p.name for p in archive[2].iterdir()} <= {
        "accepted-evidence-head", "evidence.db", "evidence.db-wal", "evidence.db-shm"}


def test_owner_restart_and_exact_replay_recompute_identical_assessment(semantic):
    archive, config, runtime = semantic
    result = run(runtime)
    restarted = RuntimeSemanticCustody(reopen(archive), config)
    assert run(restarted, "restore") == result
    assert run(restarted) == result
    with sqlite3.connect(archive[-1].path) as db:
        assert db.execute("SELECT COUNT(*) FROM orion_semantic_checkpoints").fetchone() == (1,)
        assert sum(json.loads(row[0])["event"] == "semantic_checkpoint"
                   for row in db.execute("SELECT body FROM events")) == 1


def test_new_structural_record_does_not_invent_independent_revision(semantic):
    archive, _, runtime = semantic
    assert run(runtime)["status"] == "AVAILABLE"
    r, _, _, now, _, owner = archive
    now[0] += timedelta(seconds=1)
    append(owner, r, values(r, now[0], -9), "3" * 64)
    changed = run(runtime)
    assert changed["status"] == "UNAVAILABLE"
    assert changed["reason"] == "structural_base_changed_new_study_required"
    assert "world_model" not in changed
    assert run(runtime, "restore")["reason"] == "structural_base_changed_new_study_required"
    assert len(call(owner, r, "load")["observations"]) == 2


def test_expiry_removes_dependencies_not_history_and_never_returns_cached_graph(semantic):
    archive, _, runtime = semantic
    assert run(runtime)["status"] == "AVAILABLE"
    archive[3][0] += timedelta(seconds=61)
    result = run(runtime, "restore")
    assert result["status"] == "UNAVAILABLE" and result["durable"] is False
    assert "world_model" not in result
    with sqlite3.connect(archive[-1].path) as db:
        assert db.execute("SELECT COUNT(*) FROM payloads").fetchone() == (0,)
        assert db.execute("SELECT COUNT(*) FROM orion_semantic_checkpoints").fetchone() == (1,)


@pytest.mark.parametrize("mutation", ["missing", "changed", "index_missing", "index_changed"])
def test_missing_changed_originals_or_checkpoint_reject_recovery(semantic, mutation):
    archive, _, runtime = semantic
    assert run(runtime)["status"] == "AVAILABLE"
    with sqlite3.connect(archive[-1].path) as db:
        if mutation == "missing":
            db.execute("DELETE FROM payloads WHERE sequence=2")
        elif mutation == "changed":
            db.execute("UPDATE payloads SET body='[]' WHERE sequence=2")
        elif mutation == "index_missing":
            db.execute("DELETE FROM orion_semantic_checkpoints")
        else:
            db.execute("UPDATE orion_semantic_checkpoints SET payload_json='{}'")
    result = run(runtime, "restore")
    assert result["status"] == "UNAVAILABLE" and "world_model" not in result


def test_failed_append_and_unaccepted_append_do_not_publish(semantic, monkeypatch):
    _, _, runtime = semantic

    def fail(*args, **kwargs):
        raise sqlite3.OperationalError("synthetic unavailable disk")

    monkeypatch.setattr(runtime.store, "append_semantic", fail)
    result = run(runtime)
    assert result["status"] == "UNAVAILABLE" and "world_model" not in result


def test_failed_custody_pin_keeps_unaccepted_checkpoint_unavailable(semantic, monkeypatch):
    _, _, runtime = semantic
    original = runtime.owner._append_event

    def fail(db, value):
        if value["event"] == "semantic_checkpoint":
            raise sqlite3.OperationalError("synthetic failed custody acceptance")
        return original(db, value)

    monkeypatch.setattr(runtime.owner, "_append_event", fail)
    assert run(runtime)["status"] == "UNAVAILABLE"
    assert run(runtime, "restore")["status"] == "UNAVAILABLE"


def test_assessment_bound_checked_before_append(semantic, monkeypatch):
    archive, _, runtime = semantic
    monkeypatch.setattr("orion.pilot.semantic_runtime.MAX_FRAME", 100)
    assert run(runtime)["status"] == "UNAVAILABLE"
    with sqlite3.connect(archive[-1].path) as db:
        assert db.execute("SELECT COUNT(*) FROM orion_semantic_checkpoints").fetchone() == (0,)


def test_archive_without_admitted_inputs_distinguishes_absent_checkpoint(archive):
    config = {"version": 1, "study_id": "offline", "evaluator_version": SEMANTIC_EVALUATOR_VERSION,
              "policy_sha256": semantic_policy_sha256()}
    runtime = RuntimeSemanticCustody(archive[-1], config)
    assert run(runtime, "restore")["reason"] == "semantic_checkpoint_absent"
    assert run(runtime)["reason"] == "admitted_metadata_and_records_required"


@pytest.mark.parametrize("field,value", [("version", True), ("version", 2),
    ("study_id", ""), ("evaluator_version", "custom"), ("policy_sha256", "0" * 64)])
def test_configuration_rejects_unreviewed_policy(field, value):
    config = {"version": 1, "study_id": "offline", "evaluator_version": SEMANTIC_EVALUATOR_VERSION,
              "policy_sha256": semantic_policy_sha256()}
    config[field] = value
    with pytest.raises(ValueError):
        validate_semantic_config(config)


def test_changed_study_scope_cannot_restore_or_fork_accepted_pin(semantic):
    archive, config, runtime = semantic
    assert run(runtime)["status"] == "AVAILABLE"
    changed = RuntimeSemanticCustody(reopen(archive), dict(config, study_id="another"))
    assert run(changed, "restore")["status"] == "UNAVAILABLE"
    assert run(changed)["status"] == "UNAVAILABLE"


@pytest.mark.parametrize("role", ["broker", "reasoner", "supervisor", "gateway"])
def test_caller_cannot_supply_claims_or_select_archive(role, semantic):
    _, _, runtime = semantic
    with pytest.raises(JournalDenied):
        runtime.dispatch(role, "semantic", {"mode": "evaluate"})
    with pytest.raises(ValueError):
        runtime.dispatch("owner", "semantic", {"mode": "evaluate", "claims": ["validated"]})


def test_scope_mismatch_rejected_before_semantic_store_access(semantic):
    archive, config, _ = semantic
    changed = copy.deepcopy(archive[-1].configs)
    read = next(c for c in changed.values() if c["operation"] == "read")
    read["grant"]["window"]["company"] = "different-company"
    archive[-1].configs = changed
    with pytest.raises(JournalDenied):
        RuntimeSemanticCustody(archive[-1], config)
