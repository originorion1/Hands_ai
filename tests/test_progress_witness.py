"""Bounded rollback witness: conditional progress, never truth or authority."""

import shutil
import sqlite3
import threading

import pytest
from test_supervised_broker import Harness

from orion.contracts import utc_now
from orion.pilot.broker_contract import digest
from orion.pilot.custody import AuditCustody
from orion.pilot.evidence_custody import EvidenceCustody
from orion.pilot.journal import JournalDenied
from orion.pilot.progress_witness import (
    ProgressWitness,
    enrollment_path,
    progress_state,
    stream_for,
    witness_contract,
    witness_streams,
)

KEY = b"synthetic-independent-witness-key-000000"


def _manifest(tmp_path):
    configs = [
        {"operation": "metadata", "tenant": "t_01", "company": "c_01", "source": "s_01"},
        {"operation": "read", "tenant": "t_01", "company": "c_01", "source": "s_01"},
    ]
    return {
        "configs": configs,
        "deployment_identity": "a" * 64,
        "artifact_record_sha256": "b" * 64,
        "deployment_profile_sha256": "c" * 64,
        "witness_directory": str(tmp_path / "witness"),
    }


def _states(manifest):
    return [
        progress_state(stream, 1, digest({"stream": stream["identity"], "sequence": 1}))
        for stream in witness_streams(manifest["configs"])
    ]


@pytest.fixture
def witness(tmp_path):
    manifest = _manifest(tmp_path)
    directory = tmp_path / "witness"
    state = tmp_path / "state"
    directory.mkdir(mode=0o700)
    state.mkdir(mode=0o700)
    owner = ProgressWitness.enroll(
        directory, KEY, witness_contract(manifest), _states(manifest), state
    )
    return manifest, directory, state, owner


def test_explicit_enrollment_binds_identity_scopes_and_same_host_limit(witness):
    manifest, directory, state, owner = witness
    status = owner.dispatch("owner", "status", None)
    assert status == {
        "version": 1,
        "witness_identity": witness_contract(manifest)["witness_identity"],
        "deployment_identity": manifest["deployment_identity"],
        "streams": 3,
        "available": True,
        "whole_host_rollback_protection": False,
    }
    assert (directory / "progress-witness.db").stat().st_mode & 0o777 == 0o600
    with pytest.raises(FileExistsError):
        ProgressWitness.enroll(
            directory, KEY, witness_contract(manifest), _states(manifest), state
        )


def test_missing_corrupt_or_substituted_witness_fails_closed(witness):
    manifest, directory, state, _ = witness
    path = directory / "progress-witness.db"
    backup = directory / "witness-backup.db"
    shutil.copyfile(path, backup)
    path.unlink()
    with pytest.raises(OSError):
        ProgressWitness(
            directory, KEY, witness_contract(manifest), enrollment_path(state)
        )
    with pytest.raises(FileExistsError):
        ProgressWitness.enroll(
            directory, KEY, witness_contract(manifest), _states(manifest), state
        )
    shutil.copyfile(backup, path)
    path.chmod(0o600)
    with sqlite3.connect(path) as database:
        database.execute("UPDATE metadata SET body='{}'")
    with pytest.raises((JournalDenied, KeyError)):
        ProgressWitness(
            directory, KEY, witness_contract(manifest), enrollment_path(state)
        )
    shutil.copyfile(backup, path)
    path.chmod(0o600)
    changed = dict(manifest, deployment_profile_sha256="d" * 64)
    with pytest.raises(JournalDenied, match="enrollment receipt mismatch"):
        ProgressWitness(
            directory, KEY, witness_contract(changed), enrollment_path(state)
        )


def test_conditional_advance_duplicate_and_rollback_rejection(witness):
    manifest, _, _, owner = witness
    stream = stream_for(manifest["configs"], "audit", digest(manifest["configs"][0]))
    first = next(state for state in _states(manifest) if state["identity"] == stream["identity"])
    second = progress_state(stream, 2, "d" * 64)
    request = {
        "expected_sequence": first["sequence"],
        "expected_head": first["head"],
        "state": second,
    }
    assert owner.dispatch("audit", "advance", request)["status"] == "advanced"
    assert owner.dispatch("audit", "advance", request)["status"] == "duplicate"
    assert owner.dispatch("audit", "verify", {"state": second})["status"] == "verified"
    with pytest.raises(JournalDenied, match="rollback or witness disagreement"):
        owner.dispatch("audit", "verify", {"state": first})
    conflict = dict(request, state=progress_state(stream, 2, "e" * 64))
    with pytest.raises(JournalDenied, match="conditional"):
        owner.dispatch("audit", "advance", conflict)
    reordered = dict(request, state=progress_state(stream, 3, "f" * 64))
    with pytest.raises(JournalDenied, match="conditional"):
        owner.dispatch("audit", "advance", reordered)


@pytest.mark.parametrize(
    ("role", "mutation"),
    (("evidence", "caller"), ("audit", "scope"), ("audit", "identity"), ("audit", "shape")),
)
def test_wrong_caller_scope_identity_and_malformed_requests_reject(witness, role, mutation):
    manifest, _, _, owner = witness
    stream = stream_for(manifest["configs"], "audit", digest(manifest["configs"][0]))
    state = next(state for state in _states(manifest) if state["identity"] == stream["identity"])
    request = {"state": dict(state)}
    if mutation == "scope":
        request["state"]["scope_binding"] = "d" * 64
    elif mutation == "identity":
        request["state"]["identity"] = "e" * 64
    elif mutation == "shape":
        request["invented"] = True
    with pytest.raises((JournalDenied, ValueError, KeyError)):
        owner.dispatch(role, "verify", request)


def test_concurrent_competing_writers_accept_exactly_one_successor(witness):
    manifest, _, _, owner = witness
    stream = stream_for(manifest["configs"], "audit", digest(manifest["configs"][0]))
    first = next(state for state in _states(manifest) if state["identity"] == stream["identity"])
    barrier = threading.Barrier(2)
    results = []

    def advance(head):
        barrier.wait()
        try:
            result = owner.dispatch(
                "audit",
                "advance",
                {
                    "expected_sequence": 1,
                    "expected_head": first["head"],
                    "state": progress_state(stream, 2, head),
                },
            )
            results.append(result["status"])
        except JournalDenied:
            results.append("denied")

    threads = [threading.Thread(target=advance, args=(head,)) for head in ("d" * 64, "e" * 64)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)
    assert sorted(results) == ["advanced", "denied"]


def _enrolled_for_configs(tmp_path, configs, states):
    manifest = {
        "configs": configs,
        "deployment_identity": "a" * 64,
        "artifact_record_sha256": "b" * 64,
        "deployment_profile_sha256": "c" * 64,
        "witness_directory": str(tmp_path / "witness"),
    }
    directory = tmp_path / "witness"
    state = tmp_path / "state"
    directory.mkdir(mode=0o700)
    state.mkdir(mode=0o700)
    owner = ProgressWitness.enroll(
        directory, KEY, witness_contract(manifest), states, state
    )
    return manifest, owner


def test_old_audit_database_and_tip_reject_against_preserved_witness(tmp_path):
    harness = Harness(tmp_path / "broker")
    directory = tmp_path / "audit"
    directory.mkdir(mode=0o700)
    initial = AuditCustody(directory, KEY, harness.config)
    sequence, head = initial.journal.progress()
    stream = stream_for([harness.config], "audit", digest(harness.config))
    evidence = stream_for([harness.config], "evidence")
    _, witness = _enrolled_for_configs(
        tmp_path,
        [harness.config],
        [
            progress_state(stream, sequence, head),
            progress_state(evidence, 1, "f" * 64),
        ],
    )

    def client(action, value):
        if action == "advance":
            assert (directory / "accepted-head").read_text() == value["state"]["head"]
            with sqlite3.connect(directory / "broker.db") as database:
                assert database.execute("SELECT COUNT(*) FROM events").fetchone()[0] == value[
                    "state"
                ]["sequence"]
        return witness.dispatch("audit", action, value)

    owner = AuditCustody(
        directory, KEY, harness.config, witness=client, witness_stream=stream
    )
    old_database, old_tip = tmp_path / "old-audit.db", tmp_path / "old-audit-tip"
    shutil.copyfile(directory / "broker.db", old_database)
    shutil.copyfile(directory / "accepted-head", old_tip)
    owner.dispatch(
        "supervisor",
        "begin",
        {
            "binding": digest(harness.config),
            "arguments": {"now": utc_now().isoformat(), "request_bytes": 1},
        },
    )
    shutil.copyfile(old_database, directory / "broker.db")
    shutil.copyfile(old_tip, directory / "accepted-head")
    with pytest.raises(JournalDenied, match="rollback or witness disagreement"):
        AuditCustody(
            directory, KEY, harness.config, witness=client, witness_stream=stream
        )


def test_witness_uncertainty_never_refunds_pending_attempt(tmp_path):
    harness = Harness(tmp_path / "broker")
    directory = tmp_path / "audit"
    directory.mkdir(mode=0o700)
    initial = AuditCustody(directory, KEY, harness.config)
    sequence, head = initial.journal.progress()
    stream = stream_for([harness.config], "audit", digest(harness.config))
    evidence = stream_for([harness.config], "evidence")
    _, witness = _enrolled_for_configs(
        tmp_path,
        [harness.config],
        [
            progress_state(stream, sequence, head),
            progress_state(evidence, 1, "f" * 64),
        ],
    )

    def lost_response(action, value):
        result = witness.dispatch("audit", action, value)
        if action == "advance":
            raise JournalDenied("synthetic response loss")
        return result

    owner = AuditCustody(
        directory, KEY, harness.config, witness=lost_response, witness_stream=stream
    )
    with pytest.raises(JournalDenied, match="response loss"):
        owner.dispatch(
            "supervisor",
            "begin",
            {
                "binding": digest(harness.config),
                "arguments": {"now": utc_now().isoformat(), "request_bytes": 1},
            },
        )
    recovered = AuditCustody(
        directory,
        KEY,
        harness.config,
        witness=lambda action, value: witness.dispatch("audit", action, value),
        witness_stream=stream,
    )
    assert recovered.journal.inspect()["pending"] is True
    with pytest.raises(JournalDenied, match="pending"):
        recovered.dispatch(
            "supervisor",
            "begin",
            {
                "binding": digest(harness.config),
                "arguments": {"now": utc_now().isoformat(), "request_bytes": 1},
            },
        )


@pytest.mark.parametrize("boundary", ("local_commit", "local_pin", "witness_acceptance"))
def test_attempt_crash_boundaries_fail_closed_before_success(tmp_path, monkeypatch, boundary):
    harness = Harness(tmp_path / "broker")
    directory = tmp_path / "audit"
    directory.mkdir(mode=0o700)
    initial = AuditCustody(directory, KEY, harness.config)
    sequence, head = initial.journal.progress()
    stream = stream_for([harness.config], "audit", digest(harness.config))
    evidence = stream_for([harness.config], "evidence")
    _, witness = _enrolled_for_configs(
        tmp_path,
        [harness.config],
        [
            progress_state(stream, sequence, head),
            progress_state(evidence, 1, "f" * 64),
        ],
    )

    def client(action, value):
        if boundary == "witness_acceptance" and action == "advance":
            raise JournalDenied("synthetic witness outage")
        return witness.dispatch("audit", action, value)

    owner = AuditCustody(
        directory, KEY, harness.config, witness=client, witness_stream=stream
    )
    if boundary == "local_commit":
        with sqlite3.connect(directory / "broker.db") as database:
            database.execute(
                """
                CREATE TRIGGER fail_attempt BEFORE INSERT ON events
                WHEN NEW.sequence=2
                BEGIN SELECT RAISE(ABORT, 'synthetic local commit failure'); END
                """
            )
    elif boundary == "local_pin":
        monkeypatch.setattr(
            owner,
            "pin",
            lambda: (_ for _ in ()).throw(OSError("synthetic local pin failure")),
        )
    with pytest.raises((JournalDenied, OSError, sqlite3.Error)):
        owner.dispatch(
            "supervisor",
            "begin",
            {
                "binding": digest(harness.config),
                "arguments": {"now": utc_now().isoformat(), "request_bytes": 1},
            },
        )
    if boundary == "local_commit":
        recovered = AuditCustody(
            directory, KEY, harness.config, witness=client, witness_stream=stream
        )
        assert recovered.journal.inspect()["attempts"] == 0
    else:
        with pytest.raises(JournalDenied):
            AuditCustody(
                directory, KEY, harness.config, witness=client, witness_stream=stream
            )


def test_old_evidence_database_tip_and_semantic_pin_reject(tmp_path):
    harness = Harness(tmp_path / "records")
    directory = tmp_path / "evidence"
    directory.mkdir(mode=0o700)
    policy = {"max_entries": 10, "max_bytes": 262144, "ttl_seconds": 60}
    initial = EvidenceCustody(directory, KEY, [harness.config], policy)
    with initial._connect() as database:
        events, _, _, _ = initial._check(database)
    stream = stream_for([harness.config], "evidence")
    audit = stream_for([harness.config], "audit", digest(harness.config))
    _, witness = _enrolled_for_configs(
        tmp_path,
        [harness.config],
        [
            progress_state(audit, 1, "f" * 64),
            progress_state(stream, len(events), initial.head),
        ],
    )

    def client(action, value):
        if action == "advance":
            assert (directory / "accepted-evidence-head").read_text() == value["state"]["head"]
            with sqlite3.connect(directory / "evidence.db") as database:
                assert database.execute("SELECT COUNT(*) FROM events").fetchone()[0] == value[
                    "state"
                ]["sequence"]
        return witness.dispatch("evidence", action, value)

    owner = EvidenceCustody(
        directory,
        KEY,
        [harness.config],
        policy,
        witness=client,
        witness_stream=stream,
    )
    old_database, old_tip = tmp_path / "old-evidence.db", tmp_path / "old-evidence-tip"
    shutil.copyfile(owner.path, old_database)
    shutil.copyfile(owner.anchor, old_tip)
    owner.append_semantic_event(
        {
            "event": "semantic_checkpoint",
            "binding": "d" * 64,
            "sequence_index": 1,
            "checkpoint_sha256": "e" * 64,
            "at": owner._now().isoformat(),
        }
    )
    shutil.copyfile(old_database, owner.path)
    shutil.copyfile(old_tip, owner.anchor)
    with pytest.raises(JournalDenied, match="rollback or witness disagreement"):
        EvidenceCustody(
            directory,
            KEY,
            [harness.config],
            policy,
            witness=client,
            witness_stream=stream,
        )


@pytest.mark.parametrize("response_loss", ("before_acceptance", "after_acceptance"))
def test_semantic_publication_waits_for_witness_and_uncertainty_blocks(
    tmp_path, response_loss
):
    harness = Harness(tmp_path / "records")
    directory = tmp_path / "evidence"
    directory.mkdir(mode=0o700)
    policy = {"max_entries": 10, "max_bytes": 262144, "ttl_seconds": 60}
    initial = EvidenceCustody(directory, KEY, [harness.config], policy)
    with initial._connect() as database:
        events, _, _, _ = initial._check(database)
    stream = stream_for([harness.config], "evidence")
    audit = stream_for([harness.config], "audit", digest(harness.config))
    _, witness = _enrolled_for_configs(
        tmp_path,
        [harness.config],
        [
            progress_state(audit, 1, "f" * 64),
            progress_state(stream, len(events), initial.head),
        ],
    )

    def uncertain_client(action, value):
        if action == "advance" and response_loss == "before_acceptance":
            raise JournalDenied("synthetic pre-acceptance witness outage")
        result = witness.dispatch("evidence", action, value)
        if action == "advance" and response_loss == "after_acceptance":
            raise JournalDenied("synthetic accepted response loss")
        return result

    owner = EvidenceCustody(
        directory,
        KEY,
        [harness.config],
        policy,
        witness=uncertain_client,
        witness_stream=stream,
    )
    with pytest.raises(JournalDenied, match="synthetic"):
        owner.append_semantic_event(
            {
                "event": "semantic_checkpoint",
                "binding": "d" * 64,
                "sequence_index": 1,
                "checkpoint_sha256": "e" * 64,
                "at": owner._now().isoformat(),
            }
        )

    def accepted_client(action, value):
        return witness.dispatch("evidence", action, value)

    if response_loss == "before_acceptance":
        with pytest.raises(JournalDenied, match="rollback or witness disagreement"):
            EvidenceCustody(
                directory,
                KEY,
                [harness.config],
                policy,
                witness=accepted_client,
                witness_stream=stream,
            )
    else:
        recovered = EvidenceCustody(
            directory,
            KEY,
            [harness.config],
            policy,
            witness=accepted_client,
            witness_stream=stream,
        )
        with recovered._connect() as database:
            recovered_events, _, _, _ = recovered._check(database)
        assert recovered_events[-1]["event"] == "semantic_checkpoint"
