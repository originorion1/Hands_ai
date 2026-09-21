"""Versioned post-discovery authority transition through existing custody owners."""

import copy
import json
import shutil
import threading
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
from test_credential_gateway import candidate_configs

from orion.contracts import utc_now
from orion.discovery.pilot_metadata import launch_pilot_metadata
from orion.history.evidence import _observation_to_data
from orion.pilot.broker_contract import (
    GRANT_TRANSITION_VERSION,
    authenticate,
    digest,
    transition_binding,
    transition_policy_from,
    transition_record_config,
    transition_request_from,
)
from orion.pilot.broker_metadata import erpnext_proposal_from
from orion.pilot.custody import AuditCustody
from orion.pilot.deployment import Deployment
from orion.pilot.evidence_custody import EvidenceCustody
from orion.pilot.journal import JournalDenied
from orion.pilot.progress_witness import (
    ProgressWitness,
    progress_state,
    stream_for,
    transition_initial_state,
    witness_contract,
)
from orion.pilot.services import PersistedRuntimeCustody
from orion.understanding.role_checkpoint import _json

KEY = b"synthetic-transition-custody-key-00000000"
WITNESS_KEY = b"synthetic-transition-witness-key-000000"


def transition_inputs():
    metadata, records = candidate_configs()
    records = copy.deepcopy(records)
    fields = sorted(records["grant"]["window"]["fields"])
    records["grant"]["window"]["fields"] = fields
    records["field_classifications"] = {field: "public" for field in fields}
    policy = {
        "version": GRANT_TRANSITION_VERSION,
        "tenant_id": records["grant"]["window"]["tenant_id"],
        "company": records["grant"]["window"]["company"],
        "source_id": records["grant"]["source_id"],
        "caller": records["caller"],
        "secret_reference": records["secret_reference"],
        "auth_reference": records["auth_reference"],
        "limits": records["limits"],
        "max_fields": 16,
        "max_records": 5,
        "max_window_days": 14,
        "provenance_source": records["grant"]["provenance_source"],
    }
    return metadata, records, policy


def admitted_metadata(metadata, records, now):
    resource = records["grant"]["window"]["resource"]
    fields = tuple(records["grant"]["window"]["fields"])
    date_field = records["grant"]["window"]["date_field"]

    class Adapter:
        source_id = metadata["grant"]["request"]["source_id"]

        def catalog(self, permit, maximum):
            permit.claim_io(self.source_id)
            return (resource,), True

        def schema(self, permit, target):
            permit.claim_io(self.source_id)
            assert target == resource
            return erpnext_proposal_from(
                resource, list(fields), [date_field], []
            )

    from orion.pilot.broker_contract import metadata_grant_from

    grant = metadata_grant_from(metadata["grant"])
    discovery = launch_pilot_metadata(
        grant.request,
        authorization_id=grant.authorization_id,
        lookup=lambda _: grant,
        adapter=Adapter(),
        clock=lambda: now,
    )
    return [
        _observation_to_data(
            replace(
                observation,
                evidence=replace(
                    observation.evidence,
                    payload=json.loads(_json(observation.evidence.payload)),
                ),
            )
        )
        for observation in discovery.observations
    ]


def evidence_owner(tmp_path):
    metadata, records, policy = transition_inputs()
    directory = tmp_path / "evidence"
    directory.mkdir(mode=0o700)
    now = utc_now()
    owner = EvidenceCustody(
        directory,
        KEY,
        [metadata],
        {"max_entries": 10, "max_bytes": 262144, "ttl_seconds": 3600},
        clock=lambda: now,
        transition=policy,
        deployment_identity="d" * 64,
    )
    observations = admitted_metadata(metadata, records, now)
    owner.dispatch(
        "supervisor",
        "append",
        {
            "binding": digest(metadata),
            "arguments": {
                "observations": observations,
                "journal_head": "a" * 64,
                "request_reference": "b" * 64,
                "request_sha256": "c" * 64,
            },
        },
    )
    return metadata, records, policy, directory, owner


def test_evidence_transition_is_append_only_and_restarts_with_exact_config(tmp_path):
    metadata, records, policy, directory, owner = evidence_owner(tmp_path)
    challenge = owner.dispatch(
        "owner",
        "transition_challenge",
        {"binding": digest(metadata), "arguments": {}},
    )
    request = {
        **challenge,
        "config": records,
        "expected_witness_sha256": "e" * 64,
        "mac": "f" * 64,
    }
    result = owner.dispatch(
        "owner",
        "provision",
        {"binding": digest(metadata), "arguments": {"transition": request}},
    )
    assert result["status"] == "evidence_committed"
    assert result["binding"] == digest(records)

    restarted = EvidenceCustody(
        directory,
        KEY,
        [metadata],
        {"max_entries": 10, "max_bytes": 262144, "ttl_seconds": 3600},
        clock=utc_now,
        transition=policy,
        deployment_identity="d" * 64,
    )
    restored = restarted.dispatch(
        "owner", "transition", {"binding": digest(metadata), "arguments": {}}
    )
    assert restored["status"] == "provisioned"
    assert restored["config"] == records
    with pytest.raises(JournalDenied, match="transition unavailable"):
        restarted.dispatch(
            "owner",
            "provision",
            {"binding": digest(metadata), "arguments": {"transition": request}},
        )


def test_record_audit_uses_pre_enrolled_zero_authority_stream_once(tmp_path):
    metadata, records, policy = transition_inputs()
    root, witness_directory = tmp_path / "state", tmp_path / "witness"
    root.mkdir(mode=0o700)
    witness_directory.mkdir(mode=0o700)
    metadata_directory = root / "metadata"
    metadata_directory.mkdir(mode=0o700)
    metadata_audit = AuditCustody(metadata_directory, KEY, metadata)
    metadata_sequence, metadata_head = metadata_audit.journal.progress()
    metadata_stream = stream_for([metadata], "audit", digest(metadata), transition=policy)
    evidence_stream = stream_for([metadata], "evidence", transition=policy)
    initial_transition = transition_initial_state([metadata], policy)
    manifest = {
        "configs": [metadata],
        "grant_transition": policy,
        "deployment_identity": "d" * 64,
        "artifact_record_sha256": "a" * 64,
        "deployment_profile_sha256": "b" * 64,
        "witness_directory": str(witness_directory),
    }
    witness = ProgressWitness.enroll(
        witness_directory,
        WITNESS_KEY,
        witness_contract(manifest),
        [
            progress_state(metadata_stream, metadata_sequence, metadata_head),
            initial_transition,
            progress_state(evidence_stream, 1, "c" * 64),
        ],
        root,
    )
    witness_before = tmp_path / "witness-before-transition.db"
    shutil.copyfile(witness.path, witness_before)

    def witness_client(action, value):
        return witness.dispatch("audit", action, value)

    record_directory = root / "read"
    record_directory.mkdir(mode=0o700)
    provision = {
        "transition_reference": "1" * 64,
        "metadata_checkpoint": 2,
        "metadata_evidence_head": "2" * 64,
        "predecessor_generation": 0,
    }
    stream = stream_for(
        [metadata], "audit", transition_binding(policy), transition=policy
    )
    owner = AuditCustody(
        record_directory,
        KEY,
        records,
        witness=witness_client,
        witness_stream=stream,
        provision=provision,
        transition_initial=initial_transition,
    )
    assert owner.journal.progress()[0] == 2
    assert owner.journal.inspect()["attempts"] == 0
    snapshot = witness.dispatch("owner", "snapshot", None)
    transitioned = next(
        state for state in snapshot["states"] if state["identity"] == stream["identity"]
    )
    assert transitioned["head"] == owner.journal.head
    assert transitioned["sequence"] == 2

    shutil.copyfile(witness_before, witness.path)
    with pytest.raises(JournalDenied, match="witness disagreement"):
        AuditCustody(
            record_directory,
            KEY,
            records,
            witness=witness_client,
            witness_stream=stream,
            provision=provision,
            transition_initial=initial_transition,
        )


def test_supervisor_transition_commit_order_fails_closed_after_every_boundary(monkeypatch):
    metadata, records, policy = transition_inputs()
    request = {"config": records, "expected_witness_sha256": "a" * 64}
    expected = [
        "witness",
        "authorization_prepare",
        "evidence",
        "evidence_state",
        "audit",
        "authorization_commit",
        "gateway",
        "acquisition",
    ]

    def exercise(fail_after=None):
        deployment = object.__new__(Deployment)
        deployment.transition = threading.RLock()
        deployment.grant_transition = policy
        deployment.transition_state = {"generation": 0}
        deployment.enrolled_configs = [metadata]
        deployment.configs = [metadata]
        deployment.endpoints = {
            name: name
            for name in ("witness", "evidence", "audit", "gateway", "acquisition")
        }
        deployment.caps = {"witness-owner": b"w", "owner": b"o"}
        calls = []

        def reached(name, result):
            calls.append(name)
            if fail_after == name:
                raise JournalDenied("injected transition boundary failure")
            return result

        def owner(action, value=None):
            del value
            if action == "transition_prepare":
                return reached(
                    "authorization_prepare",
                    {
                        "status": "validated_no_authority",
                        "binding": digest(records),
                        "transition_reference": "b" * 64,
                    },
                )
            if action == "transition_commit":
                return reached(
                    "authorization_commit",
                    {
                        "status": "provisioned_unarmed",
                        "generation": 1,
                        "binding": digest(records),
                    },
                )
            raise AssertionError(action)

        def restored():
            return reached(
                "evidence_state",
                {
                    "generation": 1,
                    "config": records,
                    "audit_provision": {
                        "transition_reference": "b" * 64,
                        "metadata_checkpoint": 2,
                        "metadata_evidence_head": "c" * 64,
                        "predecessor_generation": 0,
                    },
                },
            )

        def fake_rpc(endpoint, role, key, action, value):
            del role, key, action, value
            results = {
                "witness": {"states": [], "sha256": "a" * 64},
                "evidence": {
                    "status": "evidence_committed",
                    "binding": digest(records),
                    "transition_reference": "b" * 64,
                    "head": "d" * 64,
                },
                "audit": {"status": "audit_committed", "head": "e" * 64},
                "gateway": {
                    "status": "gateway_committed",
                    "binding": digest(records),
                },
                "acquisition": {
                    "status": "acquisition_committed",
                    "binding": digest(records),
                },
            }
            if endpoint == "audit":
                results[endpoint]["binding"] = digest(records)
            return reached(endpoint, results[endpoint])

        health_calls = 0

        def health():
            nonlocal health_calls
            health_calls += 1
            if health_calls == 1:
                return {"status": "healthy"}
            return {
                "status": "healthy",
                "record_authority_provisioned": True,
                "record_authority_armed": False,
            }

        deployment.owner = owner
        deployment._restore_transition_config = restored
        deployment.health = health
        deployment._cutoff = lambda: calls.append("cutoff")
        monkeypatch.setattr("orion.pilot.deployment.rpc", fake_rpc)
        if fail_after is None:
            result = deployment.provision(request)
            assert result["status"] == "provisioned_unarmed"
            assert calls == expected
            return
        with pytest.raises(JournalDenied, match="injected transition boundary failure"):
            deployment.provision(request)
        assert calls[-1] == "cutoff"
        assert calls[:-1] == expected[: expected.index(fail_after) + 1]

    exercise()
    for boundary in expected:
        exercise(boundary)


def test_restart_does_not_repair_evidence_only_partial_transition(tmp_path):
    metadata, records, policy = transition_inputs()
    directory = tmp_path / "read"
    directory.mkdir(mode=0o700)
    stream = stream_for(
        [metadata], "audit", transition_binding(policy), transition=policy
    )
    provision = {
        "transition_reference": "1" * 64,
        "metadata_checkpoint": 2,
        "metadata_evidence_head": "2" * 64,
        "predecessor_generation": 0,
    }
    with pytest.raises(JournalDenied, match="complete persisted grant transition"):
        AuditCustody(
            directory,
            KEY,
            records,
            witness=lambda *unused: None,
            witness_stream=stream,
            provision=provision,
            transition_initial=transition_initial_state([metadata], policy),
            require_existing=True,
        )
    assert not (directory / "broker.db").exists()


def test_unsupported_transition_budget_denies_before_policy_admission():
    _, _, policy = transition_inputs()
    policy["limits"]["max_requests"] = 49
    with pytest.raises(JournalDenied, match="supervised request budget exceeds audit capacity"):
        transition_policy_from(policy)


def test_unsupported_provisioned_budget_has_no_state_or_witness_effect(tmp_path):
    metadata, records, policy = transition_inputs()
    records = copy.deepcopy(records)
    stream = stream_for(
        [metadata], "audit", transition_binding(policy), transition=policy
    )
    records["limits"]["max_requests"] = 49
    directory = tmp_path / "read"
    directory.mkdir(mode=0o700)
    witness_calls = []
    provision = {
        "transition_reference": "1" * 64,
        "metadata_checkpoint": 2,
        "metadata_evidence_head": "2" * 64,
        "predecessor_generation": 0,
    }
    with pytest.raises(JournalDenied, match="supervised request budget exceeds audit capacity"):
        AuditCustody(
            directory,
            KEY,
            records,
            witness=lambda *args: witness_calls.append(args),
            witness_stream=stream,
            provision=provision,
            transition_initial=transition_initial_state([metadata], policy),
        )
    assert list(directory.iterdir()) == []
    assert witness_calls == []


@pytest.mark.parametrize(
    "mutation", ("field_count", "budget", "expiry", "caller", "source", "technical")
)
def test_transition_envelope_denies_broadening(mutation):
    _, records, policy = transition_inputs()
    changed = copy.deepcopy(records)
    if mutation == "field_count":
        for index in range(20):
            field = f"extra_{index}"
            changed["grant"]["window"]["fields"].append(field)
            changed["field_classifications"][field] = "public"
    elif mutation == "budget":
        changed["limits"]["max_requests"] += 1
    elif mutation == "expiry":
        changed["grant"]["window"]["expires_at"] = (
            utc_now() + timedelta(days=2)
        ).isoformat()
    elif mutation == "caller":
        changed["caller"] = "different-controller"
    elif mutation == "source":
        changed["grant"]["source_id"] = "https://different.test"
    else:
        changed["grant"]["identity_field"] = "unsupported_identity"
    with pytest.raises(ValueError):
        transition_record_config(changed, policy)


def transition_request(metadata, records, policy):
    request = {
        "version": policy["version"],
        "deployment_identity": "d" * 64,
        "generation": 1,
        "predecessor_generation": 0,
        "config": records,
        "metadata": {
            "binding": digest(metadata),
            "checkpoint": 2,
            "evidence_head": "1" * 64,
            "payload_sha256": "2" * 64,
            "journal_head": "3" * 64,
            "request_reference": "4" * 64,
            "observation_id": "observation",
            "evidence_id": "evidence",
        },
        "expected_witness_sha256": "5" * 64,
    }
    request["mac"] = authenticate(KEY, "grant_transition", request)
    return request


def test_transition_request_denies_wrong_predecessor_and_expired_grant():
    metadata, records, policy = transition_inputs()
    request = transition_request(metadata, records, policy)
    changed = dict(request, predecessor_generation=1)
    with pytest.raises(ValueError, match="predecessor"):
        transition_request_from(changed, policy, "d" * 64)

    expired = copy.deepcopy(records)
    expired["grant"]["window"]["expires_at"] = (
        utc_now() - timedelta(seconds=1)
    ).isoformat()
    request = transition_request(metadata, expired, policy)
    custody = object.__new__(PersistedRuntimeCustody)
    custody.transition_policy = policy
    custody.deployment_identity = "d" * 64
    custody.owner_factory = lambda config: config
    custody.runtime = SimpleNamespace(
        metadata=SimpleNamespace(key=KEY),
        validate_transition_config=lambda config: SimpleNamespace(
            window=SimpleNamespace(
                expires_at=transition_record_config(config, policy).window.expires_at
            )
        ),
    )
    with pytest.raises(JournalDenied, match="expired"):
        custody._validate_transition(request)


def test_retained_metadata_denies_stale_reference_and_unsupported_field(tmp_path):
    metadata, records, _, _, owner = evidence_owner(tmp_path)
    challenge = owner.dispatch(
        "owner",
        "transition_challenge",
        {"binding": digest(metadata), "arguments": {}},
    )
    request = {**challenge, "config": records, "expected_witness_sha256": "5" * 64,
               "mac": "6" * 64}
    stale = copy.deepcopy(request)
    stale["metadata"]["evidence_head"] = "0" * 64
    with pytest.raises(JournalDenied, match="stale metadata"):
        owner.dispatch(
            "owner",
            "provision",
            {"binding": digest(metadata), "arguments": {"transition": stale}},
        )

    unsupported = copy.deepcopy(request)
    unsupported["config"]["grant"]["window"]["fields"].append("not_discovered")
    unsupported["config"]["field_classifications"]["not_discovered"] = "public"
    with pytest.raises(JournalDenied, match="not supported"):
        owner.dispatch(
            "owner",
            "provision",
            {"binding": digest(metadata), "arguments": {"transition": unsupported}},
        )


def test_pending_acquisition_denies_before_transition_validation():
    custody = object.__new__(PersistedRuntimeCustody)
    custody.lock = threading.RLock()
    custody.active = object()
    with pytest.raises(JournalDenied, match="in flight"):
        custody.dispatch("owner", "transition_prepare", {})
