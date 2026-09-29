"""Synthetic instrument reads reuse exact grants, admission and durable budgets."""

import copy
import hashlib
from dataclasses import replace
from datetime import timedelta

import pytest
from test_broker_metadata import MetadataHarness
from test_supervised_broker import Harness
from test_supervised_runtime import acquire, control

from orion.contracts import EvidenceKind
from orion.pilot.broker import Broker
from orion.pilot.broker_contract import (
    INSTRUMENT_OPERATIONS,
    authenticate,
    digest,
    is_record_operation,
    observations_from,
)
from orion.pilot.custody import AuditCustody, AuthorizationCustody, RemoteJournal, RuntimeCustody
from orion.pilot.gateway import CredentialGateway
from orion.pilot.runtime import SupervisedReadOnlyRuntime


@pytest.fixture
def instrument_runtime(tmp_path, monkeypatch):
    records = Harness(tmp_path / "read")
    metadata = MetadataHarness(tmp_path / "metadata")
    instrument = Harness(
        tmp_path / "instrument",
        grant=replace(records.grant, authorization_id="a_independent", source_id="https://i.test"),
    )
    instrument.config["operation"] = "instrument_0"
    metadata.key = instrument.key = records.key
    metadata.secret = records.secret
    metadata.write_source()
    monkeypatch.setenv("BROKER_AUTH_KEY", records.key.decode())
    monkeypatch.setenv("BROKER_SOURCE_SECRET", instrument.secret)
    owners, audits = [], []
    for harness in (metadata, records, instrument):
        audit = AuditCustody(harness.state, b"synthetic-independent-audit-key-00", harness.config)
        remote = RemoteJournal(
            lambda action, value, a=audit: a.dispatch("supervisor", action, value),
            digest(harness.config),
        )
        # Each protected owner resolves its configured synthetic source credential.
        monkeypatch.setenv("BROKER_SOURCE_SECRET", harness.secret)
        owners.append(AuthorizationCustody(harness.config, remote))
        audits.append(audit)
    runtime = SupervisedReadOnlyRuntime(*owners)
    return metadata, records, instrument, owners, audits, runtime, RuntimeCustody(runtime)


def message(harness, identity="independent_q"):
    return harness.message(identity) | {"operation": harness.config["operation"]}


def arm_instrument(deployment):
    metadata, _, _, owners, _, runtime, service = deployment
    runtime.control("metadata", control(owners[0], "arm"))
    assert acquire(metadata, service)["status"] == "admitted"
    runtime.control("instrument_0", control(owners[2], "arm"))


def instrument_acquire(harness, service):
    reply = service.dispatch("broker", "begin", message(harness))
    if reply.get("status") == "offered":
        value = {"receipt": reply["receipt"], "binding": digest(harness.config)}
        service.dispatch("source", "redeem", value)
        reply = service.dispatch(
            "broker", "complete", {"receipt": reply["receipt"], "body": harness.source.read_bytes().hex()}
        )
    return reply


def test_fixed_instrument_operations_do_not_authorize_arbitrary_routes():
    assert INSTRUMENT_OPERATIONS == tuple("instrument_" + str(n) for n in range(8))
    assert all(is_record_operation(o) for o in ("read", *INSTRUMENT_OPERATIONS))
    assert not any(is_record_operation(o) for o in ("metadata", "instrument_8", "write", "/other"))


def test_governed_metadata_does_not_arm_instrument(instrument_runtime):
    metadata, _, instrument, owners, audits, runtime, service = instrument_runtime
    with pytest.raises(ValueError):
        runtime.control("instrument_0", control(owners[2], "arm"))
    with pytest.raises(ValueError):
        service.dispatch("broker", "begin", message(instrument))
    runtime.control("metadata", control(owners[0], "arm"))
    assert acquire(metadata, service)["status"] == "admitted"
    assert not owners[2].armed
    assert runtime.health()["instrument_authority_armed"] == {"instrument_0": False}
    assert service.dispatch("broker", "begin", message(instrument))["status"] == "denied"
    assert audits[2].journal.inspect()["attempts"] == 0


def test_separate_instrument_grant_admits_canonical_originals(instrument_runtime):
    _, records, instrument, _, audits, runtime, service = instrument_runtime
    arm_instrument(instrument_runtime)
    assert runtime.health()["instrument_authority_armed"] == {"instrument_0": True}
    result = instrument_acquire(instrument, service)
    assert result["status"] == "admitted"
    observation, = observations_from(result["observations"])
    assert observation.evidence.kind is EvidenceKind.EXPERIMENT
    provenance = observation.evidence.payload["provenance"]
    assert provenance["authorization_id"] == instrument.grant.authorization_id
    assert provenance["source_id"] == instrument.grant.source_id != records.grant.source_id
    assert audits[1].journal.inspect()["attempts"] == 0
    assert runtime.health()["budgets"]["instrument_0"]["attempts"] == 1


@pytest.mark.parametrize("token_owner", (0, 1))
def test_metadata_and_business_tokens_cannot_authorize_instruments(instrument_runtime, token_owner):
    metadata, records, instrument, _, audits, _, service = instrument_runtime
    arm_instrument(instrument_runtime)
    other = (metadata, records)[token_owner]
    value = message(instrument)
    value["grant_token"] = authenticate(other.key, "read_grant", digest(other.config))
    assert service.dispatch("broker", "begin", value)["status"] == "denied"
    assert audits[2].journal.inspect()["attempts"] == 0


@pytest.mark.parametrize("field", ("tenant_id", "company", "source_id", "resource", "fields", "start", "end"))
def test_instrument_scope_denied_before_worker_or_reservation(instrument_runtime, field, monkeypatch):
    _, _, instrument, owners, audits, _, service = instrument_runtime
    arm_instrument(instrument_runtime)
    worker_calls = []
    monkeypatch.setattr(owners[2], "_worker", lambda *a, **kw: worker_calls.append(a))
    value = message(instrument)
    if field == "fields":
        value["request"][field].append("outside")
    elif field in ("start", "end"):
        value["request"][field] = "2023-01-01"
    else:
        value["request"][field] = "https://other.test" if field == "source_id" else "outside"
    assert service.dispatch("broker", "begin", value)["status"] == "denied"
    assert worker_calls == []
    assert audits[2].journal.inspect()["attempts"] == 0


@pytest.mark.parametrize("control_name", ("stop", "revoke"))
def test_instrument_control_persists_without_authority_on_restart(instrument_runtime, control_name):
    _, _, instrument, owners, audits, runtime, service = instrument_runtime
    arm_instrument(instrument_runtime)
    assert instrument_acquire(instrument, service)["status"] == "admitted"
    runtime.control("instrument_0", control(owners[2], control_name))
    assert service.dispatch("broker", "begin", message(instrument, "later"))["status"] == "denied"
    remote = RemoteJournal(
        lambda action, value: audits[2].dispatch("supervisor", action, value), digest(instrument.config)
    )
    replacement = AuthorizationCustody(instrument.config, remote)
    recovered = SupervisedReadOnlyRuntime(owners[0], owners[1], replacement)
    assert not recovered.metadata_admitted and not replacement.armed
    assert recovered.health()["budgets"]["instrument_0"]["attempts"] == 1
    assert recovered.health()["status"] == "blocked"
    with pytest.raises(ValueError):
        replacement.control(control(replacement, "arm"))


def test_wrong_instrument_kind_denied_before_credential_resolution(instrument_runtime, monkeypatch):
    _, _, instrument, *_ = instrument_runtime
    config = copy.deepcopy(instrument.config)
    config["grant"]["evidence_kind"] = EvidenceKind.DOCUMENTATION
    monkeypatch.delenv("BROKER_AUTH_KEY")
    with pytest.raises(ValueError, match="instrument experiment authorization"):
        Broker(config, instrument.state)


def test_missing_instrument_grant_token_denies_without_source_io(instrument_runtime, monkeypatch):
    _, _, instrument, owners, audits, _, service = instrument_runtime
    arm_instrument(instrument_runtime)
    calls = []
    monkeypatch.setattr(owners[2], "_worker", lambda *a, **kw: calls.append(a))
    value = message(instrument)
    del value["grant_token"]
    assert service.dispatch("broker", "begin", value)["status"] == "denied"
    assert calls == [] and audits[2].journal.inspect()["attempts"] == 0


def test_expired_instrument_cannot_arm_or_reserve(instrument_runtime, monkeypatch):
    _, _, instrument, owners, audits, runtime, service = instrument_runtime
    runtime.metadata_admitted = True  # Protected setup; expiry still enforces acquisition.
    monkeypatch.setattr("orion.pilot.broker.utc_now", lambda: owners[2].expires_at + timedelta(seconds=1))
    with pytest.raises(ValueError, match="stopped or expired"):
        runtime.control("instrument_0", control(owners[2], "arm"))
    assert service.dispatch("broker", "begin", message(instrument))["status"] == "denied"
    assert audits[2].journal.inspect()["attempts"] == 0


def test_exact_instrument_attempt_replay_does_not_repeat_io(instrument_runtime):
    _, _, instrument, _, audits, _, service = instrument_runtime
    arm_instrument(instrument_runtime)
    assert instrument_acquire(instrument, service)["status"] == "admitted"
    assert instrument_acquire(instrument, service)["status"] == "denied"
    assert audits[2].journal.inspect()["attempts"] == 1


def test_instrument_budget_restores_but_discovery_and_authority_do_not(instrument_runtime):
    _, _, instrument, owners, audits, _, service = instrument_runtime
    arm_instrument(instrument_runtime)
    assert instrument_acquire(instrument, service)["status"] == "admitted"
    remote = RemoteJournal(
        lambda action, value: audits[2].dispatch("supervisor", action, value), digest(instrument.config)
    )
    restarted = AuthorizationCustody(instrument.config, remote)
    recovered = SupervisedReadOnlyRuntime(owners[0], owners[1], restarted)
    assert recovered.health()["budgets"]["instrument_0"]["attempts"] == 1
    assert not restarted.armed and not recovered.metadata_admitted
    assert recovered.health()["instrument_authority_armed"] == {"instrument_0": False}
    assert recovered.handle(message(instrument, "after_restart"))["status"] == "denied"
    assert audits[2].journal.inspect()["attempts"] == 1


def test_gateway_uses_only_fixed_instrument_route_and_approved_binding(tmp_path):
    certificate = tmp_path / "certificate"
    certificate.write_bytes(b"synthetic-certificate")
    certificate.chmod(0o600)
    configs = [{"operation": op, "limits": {"response_bytes": 65536}} for op in ("metadata", "read", "instrument_0")]
    calls = []
    gateway = CredentialGateway(
        configs, lambda action, value: {"authorized": True, "binding": value["binding"]},
        "SyntheticCredential0123456789", str(certificate),
        hashlib.sha256(certificate.read_bytes()).hexdigest(), "192.0.2.2",
    )
    gateway.read = lambda path, **kwargs: calls.append(path) or b"{}"
    value = {"binding": digest(configs[2]), "receipt": "approved-one-use"}
    assert gateway.dispatch("acquisition", "acquire", value) == {"body": "7b7d"}
    assert calls == ["/instrument/0"]
    with pytest.raises(ValueError):
        gateway.dispatch("acquisition", "acquire", value | {"url": "https://elsewhere.test"})
    assert calls == ["/instrument/0"]
    with pytest.raises(ValueError, match="fixed gateway path"):
        CredentialGateway.read(gateway, "/instrument/1")
    assert calls == ["/instrument/0"]
