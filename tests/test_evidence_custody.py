"""Actual SQLite admission custody: no grant issuer or replacement attempt journal."""

import copy
import json
import shutil
import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest
from test_broker_metadata import MetadataHarness
from test_supervised_broker import Harness

from orion.contracts import Evidence, Observation, utc_now
from orion.discovery.pilot_metadata import launch_pilot_metadata
from orion.discovery.pilot_read import _admit
from orion.history.evidence import _observation_to_data
from orion.pilot.broker_contract import digest, observations_from
from orion.pilot.broker_metadata import proposal_from
from orion.pilot.evidence_custody import EvidenceCustody
from orion.pilot.journal import JournalDenied
from orion.understanding.role_checkpoint import _json

KEY = b"independent-admitted-evidence-test-key-0000"


@pytest.fixture
def archive(tmp_path):
    r, m = Harness(tmp_path / "read"), MetadataHarness(tmp_path / "metadata")
    directory = tmp_path / "archive"
    directory.mkdir(mode=0o700)
    now = [utc_now()]
    policy = {"max_entries": 10, "max_bytes": 262144, "ttl_seconds": 60}
    owner = EvidenceCustody(directory, KEY, [m.config, r.config], policy, clock=lambda: now[0])
    return r, m, directory, now, policy, owner


def values(h, now, marker="retained-marker"):
    record = dict(h.rows[0], f_d=marker)
    raw = Observation(
        Evidence(
            h.grant.evidence_kind,
            h.grant.provenance_source,
            {"resource": h.request.resource, "record": record},
            observed_at=now,
            tenant_id=h.request.tenant_id,
        )
    )
    return [_observation_to_data(o) for o in _admit((raw,), h.request, h.grant, now)]


def call(owner, h, action, role=None, **args):
    role = role or ("supervisor" if action in ("append", "availability") else "owner")
    return owner.dispatch(role, action, {"binding": digest(h.config), "arguments": args})


def append(owner, h, observations, request="1" * 64):
    return call(
        owner,
        h,
        "append",
        observations=observations,
        journal_head="a" * 64,
        request_reference=request,
    )


def reopen(archive):
    r, m, directory, now, policy, _ = archive
    return EvidenceCustody(directory, KEY, [m.config, r.config], policy, clock=lambda: now[0])


def test_canonical_evidence_checkpoint_restart_preserves_ids_without_authority(archive):
    r, _, _, now, _, owner = archive
    observations = values(r, now[0])
    accepted = append(owner, r, observations)
    result = call(reopen(archive), r, "load")
    assert result["head"] == accepted["head"]
    assert result["observations"] == observations
    assert result["authority_restored"] is False
    assert result["checkpoints"][0]["journal_head"] == "a" * 64
    assert result["checkpoints"][0]["references"][0]["revision"] == 1
    assert (
        observations_from(result["observations"])[0].evidence.payload["provenance"][
            "authorization_id"
        ]
        == r.grant.authorization_id
    )


def test_revision_history_is_immutable_and_company_tenant_source_scoped(archive):
    r, m, _, now, _, owner = archive
    first = values(r, now[0], "first-revision")
    append(owner, r, first)
    now[0] += timedelta(seconds=1)
    second = values(r, now[0], "second-revision")
    append(owner, r, second, "2" * 64)
    result = call(owner, r, "load")
    assert result["observations"] == first + second
    assert [c["references"][0]["revision"] for c in result["checkpoints"]] == [1, 2]
    assert all(
        c["company"] == r.request.company and c["source_id"] == r.request.source_id
        for c in result["checkpoints"]
    )
    assert call(owner, m, "load")["observations"] == []
    with pytest.raises(JournalDenied):
        owner.dispatch("owner", "load", {"binding": "0" * 64, "arguments": {}})


def test_metadata_discovery_is_indexed_but_does_not_grant_record_reads(archive):
    _, m, _, now, _, owner = archive

    class Metadata:
        source_id = m.request.source_id

        def catalog(self, permit, maximum):
            permit.claim_io(self.source_id)
            return ("r_01",), True

        def schema(self, permit, resource):
            permit.claim_io(self.source_id)
            return proposal_from(
                resource,
                [
                    {
                        "resource": resource,
                        "name": "f_c",
                        "kind": "date",
                        "source_type": "local_schema_v1",
                    }
                ],
            )

    discovery = launch_pilot_metadata(
        m.request,
        authorization_id=m.grant.authorization_id,
        lookup=lambda _: m.grant,
        adapter=Metadata(),
        clock=lambda: now[0],
    )
    # Canonical immutable values are serialized by the existing authoritative wire serializer.
    observations = [
        _observation_to_data(
            replace(o, evidence=replace(o.evidence, payload=json.loads(_json(o.evidence.payload))))
        )
        for o in discovery.observations
    ]
    append(owner, m, observations)
    result = call(reopen(archive), m, "load")
    assert result["observations"] == observations
    assert result["observations"][0]["evidence"]["payload"]["record_reads_allowed"] is False
    assert result["authority_restored"] is False


@pytest.mark.parametrize(
    "attack",
    [
        "tenant",
        "company",
        "source",
        "authorization",
        "resource",
        "field",
        "observation",
        "evidence",
        "scope",
    ],
)
def test_forged_admitted_payload_denied_at_custody_boundary(archive, attack):
    r, _, _, now, _, owner = archive
    payload = values(r, now[0])
    evidence = payload[0]["evidence"]
    if attack == "tenant":
        evidence["tenant_id"] = "other"
    elif attack == "company":
        evidence["payload"]["record"][r.grant.company_field] = "other"
    elif attack == "source":
        evidence["payload"]["provenance"]["source_id"] = "https://other.test"
    elif attack == "authorization":
        evidence["payload"]["provenance"]["authorization_id"] = "other"
    elif attack == "resource":
        evidence["payload"]["resource"] = "other"
    elif attack == "field":
        evidence["payload"]["record"]["unapproved"] = "value"
    elif attack == "observation":
        payload[0]["observation_id"] = "00000000-0000-0000-0000-000000000000"
    elif attack == "evidence":
        evidence["evidence_id"] = "00000000-0000-0000-0000-000000000000"
    else:
        evidence["payload"]["provenance"]["scope_sha256"] = "0" * 64
    with pytest.raises(ValueError):
        append(owner, r, payload)
    assert call(owner, r, "inspect")["index_entries"] == 0


@pytest.mark.parametrize("role", ["broker", "reasoner", "source", "gateway", "owner", "anonymous"])
def test_unauthorized_append_is_denied_before_storage_mutation(archive, role):
    r, _, _, now, _, owner = archive
    head = owner.head
    with pytest.raises(JournalDenied):
        call(
            owner,
            r,
            "append",
            role=role,
            observations=values(r, now[0]),
            journal_head="a" * 64,
            request_reference="1" * 64,
        )
    assert owner.head == head


@pytest.mark.parametrize(
    "attack",
    [
        "payload",
        "delete_payload",
        "event",
        "delete_event",
        "rollback",
        "anchor",
        "missing_anchor",
        "missing_database",
    ],
)
def test_tampering_deletion_and_rollback_deny_restart_without_repair(archive, attack):
    r, _, directory, now, _, owner = archive
    baseline = directory / "snapshot.db"
    shutil.copyfile(owner.path, baseline)
    append(owner, r, values(r, now[0]))
    if attack == "payload":
        with sqlite3.connect(owner.path) as db:
            db.execute("UPDATE payloads SET body='[]'")
    elif attack == "delete_payload":
        with sqlite3.connect(owner.path) as db:
            db.execute("DELETE FROM payloads")
    elif attack == "event":
        with sqlite3.connect(owner.path) as db:
            db.execute("UPDATE events SET body='{}' WHERE sequence=2")
    elif attack == "delete_event":
        with sqlite3.connect(owner.path) as db:
            db.execute("DELETE FROM events WHERE sequence=2")
    elif attack == "rollback":
        shutil.copyfile(baseline, owner.path)
    elif attack == "anchor":
        owner.anchor.write_text("0" * 64)
    elif attack == "missing_anchor":
        owner.anchor.unlink()
    else:
        owner.path.unlink()
    with pytest.raises((ValueError, OSError)):
        reopen(archive)


def test_retention_erases_content_but_keeps_authenticated_tombstone_and_checkpoint(archive):
    r, _, _, now, _, owner = archive
    observations = values(r, now[0])
    accepted = append(owner, r, observations)
    now[0] += timedelta(seconds=60)
    result = call(owner, r, "load")
    assert result["observations"] == [] and result["interpretation"] == "UNKNOWN"
    checkpoint = result["checkpoints"][0]
    assert checkpoint["retained"] is False
    assert checkpoint["payload_sha256"] == accepted["payload_sha256"]
    assert checkpoint["references"][0]["observation_id"] == observations[0]["observation_id"]
    with sqlite3.connect(owner.path) as db:
        assert db.execute("SELECT COUNT(*) FROM payloads").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM events").fetchone()[0] == 3
    assert b"retained-marker" not in owner.path.read_bytes()
    assert call(reopen(archive), r, "load")["checkpoints"] == result["checkpoints"]


def test_expired_payload_resurrection_is_detected(archive):
    r, _, _, now, _, owner = archive
    observations = values(r, now[0])
    accepted = append(owner, r, observations)
    now[0] += timedelta(seconds=61)
    call(owner, r, "prune")
    with sqlite3.connect(owner.path) as db:
        db.execute(
            "INSERT INTO payloads VALUES (?,?)", (accepted["checkpoint"], json.dumps(observations))
        )
    with pytest.raises(JournalDenied):
        reopen(archive)


def test_request_and_observation_replay_denied_even_after_expiry(archive):
    r, _, _, now, _, owner = archive
    observations = values(r, now[0])
    append(owner, r, observations)
    with pytest.raises(JournalDenied):
        append(owner, r, observations)
    with pytest.raises(JournalDenied):
        append(owner, r, observations, "2" * 64)
    now[0] += timedelta(seconds=61)
    call(owner, r, "prune")
    with pytest.raises(JournalDenied):
        append(owner, r, observations, "3" * 64)


@pytest.mark.parametrize("restart", [False, True])
@pytest.mark.parametrize("expired", [False, True])
def test_preflight_denies_accepted_request_before_io_across_restart_and_retention(
    archive, restart, expired
):
    r, m, _, now, _, owner = archive
    append(owner, r, values(r, now[0]), "1" * 64)
    if expired:
        now[0] += timedelta(seconds=61)
    if restart:
        owner = reopen(archive)
    with pytest.raises(JournalDenied, match="request replay"):
        call(owner, r, "availability", request_reference="1" * 64)
    assert call(owner, r, "availability", request_reference="2" * 64)["available"]
    assert call(owner, r, "availability")["available"]  # Backward-compatible health check.
    assert call(owner, m, "availability", request_reference="1" * 64)["available"]
    state = call(owner, r, "load")
    assert state["index_entries"] == 1
    assert state["checkpoints"][0]["retained"] is (not expired)
    if expired:
        assert state["observations"] == []


@pytest.mark.parametrize("role", ["broker", "reasoner", "gateway", "owner", "anonymous"])
def test_preflight_does_not_trust_an_unauthorized_callers_role(archive, role):
    r, _, _, _, _, owner = archive
    head = owner.head
    with pytest.raises(JournalDenied):
        call(owner, r, "availability", role=role, request_reference="1" * 64)
    assert owner.head == head


@pytest.mark.parametrize(
    "args",
    [
        {"request_reference": "not-a-digest"},
        {"request_reference": "1" * 64, "ignore_replay": True},
        {"request_id": "q_01"},
        [],
    ],
)
def test_preflight_requires_exact_digest_reference_shape(archive, args):
    r, _, _, _, _, owner = archive
    head = owner.head
    with pytest.raises(ValueError):
        owner.dispatch(
            "supervisor",
            "availability",
            {
                "binding": digest(r.config),
                "arguments": args,
            },
        )
    assert owner.head == head


def test_expiry_remains_durable_when_following_request_is_denied(archive):
    r, _, _, now, _, owner = archive
    observations = values(r, now[0])
    append(owner, r, observations)
    now[0] += timedelta(seconds=61)
    # No explicit prune first: retention must commit before replay denial.
    with pytest.raises(JournalDenied):
        append(owner, r, observations, "2" * 64)
    assert call(reopen(archive), r, "load")["observations"] == []


def test_index_capacity_denies_further_acquisition_without_reset_after_retention(tmp_path):
    h = Harness(tmp_path / "read")
    directory = tmp_path / "archive"
    directory.mkdir(mode=0o700)
    now = [utc_now()]
    policy = {"max_entries": 1, "max_bytes": 262144, "ttl_seconds": 1}
    owner = EvidenceCustody(directory, KEY, [h.config], policy, clock=lambda: now[0])
    assert call(owner, h, "availability")["available"]
    append(owner, h, values(h, now[0]))
    with pytest.raises(JournalDenied):
        call(owner, h, "availability")
    now[0] += timedelta(seconds=2)
    call(owner, h, "prune")
    restarted = EvidenceCustody(directory, KEY, [h.config], policy, clock=lambda: now[0])
    with pytest.raises(JournalDenied):
        call(restarted, h, "availability")
    assert call(restarted, h, "load")["index_entries"] == 1


def test_clock_key_or_policy_changes_do_not_silently_restore_state(archive):
    r, m, directory, now, policy, owner = archive
    append(owner, r, values(r, now[0]))
    now[0] -= timedelta(seconds=1)
    with pytest.raises(JournalDenied):
        call(owner, r, "load")
    with pytest.raises(JournalDenied):
        EvidenceCustody(
            directory, b"other-independent-storage-key-000000", [m.config, r.config], policy
        )
    changed = copy.deepcopy(policy)
    changed["ttl_seconds"] += 1
    with pytest.raises(JournalDenied):
        EvidenceCustody(directory, KEY, [m.config, r.config], changed)
