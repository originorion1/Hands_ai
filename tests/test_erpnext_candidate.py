"""Native ERPNext response normalization stays behind canonical grants."""

import hashlib
import json

import pytest
from test_credential_gateway import candidate_configs

from orion.pilot.broker_contract import authenticate, digest
from orion.pilot.broker_metadata import acquire_metadata, erpnext_proposal_from
from orion.pilot.broker_worker import acquire
from orion.pilot.custody import AuditCustody, AuthorizationCustody, RemoteJournal
from orion.pilot.journal import JournalDenied


@pytest.mark.parametrize("fields", [[], [
    {"fieldname": "opaque_date", "fieldtype": "Date"},
    {"fieldname": "opaque_number", "fieldtype": "Float"},
]])
def test_native_non_submittable_schema_retains_structural_unknowns(tmp_path, fields):
    config = candidate_configs()[0]
    resource = "Opaque Resource"
    body = json.dumps({"message": {"docs": [{
        "name": resource, "is_submittable": 0, "fields": fields,
    }]}}).encode()
    path = _private_response(tmp_path, body)
    response = acquire_metadata({
        "operation": "metadata",
        "grant": config["grant"],
        "request": config["grant"]["request"],
        "path": str(path),
        "source_digest": hashlib.sha256(body).hexdigest(),
        "protocol": config["protocol"],
        "secret": "synthetic-normalizer-only",
        "field_classifications": {},
        "target": resource,
    })
    assert response["available"] is True
    proposal = erpnext_proposal_from(
        resource, response["fields"], response["date_fields"], response["declarations"]
    )
    assert proposal.fields == proposal.date_fields == ()
    assert {c.declaration.name for c in proposal.interpretation.candidates} == {
        field["fieldname"] for field in fields
    }
    assert "record_authorization_missing" in proposal.interpretation.unknowns
    assert "business_meaning_unconfirmed" in proposal.interpretation.unknowns


def test_structural_only_proposal_cannot_smuggle_executable_dates():
    with pytest.raises(ValueError, match="structural proposal"):
        erpnext_proposal_from("Opaque Resource", [], ["opaque_date"], [])


def _private_response(tmp_path, body):
    path = tmp_path / "response.json"
    path.write_bytes(body)
    path.chmod(0o600)
    return path


def _record_request(config):
    window = config["grant"]["window"]
    request = {
        name: window[name]
        for name in (
            "tenant_id",
            "company",
            "resource",
            "fields",
            "date_field",
            "start",
            "end",
        )
    }
    request.update(
        source_id=config["grant"]["source_id"],
        max_records=config["grant"]["max_records"],
    )
    return request


def test_malformed_native_record_response_denies_without_fabricated_observation(tmp_path):
    config = candidate_configs()[1]
    body = b'{"data":[{"name":"partial"}'
    path = _private_response(tmp_path, body)
    bootstrap = {
        "grant": config["grant"],
        "request": _record_request(config),
        "path": str(path),
        "source_digest": hashlib.sha256(body).hexdigest(),
        "protocol": config["protocol"],
        "secret": "synthetic-normalizer-only",
        "field_classifications": config["field_classifications"],
    }
    with pytest.raises(ValueError, match="upstream_read_failed"):
        acquire(bootstrap)


def test_changed_native_response_denies_before_record_parsing(tmp_path):
    config = candidate_configs()[1]
    body = b'{"data":[]}'
    path = _private_response(tmp_path, body)
    bootstrap = {
        "grant": config["grant"],
        "request": _record_request(config),
        "path": str(path),
        "source_digest": "0" * 64,
        "protocol": config["protocol"],
        "secret": "synthetic-normalizer-only",
        "field_classifications": config["field_classifications"],
    }
    with pytest.raises(ValueError, match="response changed"):
        acquire(bootstrap)


def test_malformed_native_metadata_response_denies_without_proposal(tmp_path):
    config = candidate_configs()[0]
    body = b'{"message":'
    path = _private_response(tmp_path, body)
    bootstrap = {
        "operation": "metadata",
        "grant": config["grant"],
        "request": config["grant"]["request"],
        "path": str(path),
        "source_digest": hashlib.sha256(body).hexdigest(),
        "protocol": config["protocol"],
        "secret": "synthetic-normalizer-only",
        "field_classifications": {},
        "target": None,
    }
    with pytest.raises(ValueError, match="catalog rejected"):
        acquire_metadata(bootstrap)


def test_candidate_receipt_binds_exact_source_descriptor_before_redemption(
    tmp_path, monkeypatch
):
    config = candidate_configs()[1]
    issuer = b"candidate-issuer-key-000000000000000000"
    monkeypatch.setenv("BROKER_AUTH_KEY", issuer.decode())
    monkeypatch.setenv("BROKER_SOURCE_SECRET", "synthetic-normalizer-only")
    audit_directory = tmp_path / "audit"
    audit_directory.mkdir(mode=0o700)
    audit = AuditCustody(
        audit_directory,
        b"candidate-audit-key-0000000000000000000",
        config,
    )
    journal = RemoteJournal(
        lambda action, value: audit.dispatch("supervisor", action, value),
        digest(config),
    )
    owner = AuthorizationCustody(config, journal)
    control = {"control": "arm", "nonce": owner.nonce, "head": owner.journal.head}
    owner.control({**control, "mac": authenticate(issuer, "control", control)})
    message = {
        "operation": "read",
        "grant_token": authenticate(issuer, "read_grant", digest(config)),
        "request_id": "candidate-read-1",
        "request": _record_request(config),
    }

    offer = owner.dispatch("broker", "begin", message)
    assert offer["status"] == "offered"
    wrong = json.loads(json.dumps(offer["source_request"]))
    wrong["request"]["resource"] = "User"
    with pytest.raises(JournalDenied, match="source redemption denied"):
        owner.dispatch(
            "source",
            "redeem",
            {
                "receipt": offer["receipt"],
                "binding": digest(config),
                "source_request_sha256": digest(wrong),
            },
        )

    assert owner.dispatch(
        "source",
        "redeem",
        {
            "receipt": offer["receipt"],
            "binding": digest(config),
            "source_request_sha256": digest(offer["source_request"]),
        },
    ) == {"authorized": True}
    body = json.dumps(
        {
            "data": [
                {
                    "name": "SINV-0001",
                    "company": "company-1",
                    "docstatus": 1,
                    "posting_date": "2026-01-01",
                    "amount": 9,
                }
            ]
        }
    ).encode()
    admitted = owner.dispatch(
        "broker",
        "complete",
        {"receipt": offer["receipt"], "body": body.hex()},
    )
    assert admitted["status"] == "admitted"
    assert admitted["budget"]["attempts"] == 1
