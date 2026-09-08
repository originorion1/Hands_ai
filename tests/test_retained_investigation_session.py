from __future__ import annotations

import json
import os
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from pathlib import Path

import pytest
from test_erpnext_bounded_trial import KEY_REF, SECRET_REF
from test_erpnext_learning_comparison import baseline

from orion.discovery import erpnext_learning_comparison as comparison
from orion.discovery import retained_investigation_session as session
from orion.stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore


def missing_baseline(tmp_path):
    inputs, _, _, ledger, _ = baseline(tmp_path)
    inputs = replace(inputs, environment={
        key: value for key, value in inputs.environment.items() if key not in {KEY_REF, SECRET_REF}
    })
    evidence, checkpoint = comparison._paths(inputs)
    store = SQLiteHistoricalEvidenceStore(evidence)
    scope = inputs.live().reviewed_scopes[0]
    batch = store.load_all(tenant_id=inputs.live().tenant_id, resource=scope.entity)[-1]
    record = dict(batch.observations[0].evidence.payload["record"])
    record[scope.fields[0]] = None
    observation = replace(batch.observations[0], evidence=replace(
        batch.observations[0].evidence, payload={"resource": scope.entity, "record": record},
    ))
    batch = replace(batch, sequence=batch.sequence + 1, observations=(observation,))
    store.append(batch)
    return inputs, (ledger, evidence, checkpoint), store, batch


def artifacts(inputs):
    return sorted(inputs.live().report_directory.glob("retained-investigation-*.json"))


def test_private_memory_restart_is_idempotent_and_aggregate_only(tmp_path):
    inputs, sources, _, _ = missing_baseline(tmp_path)
    before = {path: path.read_bytes() for path in sources}
    first = session.run_retained_investigation(inputs)
    path, = artifacts(inputs)
    body = path.read_bytes()
    second = session.run_retained_investigation(replace(inputs))
    assert first == second
    assert first["status"] == "review_candidate"
    assert first["missing_identities"] >= 1
    assert first["business_defect_validated"] is False
    assert first["execution_allowed"] is False
    assert path.stat().st_mode & 0o777 == 0o600
    assert path.read_bytes() == body
    assert {path: path.read_bytes() for path in sources} == before
    private = json.loads(body)
    assert private["schema"] == 1
    assert private["finding"]["evidence"]
    assert private["binding"]["candidate_sha256"] == inputs.candidate_sha256
    assert private["aggregate"] == first
    assert not {"tenant_id", "company", "field", "entity", "binding", "evidence"} & first.keys()


def test_new_source_creates_new_memory_without_overwriting_old(tmp_path):
    inputs, _, store, batch = missing_baseline(tmp_path)
    session.run_retained_investigation(inputs)
    original, = artifacts(inputs)
    body = original.read_bytes()
    store.append(replace(batch, sequence=batch.sequence + 1))
    session.run_retained_investigation(inputs)
    assert len(artifacts(inputs)) == 2
    assert original.read_bytes() == body


def test_concurrent_identical_sessions_publish_one_result(tmp_path):
    inputs, _, _, _ = missing_baseline(tmp_path)
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(session.run_retained_investigation, [inputs, inputs]))
    assert results[0] == results[1]
    assert len(artifacts(inputs)) == 1


@pytest.mark.parametrize("corruption", ["checksum", "tenant", "sequence"])
def test_invalid_retained_source_never_publishes(tmp_path, corruption):
    inputs, (_, evidence, _), _, _ = missing_baseline(tmp_path)
    column = {"checksum": "checksum_sha256", "tenant": "tenant_id", "sequence": "sequence"}[corruption]
    with sqlite3.connect(evidence) as connection:
        connection.execute(
            f"UPDATE orion_historical_evidence SET {column} = ? "
            "WHERE rowid = (SELECT MIN(rowid) FROM orion_historical_evidence)", ("invalid",),
        )
    with pytest.raises((ValueError, TypeError)):
        session.run_retained_investigation(inputs)
    assert artifacts(inputs) == []


@pytest.mark.parametrize("unsafe", ["tampered", "permissions", "symlink"])
def test_unsafe_existing_memory_is_rejected(tmp_path, unsafe):
    inputs, _, _, _ = missing_baseline(tmp_path)
    session.run_retained_investigation(inputs)
    path, = artifacts(inputs)
    if unsafe == "tampered":
        path.write_text("{}")
    elif unsafe == "permissions":
        path.chmod(0o644)
    else:
        target = path.with_suffix(".saved")
        path.rename(target)
        path.symlink_to(target)
    with pytest.raises((ValueError, OSError)):
        session.run_retained_investigation(inputs)


def test_interrupted_publication_leaves_no_partial_memory(tmp_path, monkeypatch):
    inputs, _, _, _ = missing_baseline(tmp_path)
    def interrupted(*args, **kwargs):
        raise OSError("synthetic publication interruption")
    monkeypatch.setattr(session.os, "link", interrupted)
    with pytest.raises(OSError):
        session.run_retained_investigation(inputs)
    assert artifacts(inputs) == []
    assert list(inputs.live().report_directory.glob(".investigation-*.tmp")) == []


def test_process_death_after_link_is_recovered_without_replacing_memory(tmp_path):
    inputs, _, _, _ = missing_baseline(tmp_path)
    expected = session.run_retained_investigation(inputs)
    path, = artifacts(inputs)
    body = path.read_bytes()
    temporary = path.parent / (".investigation-" + "a" * 32 + ".tmp")
    # Exact filesystem state after link succeeds but before temporary unlink.
    os.link(path, temporary)
    assert path.stat().st_nlink == 2
    assert session.run_retained_investigation(inputs) == expected
    assert path.read_bytes() == body
    assert path.stat().st_nlink == 1
    assert not temporary.exists()


def test_unrecognized_hardlink_is_not_removed_or_accepted(tmp_path):
    inputs, _, _, _ = missing_baseline(tmp_path)
    session.run_retained_investigation(inputs)
    path, = artifacts(inputs)
    other = path.parent / "unrelated.json"
    os.link(path, other)
    with pytest.raises(ValueError, match="not recognized"):
        session.run_retained_investigation(inputs)
    assert other.exists()
    assert path.stat().st_nlink == 2


def test_source_mutation_before_publication_is_rejected(tmp_path, monkeypatch):
    inputs, _, store, batch = missing_baseline(tmp_path)
    original = session._artifact
    calls = []
    def mutate_after_snapshot(value):
        result = original(value)
        if not calls:
            store.append(replace(batch, sequence=batch.sequence + 1))
        calls.append(True)
        return result
    monkeypatch.setattr(session, "_artifact", mutate_after_snapshot)
    with pytest.raises(ValueError, match="changed during"):
        session.run_retained_investigation(inputs)
    assert artifacts(inputs) == []


def test_invalid_candidate_binding_fails_before_memory_creation(tmp_path):
    inputs, _, _, _ = missing_baseline(tmp_path)
    with pytest.raises(ValueError):
        session.run_retained_investigation(replace(inputs, candidate_sha256="0" * 64))
    assert artifacts(inputs) == []


@pytest.mark.parametrize("cross_scope", ["company", "field"])
def test_valid_envelope_cannot_introduce_cross_scope_facts(tmp_path, cross_scope):
    inputs, _, store, batch = missing_baseline(tmp_path)
    record = dict(batch.observations[0].evidence.payload["record"])
    record["company" if cross_scope == "company" else "outside_scope"] = "outside"
    observation = replace(batch.observations[0], evidence=replace(
        batch.observations[0].evidence, payload={"resource": batch.resource, "record": record},
    ))
    store.append(replace(batch, sequence=batch.sequence + 1, observations=(observation,)))
    with pytest.raises(ValueError):
        session.run_retained_investigation(inputs)
    assert artifacts(inputs) == []


def test_checkpoint_checksum_failure_never_publishes(tmp_path):
    inputs, (_, _, checkpoint), _, _ = missing_baseline(tmp_path)
    with sqlite3.connect(checkpoint) as connection:
        connection.execute("UPDATE orion_study_checkpoints SET checksum_sha256 = 'invalid'")
    with pytest.raises(ValueError):
        session.run_retained_investigation(inputs)
    assert artifacts(inputs) == []


def test_no_missing_values_saves_honest_no_candidate(tmp_path):
    inputs, _, _, _, _ = baseline(tmp_path)
    report = session.run_retained_investigation(inputs)
    assert report["status"] == "no_candidate"
    assert json.loads(artifacts(inputs)[0].read_bytes())["finding"] is None


def test_nonprivate_destination_is_rejected(tmp_path):
    inputs, _, _, _ = missing_baseline(tmp_path)
    directory: Path = inputs.live().report_directory
    directory.chmod(0o755)
    with pytest.raises(ValueError, match="destination"):
        session.run_retained_investigation(inputs)
    assert artifacts(inputs) == []


def test_planner_remembers_inconclusive_then_moves_to_next_target(tmp_path):
    inputs, sources, store, batch = missing_baseline(tmp_path)
    scope = next(item for item in inputs.live().reviewed_scopes if item.entity == batch.resource)
    record = dict(batch.observations[0].evidence.payload["record"])
    record[scope.fields[1]] = None
    observation = replace(batch.observations[0], evidence=replace(
        batch.observations[0].evidence, payload={"resource": batch.resource, "record": record},
    ))
    store.append(replace(batch, sequence=batch.sequence + 1, observations=(observation,)))
    before = {path: path.read_bytes() for path in sources}
    assert session.run_retained_investigation_planner(inputs)["status"] == "review_candidate"
    prefix = session._disposition_prefix(inputs)
    first_path, = inputs.live().report_directory.glob(prefix + "*.json")
    first = json.loads(first_path.read_bytes())["disposition"]
    assert session.run_retained_investigation_planner(replace(inputs))["status"] == "review_candidate"
    records = [json.loads(path.read_bytes())["disposition"]
               for path in inputs.live().report_directory.glob(prefix + "*.json")]
    assert len({item["field"] for item in records}) == 2
    assert session.run_retained_investigation_planner(inputs)["status"] == "no_candidate"
    assert first["status"] == "inconclusive"
    assert {path: path.read_bytes() for path in sources} == before
    assert artifacts(inputs) == []


def test_planner_recovers_interrupted_disposition_publication(tmp_path):
    inputs, _, _, _ = missing_baseline(tmp_path)
    session.run_retained_investigation_planner(inputs)
    path, = inputs.live().report_directory.glob(session._disposition_prefix(inputs) + "*.json")
    temporary = path.parent / (".investigation-" + "b" * 32 + ".tmp")
    os.link(path, temporary)
    assert session.run_retained_investigation_planner(inputs)["status"] == "no_candidate"
    assert not temporary.exists()
    assert path.stat().st_nlink == 1


def test_planner_rejects_tampered_disposition(tmp_path):
    inputs, _, _, _ = missing_baseline(tmp_path)
    session.run_retained_investigation_planner(inputs)
    path, = inputs.live().report_directory.glob(session._disposition_prefix(inputs) + "*.json")
    original = path.read_bytes()
    path.write_bytes(original.replace(b'"inconclusive"', b'"validated"'))
    with pytest.raises(ValueError, match="digest"):
        session.run_retained_investigation_planner(inputs)
