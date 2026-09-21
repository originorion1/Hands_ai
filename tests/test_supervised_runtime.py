"""Concrete custody and integrated runtime risks; canonical owners, no real ERP."""

import copy
import sqlite3
import sys
from datetime import timedelta
from pathlib import Path

import pytest
from protected_custody_support import Endpoint, rpc
from test_broker_metadata import MetadataHarness
from test_supervised_broker import Harness

from orion.contracts import utc_now
from orion.pilot.broker_contract import authenticate, digest, observations_from
from orion.pilot.custody import AuditCustody, AuthorizationCustody, RemoteJournal, RuntimeCustody
from orion.pilot.journal import JournalDenied
from orion.pilot.runtime import SupervisedReadOnlyRuntime

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))


@pytest.fixture
def deployment(tmp_path, monkeypatch):
    r = Harness(tmp_path / "read")
    m = MetadataHarness(
        tmp_path / "metadata",
        schemas={"r_01": [{"name": "f_c", "kind": "date", "classification": "public"}]},
    )
    m.key, m.secret = r.key, r.secret
    m.write_source()
    monkeypatch.setenv("BROKER_AUTH_KEY", r.key.decode())
    monkeypatch.setenv("BROKER_SOURCE_SECRET", r.secret)
    audits, owners = [], []
    for h in (m, r):
        custody = AuditCustody(h.state, b"independent-audit-test-key-00000000", h.config)
        audits.append(custody)
        remote = RemoteJournal(
            lambda action, value, a=custody: a.dispatch("supervisor", action, value),
            digest(h.config),
        )
        owners.append(AuthorizationCustody(h.config, remote))
    runtime = SupervisedReadOnlyRuntime(*owners)
    return m, r, audits, owners, runtime, RuntimeCustody(runtime)


def control(owner, action):
    payload = {"control": action, "nonce": owner.nonce, "head": owner.journal.head}
    return payload | {"mac": authenticate(owner.key, "control", payload)}


def acquire(h, service):
    reply = service.dispatch("broker", "begin", h.message())
    while reply.get("status") == "offered":
        receipt = reply["receipt"]
        service.dispatch("source", "redeem", {"receipt": receipt, "binding": digest(h.config)})
        reply = service.dispatch(
            "broker", "complete", {"receipt": receipt, "body": h.source.read_bytes().hex()}
        )
    return reply


def test_metadata_is_mandatory_but_does_not_issue_record_authority(deployment):
    m, r, audits, owners, runtime, service = deployment
    with pytest.raises(ValueError):
        service.dispatch("broker", "begin", r.message())
    with pytest.raises(ValueError):
        runtime.control("read", control(owners[1], "arm"))
    assert audits[1].journal.inspect()["attempts"] == 0
    runtime.control("metadata", control(owners[0], "arm"))
    result = acquire(m, service)
    assert result["status"] == "admitted" and result["interpretation"] == "UNKNOWN"
    assert not owners[1].armed
    runtime.control("read", control(owners[1], "arm"))
    result = acquire(r, service)
    assert result["status"] == "admitted"
    o = observations_from(result["observations"])[0]
    assert o.evidence.payload["provenance"]["authorization_id"] == r.grant.authorization_id
    with pytest.raises(TypeError):
        o.evidence.payload["record"]["f_d"] = 0
    assert runtime.health()["live_ready"] is False


@pytest.mark.parametrize(
    "attack",
    [
        "token",
        "tenant_id",
        "company",
        "source_id",
        "fields",
        "resource",
        "operation",
        "url",
        "grant",
    ],
)
def test_compromised_requester_cannot_mint_or_expand_grants(deployment, attack):
    _, r, audits, owners, runtime, service = deployment
    runtime.metadata_admitted = True  # Trusted setup only; not a wire operation.
    runtime.control("read", control(owners[1], "arm"))
    message = r.message()
    if attack == "token":
        message["grant_token"] = authenticate(
            b"requester-capability-not-issuer-key", "read_grant", digest(r.config)
        )
    elif attack == "fields":
        message["request"]["fields"].append("unapproved")
    elif attack in ("tenant_id", "company", "source_id", "resource"):
        message["request"][attack] = "https://other.test" if attack == "source_id" else "other"
    elif attack == "operation":
        message["operation"] = "write"
    else:
        message[attack] = "unapproved"
    try:
        result = service.dispatch("broker", "begin", message)
        assert result["status"] == "denied"
    except ValueError:
        pass
    assert audits[1].journal.inspect()["attempts"] == 0


@pytest.mark.parametrize("action", ["control", "redeem", "sign", "revoke", "widen", "health"])
def test_authorization_boundary_enforces_requester_role(deployment, action):
    *_, service = deployment
    with pytest.raises(ValueError):
        service.dispatch("broker", action, {})


@pytest.mark.parametrize("action", ["rewrite", "truncate", "rollback", "begin", "stop", "records"])
def test_audit_boundary_denies_unauthorized_callers(deployment, action):
    _, r, audits, *_ = deployment
    owner = audits[1]
    head = owner.journal.head
    with pytest.raises(ValueError):
        owner.dispatch("broker", action, {"binding": digest(r.config), "arguments": {}})
    assert owner.journal.head == head


def test_audit_enforces_binding_and_digest_reference_scope(deployment):
    _, r, audits, *_ = deployment
    owner = audits[1]
    with pytest.raises(ValueError):
        owner.dispatch("supervisor", "inspect", {"binding": "0" * 64, "arguments": {}})
    with pytest.raises(ValueError):
        owner.dispatch(
            "supervisor",
            "lifecycle",
            {
                "binding": digest(r.config),
                "arguments": {
                    "event": "broker_start",
                    "at": utc_now().isoformat(),
                    "references": {"scope": "0" * 64, "caller": digest(r.config["caller"])},
                },
            },
        )


@pytest.mark.parametrize("ending", ["stop", "revoke", "expiry", "audit-loss"])
def test_source_redemption_independently_denies_continuation(deployment, monkeypatch, ending):
    _, r, audits, owners, runtime, service = deployment
    runtime.metadata_admitted = True
    runtime.control("read", control(owners[1], "arm"))
    receipt = service.dispatch("broker", "begin", r.message())["receipt"]
    if ending in ("stop", "revoke"):
        runtime.control("read", control(owners[1], ending))
    elif ending == "expiry":
        monkeypatch.setattr(
            "orion.pilot.custody.utc_now", lambda: owners[1].expires_at + timedelta(seconds=1)
        )
    else:
        owners[1].journal.client = lambda *a: (_ for _ in ()).throw(JournalDenied("unavailable"))
    with pytest.raises(ValueError):
        service.dispatch("source", "redeem", {"receipt": receipt, "binding": digest(r.config)})
    assert audits[1].journal.inspect()["attempts"] == 1


def test_receipt_replay_and_unauthorized_completion_denied(deployment):
    _, r, _, owners, runtime, service = deployment
    runtime.metadata_admitted = True
    runtime.control("read", control(owners[1], "arm"))
    receipt = service.dispatch("broker", "begin", r.message())["receipt"]
    with pytest.raises(ValueError):
        service.dispatch(
            "broker", "complete", {"receipt": receipt, "body": r.source.read_bytes().hex()}
        )
    for bad in ("0" * 128, receipt[:64] + "0" * 64):
        with pytest.raises(ValueError):
            service.dispatch("source", "redeem", {"receipt": bad, "binding": digest(r.config)})
    service.dispatch("source", "redeem", {"receipt": receipt, "binding": digest(r.config)})
    with pytest.raises(ValueError):
        service.dispatch("source", "redeem", {"receipt": receipt, "binding": digest(r.config)})
    assert (
        service.dispatch(
            "broker", "complete", {"receipt": receipt, "body": r.source.read_bytes().hex()}
        )["status"]
        == "admitted"
    )


@pytest.mark.parametrize("mutation", ["rewrite", "rollback", "missing"])
def test_protected_tip_rejects_corruption_without_silent_recovery(deployment, mutation):
    _, r, audits, *_ = deployment
    owner = audits[1]
    snapshot = (r.state / "broker.db").read_bytes()
    owner.dispatch(
        "supervisor",
        "begin",
        {
            "binding": digest(r.config),
            "arguments": {"now": utc_now().isoformat(), "request_bytes": 1},
        },
    )
    if mutation == "rewrite":
        with sqlite3.connect(r.state / "broker.db") as db:
            db.execute("UPDATE events SET body='{}' WHERE sequence=2")
    elif mutation == "rollback":
        (r.state / "broker.db").write_bytes(snapshot)  # Trusted adversarial injector, not broker.
    else:
        (r.state / "broker.db").unlink()
    with pytest.raises(ValueError):
        AuditCustody(r.state, b"independent-audit-test-key-00000000", r.config)


def test_fresh_rpc_challenges_authenticate_callers_and_reject_captured_replay(tmp_path):
    key = b"authenticated-role-test-key-000000"
    received = []
    capture = []
    endpoint = Endpoint(
        tmp_path / "service",
        {"broker": key},
        lambda role, action, value: received.append(value) or value,
    )
    try:
        assert rpc(tmp_path / "service", "broker", key, "echo", 7, capture=capture) == 7
        with pytest.raises(ValueError):
            rpc(tmp_path / "service", "owner", key, "echo", 8)
        # Replayed MAC under a new challenge fails even if the attacker knows role.
        import socket

        from protected_custody_support import receive, send

        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as client:
            client.settimeout(2)
            client.connect(str(tmp_path / "service"))
            with client.makefile("rwb") as stream:
                receive(stream)
                send(stream, capture[0])
                assert receive(stream)["body"]["ok"] is False
        assert received == [7]
    finally:
        endpoint.close()


def test_runtime_report_tampering_cannot_relabel_deployment_as_pass():
    from pilot_runtime import main, valid_report

    report = {
        "status": "PASS",
        "production_containment": "NOT PROVEN",
        "execution_allowed": False,
        "live_ready": False,
        "allow_live_customer_access": False,
        "cases": [],
    }
    assert not valid_report(report)
    forged = copy.deepcopy(report)
    forged["live_ready"] = True
    assert not valid_report(forged)
    assert main(["--start"]) == 2


def test_complete_deployment_witnesses_are_required_by_public_validator():
    from pilot_runtime import BROKER_CHECKS, CUSTODY_CHECKS, REASONER_CHECKS, valid_report

    cases = []
    for ending in ("stop", "revoke", "audit-loss", "auth-loss", "pending-stop"):
        acquired = ending in ("stop", "revoke")
        checks = {
            "broker": dict.fromkeys(BROKER_CHECKS, True),
            "auth": dict.fromkeys(CUSTODY_CHECKS, True),
            "audit": dict.fromkeys(CUSTODY_CHECKS, True),
        }
        if acquired:
            checks["reasoner"] = dict.fromkeys(REASONER_CHECKS, True)
        if ending == "stop":
            checks["emergency_stop"] = {"kernel_egress_removed": True, "broker_terminated": True}
        if ending in ("audit-loss", "auth-loss"):
            checks["restart_denial"] = {"pending_custody_refuses_authority": True}
        targets = {"ipv4-unapproved", "ipv4-alternate-port", "ipv4-proxy"}
        cases.append(
            {
                "status": "PASS",
                "ending": ending,
                "checks": checks,
                "protocol": "local_columns_v1" if ending == "revoke" else "local_rows_v1",
                "ipv6_enabled": False,
                "source_outside_broker": True,
                "live_ready": False,
                "execution_allowed": False,
                "reachable": dict.fromkeys(targets | {"approved"}, True),
                "kernel_denials": [
                    {"target": t, "counter": "deny_" + t.replace("-", "_"), "rejects": 1}
                    for t in targets
                ],
                "read_source_io": int(acquired),
                "metadata_source_io": 4,
                "budget_after_restart": {
                    "attempts": 1,
                    "reserved_bytes": 65536,
                    "pending": not acquired,
                    "stopped": ending == "pending-stop",
                },
                "provenance_intact": True,
                "interpretation": "UNKNOWN",
                "reasoner": {"canonical_immutable": True},
            }
        )
    report = {
        "status": "PASS",
        "cases": cases,
        "production_containment": "NOT PROVEN",
        "live_ready": False,
        "execution_allowed": False,
        "allow_live_customer_access": False,
    }
    assert valid_report(report)
    for mutation in (
        "missing-custody",
        "missing-key-proof",
        "no-counter",
        "missing-destination",
        "unreachable",
        "budget-reset",
        "authority-restored",
    ):
        bad = copy.deepcopy(report)
        item = bad["cases"][0]
        if mutation == "missing-custody":
            item["checks"].pop("audit")
        elif mutation == "missing-key-proof":
            item["checks"]["broker"].pop("issuer_key_inaccessible")
        elif mutation == "no-counter":
            item["kernel_denials"][0]["rejects"] = 0
        elif mutation == "missing-destination":
            item["reachable"].pop("ipv4-unapproved")
            item["kernel_denials"] = [
                d for d in item["kernel_denials"] if d["target"] != "ipv4-unapproved"
            ]
        elif mutation == "unreachable":
            item["reachable"]["ipv4-unapproved"] = False
        elif mutation == "budget-reset":
            item["budget_after_restart"]["attempts"] = 0
        else:
            bad["live_ready"] = True
        assert not valid_report(bad)
