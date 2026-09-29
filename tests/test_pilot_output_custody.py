"""Hostile application output is not independent admitted-evidence custody."""

import copy

import pytest

from orion.pilot import deployment
from orion.pilot.broker_contract import digest
from orion.pilot.deployment import Deployment
from orion.pilot.journal import JournalDenied


@pytest.fixture
def boundary(monkeypatch):
    owner = Deployment.__new__(Deployment)
    owner.configs = [{"operation": "read", "caller": "synthetic", "tenant": "synthetic"}]
    owner.endpoints = {"evidence": "/independent/evidence"}
    owner.caps = {"owner": b"protected-host-owner-capability"}
    owner.health = lambda: {"status": "healthy"}
    message = {"operation": "read", "request_id": "one", "grant_token": "synthetic", "request": {}}
    observations = [{"canonical": "protected admitted observation"}]
    stored = {
        "head": "a" * 64, "payload_sha256": digest(observations), "journal_head": "b" * 64,
        "request_sha256": digest(message), "observations": observations,
    }
    names = (
        "user_separated", "pid_separated", "mnt_separated", "capabilities_dropped",
        "network_confined", "environment_cleared", "issuer_inaccessible", "signing-key_inaccessible",
        "credential_inaccessible", "worker-secret_inaccessible", "custody_storage_inaccessible",
    )
    value = {"reasoner_checks": dict.fromkeys(names, True), "response": {
        "status": "admitted", "head": stored["journal_head"], "observations": observations,
        "checkpoint": {"checkpoint": 2, "head": stored["head"], "payload_sha256": stored["payload_sha256"]},
    }}
    calls = []

    def protected_rpc(endpoint, role, key, action, data):
        calls.append((endpoint, role, key, action, data))
        assert data == {"binding": digest(owner.configs[0]), "arguments": {
            "checkpoint": 2, "request_reference": digest(("synthetic", "one")),
        }}
        return copy.deepcopy(stored)

    monkeypatch.setattr(deployment, "rpc", protected_rpc)
    return owner, message, value, stored, calls


def test_controller_uses_independent_custody_and_discards_application_fields(boundary):
    owner, message, value, _, calls = boundary
    value["response"].update(execution_allowed=True, interpretation="VALIDATED", secret="fake-secret")
    result = owner.validate_reasoner_response(message, value)
    assert len(calls) == 1 and calls[0][1:4] == ("owner", owner.caps["owner"], "resolve")
    assert result["response"]["execution_allowed"] is False
    assert result["response"]["interpretation"] == "UNKNOWN"
    assert "secret" not in result["response"]


@pytest.mark.parametrize("mutation", ("empty_checks", "integer_checks", "extra_checks", "body", "head", "pin", "scope", "token", "request", "custody_loss"))
def test_hostile_output_cannot_replace_protected_exact_request(boundary, mutation):
    owner, message, value, _, _ = boundary
    if mutation == "empty_checks":
        value["reasoner_checks"] = {}
    elif mutation == "integer_checks":
        value["reasoner_checks"]["network_confined"] = 1
    elif mutation == "extra_checks":
        value["reasoner_checks"]["invented_proof"] = True
    elif mutation == "body":
        value["response"]["observations"] = [{"forged": "synthetic"}]
    elif mutation == "head":
        value["response"]["head"] = "c" * 64
    elif mutation == "pin":
        value["response"]["checkpoint"]["head"] = "c" * 64
    elif mutation == "scope":
        message["operation"] = "metadata"
    elif mutation in ("token", "request"):
        message["grant_token" if mutation == "token" else "request"] = "widened synthetic scope"
    else:
        owner.health = lambda: {"status": "blocked"}
    with pytest.raises(JournalDenied):
        owner.validate_reasoner_response(message, value)


def test_claimed_denial_cannot_smuggle_unadmitted_observations(boundary):
    owner, message, value, _, calls = boundary
    value["response"].update(status="denied", observations=[{"forged": "synthetic"}])
    result = owner.validate_reasoner_response(message, value)
    assert not calls
    assert result["response"] == {"status": "denied", "execution_allowed": False, "allow_live_customer_access": False}


def test_unavailable_archive_is_not_replaced_by_application_claims(boundary, monkeypatch):
    owner, message, value, _, _ = boundary

    def unavailable(*args):
        raise JournalDenied("independent custody unavailable")

    monkeypatch.setattr(deployment, "rpc", unavailable)
    with pytest.raises(JournalDenied):
        owner.validate_reasoner_response(message, value)


def test_controller_replay_denies_before_launch(boundary, monkeypatch):
    owner, message, _, _, _ = boundary

    def inspect(*args):
        return {"available": True, "checkpoints": [{"request_reference": digest(("synthetic", "one"))}]}

    def must_not_launch(*args, **kwargs):
        pytest.fail("hostile application was launched for accepted request replay")

    monkeypatch.setattr(deployment, "rpc", inspect)
    monkeypatch.setattr(deployment, "process_command", must_not_launch)
    with pytest.raises(JournalDenied):
        owner.reason(message)
