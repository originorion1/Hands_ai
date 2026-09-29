"""Real SQLite indexing contracts for canonical, reference-only semantics."""

import json
import sqlite3
from hashlib import sha256

import pytest
from semantic_lab import Organization

from orion.discovery.checkpoint import (
    StudyCheckpointConflictError,
    StudyCheckpointIntegrityError,
    StudyCheckpointSequenceError,
)
from orion.stores.sqlite_checkpoint import SQLiteStudyCheckpointStore
from orion.understanding.semantic_rules import RULES
from orion.understanding.semantic_study import SemanticStudy


def restore(store, lab, **changes):
    arguments = {'tenant_id': lab.tenant, 'company': lab.company,
                 'source_id': lab.source, 'study_id': "reviewed-study",
                 'instruments': lab.instruments, 'rules': RULES,
                 'evidence_lookup': lab.archive.get}
    arguments.update(changes)
    return store.restore_semantic(**arguments)


def test_semantic_index_restores_unknown_and_exact_replay(tmp_path):
    lab = Organization()
    store = SQLiteStudyCheckpointStore(tmp_path / "study.sqlite")
    store.append_semantic(lab.study, study_id="reviewed-study", sequence=1)
    store.append_semantic(lab.study, study_id="reviewed-study", sequence=1)
    recovered = restore(SQLiteStudyCheckpointStore(tmp_path / "study.sqlite"), lab)
    assert recovered is not None
    assert all(claim.hypothesis.status == "unknown" for claim in recovered.claims())
    assert recovered.history == ()


def test_semantic_index_extends_but_rejects_forks_and_conflicts(tmp_path):
    lab = Organization()
    lab.records()
    lab.anchors("monetary")
    store = SQLiteStudyCheckpointStore(tmp_path / "study.sqlite")
    store.append_semantic(lab.study, study_id="reviewed-study", sequence=1)
    original_revisions = [revision.revision_id for revision in lab.study.history]
    completion = lab.anchors("completion")
    with pytest.raises(StudyCheckpointConflictError):
        store.append_semantic(lab.study, study_id="reviewed-study", sequence=1)
    with pytest.raises(StudyCheckpointSequenceError):
        store.append_semantic(lab.study, study_id="reviewed-study", sequence=3)
    fork = SemanticStudy(lab.base, instruments=lab.instruments, rules=RULES,
                         evidence_lookup=lab.archive.get)
    fork.observe(completion)
    with pytest.raises(StudyCheckpointConflictError, match="extend"):
        store.append_semantic(fork, study_id="reviewed-study", sequence=2)
    assert [revision.revision_id for revision in restore(store, lab).history] == original_revisions
    store.append_semantic(lab.study, study_id="reviewed-study", sequence=2)
    assert restore(store, lab).history == lab.study.history
    with pytest.raises(StudyCheckpointConflictError, match="extend"):
        store.append_semantic(lab.study, study_id="reviewed-study", sequence=3)


@pytest.mark.parametrize("sequence", [0, -1, 101, True, 1.0, "1"])
def test_semantic_index_rejects_unbounded_or_noninteger_sequence(tmp_path, sequence):
    lab = Organization()
    store = SQLiteStudyCheckpointStore(tmp_path / "study.sqlite")
    with pytest.raises(StudyCheckpointSequenceError):
        store.append_semantic(lab.study, study_id="reviewed-study", sequence=sequence)
    assert restore(store, lab) is None


@pytest.mark.parametrize("field", ["tenant_id", "company", "source_id", "study_id"])
def test_semantic_index_does_not_publish_under_substituted_scope(tmp_path, field):
    lab = Organization()
    store = SQLiteStudyCheckpointStore(tmp_path / "study.sqlite")
    store.append_semantic(lab.study, study_id="reviewed-study", sequence=1)
    assert restore(store, lab, **{field: "unrelated-synthetic-scope"}) is None
    assert restore(store, lab) is not None


def test_semantic_insert_failure_preserves_last_complete_revision(tmp_path):
    lab = Organization()
    lab.records()
    lab.anchors("monetary")
    path = tmp_path / "study.sqlite"
    store = SQLiteStudyCheckpointStore(path)
    store.append_semantic(lab.study, study_id="reviewed-study", sequence=1)
    original_revisions = [revision.revision_id for revision in lab.study.history]
    lab.anchors("completion")
    with sqlite3.connect(path) as connection:
        connection.execute("""
            CREATE TRIGGER fail_semantic_insert AFTER INSERT ON orion_semantic_checkpoints
            WHEN NEW.sequence = 2 BEGIN SELECT RAISE(ABORT, 'synthetic interrupted insert'); END
        """)
    with pytest.raises(sqlite3.IntegrityError, match="interrupted"):
        store.append_semantic(lab.study, study_id="reviewed-study", sequence=2)
    assert [revision.revision_id for revision in restore(store, lab).history] == original_revisions
    with sqlite3.connect(path) as connection:
        assert connection.execute("SELECT COUNT(*) FROM orion_semantic_checkpoints").fetchone()[0] == 1
        connection.execute("DROP TRIGGER fail_semantic_insert")
    store.append_semantic(lab.study, study_id="reviewed-study", sequence=2)
    assert restore(store, lab).history == lab.study.history


@pytest.mark.parametrize("column", ["payload_json", "checksum_sha256", "envelope_json", "envelope_sha256"])
def test_semantic_index_checks_old_envelopes_not_only_latest(tmp_path, column):
    lab = Organization()
    lab.records()
    lab.anchors("monetary")
    path = tmp_path / "study.sqlite"
    store = SQLiteStudyCheckpointStore(path)
    store.append_semantic(lab.study, study_id="reviewed-study", sequence=1)
    lab.anchors("completion")
    store.append_semantic(lab.study, study_id="reviewed-study", sequence=2)
    with sqlite3.connect(path) as connection:
        connection.execute(f"UPDATE orion_semantic_checkpoints SET {column} = {column} || ' ' WHERE sequence = 1")
    with pytest.raises(StudyCheckpointIntegrityError):
        restore(store, lab)
    with pytest.raises(StudyCheckpointIntegrityError):
        store.append_semantic(lab.study, study_id="reviewed-study", sequence=2)


@pytest.mark.parametrize("binding", ["version", "base_version", "evaluator_version"])
def test_semantic_version_rejection_is_not_just_checksum_failure(tmp_path, binding):
    lab = Organization()
    path = tmp_path / "study.sqlite"
    store = SQLiteStudyCheckpointStore(path)
    store.append_semantic(lab.study, study_id="reviewed-study", sequence=1)
    with sqlite3.connect(path) as connection:
        payload, envelope = connection.execute(
            "SELECT payload_json, envelope_json FROM orion_semantic_checkpoints"
        ).fetchone()
        document = json.loads(payload)
        metadata = json.loads(envelope)
        if binding == "base_version":
            base = json.loads(document["base"])
            base["version"] = 99
            document["base"] = json.dumps(base, sort_keys=True, separators=(",", ":"))
        else:
            document[binding] = 99 if binding == "version" else "unreviewed-evaluator"
            if binding == "evaluator_version":
                metadata[binding] = document[binding]
        payload = json.dumps(document, sort_keys=True, separators=(",", ":"))
        checksum = sha256(payload.encode()).hexdigest()
        metadata["checkpoint_sha256"] = checksum
        envelope = json.dumps(metadata, sort_keys=True, separators=(",", ":"))
        connection.execute("""
            UPDATE orion_semantic_checkpoints SET payload_json = ?, checksum_sha256 = ?,
                   envelope_json = ?, envelope_sha256 = ?
        """, (payload, checksum, envelope, sha256(envelope.encode()).hexdigest()))
    with pytest.raises(StudyCheckpointIntegrityError):
        restore(store, lab)


def test_semantic_index_legacy_readonly_database_has_no_semantic_authority(tmp_path):
    lab = Organization()
    path = tmp_path / "legacy.sqlite"
    SQLiteStudyCheckpointStore(path)
    with sqlite3.connect(path) as connection:
        connection.execute("DROP TABLE orion_semantic_checkpoints")
    store = SQLiteStudyCheckpointStore(path, read_only=True)
    assert restore(store, lab) is None
    with pytest.raises(sqlite3.OperationalError, match="read-only"):
        store.append_semantic(lab.study, study_id="reviewed-study", sequence=1)


def test_semantic_chain_detects_missing_prefix(tmp_path):
    lab = Organization()
    lab.records()
    lab.anchors("monetary")
    path = tmp_path / "study.sqlite"
    store = SQLiteStudyCheckpointStore(path)
    store.append_semantic(lab.study, study_id="reviewed-study", sequence=1)
    lab.anchors("completion")
    store.append_semantic(lab.study, study_id="reviewed-study", sequence=2)
    with sqlite3.connect(path) as connection:
        connection.execute("DELETE FROM orion_semantic_checkpoints WHERE sequence = 1")
    with pytest.raises(StudyCheckpointIntegrityError):
        restore(store, lab)
