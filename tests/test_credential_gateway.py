"""Credential use is after live independent custody, with one fixed native route."""

import hashlib
import io
import json
from dataclasses import asdict
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from orion.contracts import EvidenceKind, utc_now
from orion.discovery.pilot_metadata import MetadataAuthorization, MetadataRequest
from orion.discovery.pilot_read import PilotAuthorization
from orion.discovery.read_window import ReviewedReadWindow
from orion.pilot.broker_contract import ERPNEXT_VERSION, digest
from orion.pilot.gateway import CredentialGateway, response_length
from orion.pilot.journal import JournalDenied, TransportLimits
from orion.understanding.role_checkpoint import _json


def candidate_configs():
    source = "https://opaque.test"
    expires = utc_now() + timedelta(hours=1)
    metadata = MetadataAuthorization(
        "metadata-authorization",
        MetadataRequest("tenant-1", "company-1", source),
        expires,
        True,
        10,
        2,
        (),
    )
    fields = ("name", "company", "docstatus", "posting_date", "amount")
    window = ReviewedReadWindow(
        "tenant-1",
        "company-1",
        "Sales Invoice",
        fields,
        "posting_date",
        date(2026, 1, 1),
        date(2026, 1, 7),
        expires,
    )
    records = PilotAuthorization(
        "record-authorization",
        source,
        window,
        "name",
        "company",
        "erpnext-historical-sample-read-only",
        EvidenceKind.API,
        1,
    )
    limits = asdict(TransportLimits(3, 32768, 65536, 524288, 1, 8, expires))
    common = {
        "version": ERPNEXT_VERSION,
        "mode": "candidate_erpnext_read_only",
        "caller": "installed-candidate",
        "limits": limits,
        "secret_reference": "BROKER_SOURCE_SECRET",
        "auth_reference": "BROKER_AUTH_KEY",
    }
    return json.loads(
        _json(
            [
                {
                    **common,
                    "operation": "metadata",
                    "grant": asdict(metadata),
                    "protocol": "erpnext_metadata_v1",
                    "field_classifications": {},
                },
                {
                    **common,
                    "operation": "read",
                    "grant": asdict(records),
                    "protocol": "erpnext_records_v1",
                    "field_classifications": {field: "public" for field in fields},
                },
            ]
        )
    )


def candidate_gateway(tmp_path):
    certificate = tmp_path / "candidate-certificate"
    certificate.write_bytes(b"synthetic-candidate-trust-only")
    certificate.chmod(0o600)
    configs = candidate_configs()
    calls = []

    def client(action, value):
        calls.append((action, value))
        return {"authorized": True, "binding": value["binding"]}

    owner = CredentialGateway(
        configs,
        client,
        "CandidateKey123456:CandidateSecret123456",
        str(certificate),
        hashlib.sha256(certificate.read_bytes()).hexdigest(),
        "192.0.2.2",
    )
    owner.read = lambda path, **kwargs: calls.append(("source", path, kwargs)) or b"{}"
    return owner, calls, configs


@pytest.fixture
def gateway(tmp_path, monkeypatch):
    certificate = tmp_path / "certificate"
    certificate.write_bytes(b"synthetic-trust-only")
    certificate.chmod(0o600)
    configs = [
        {"operation": op, "limits": {"response_bytes": 65536}} for op in ("metadata", "read")
    ]
    calls = []

    def client(action, value):
        calls.append((action, value))
        return {"authorized": True, "binding": value["binding"]}

    owner = CredentialGateway(
        configs,
        client,
        "SyntheticOnlyCredential0123456789",
        str(certificate),
        hashlib.sha256(certificate.read_bytes()).hexdigest(),
        "192.0.2.2",
    )
    owner.read = lambda path, **kw: calls.append(("source", path)) or b"{}"
    return owner, calls, configs, certificate


@pytest.mark.parametrize(
    "role,action",
    (
        ("broker", "acquire"),
        ("reasoner", "acquire"),
        ("acquisition", "issue"),
        ("owner", "acquire"),
    ),
)
def test_gateway_unauthorized_role_denies_before_custody_or_source(gateway, role, action):
    owner, calls, configs, _ = gateway
    with pytest.raises(JournalDenied):
        owner.dispatch(role, action, {"binding": digest(configs[1]), "receipt": "forged"})
    assert calls == []


@pytest.mark.parametrize("extra", ("url", "headers", "proxy", "port", "body", "redirect"))
def test_gateway_route_and_proxy_selectors_denied_before_source(gateway, extra):
    owner, calls, configs, _ = gateway
    with pytest.raises(ValueError):
        owner.dispatch(
            "acquisition",
            "acquire",
            {"binding": digest(configs[1]), "receipt": "forged", extra: "unapproved"},
        )
    assert calls == []


def test_gateway_custody_denial_or_wrong_scope_has_no_source_io(gateway):
    owner, calls, configs, _ = gateway
    value = {"binding": digest(configs[1]), "receipt": "forged"}

    def unavailable(*args):
        raise JournalDenied("unavailable")

    owner.client = unavailable
    with pytest.raises(JournalDenied):
        owner.dispatch("acquisition", "acquire", value)
    owner.client = lambda *args: {"authorized": True, "binding": "other"}
    with pytest.raises(JournalDenied):
        owner.dispatch("acquisition", "acquire", value)
    assert calls == []


def test_gateway_approval_precedes_fixed_source_route(gateway):
    owner, calls, configs, _ = gateway
    value = {"binding": digest(configs[1]), "receipt": "one-use-test-receipt"}
    assert owner.dispatch("acquisition", "acquire", value) == {"body": b"{}".hex()}
    assert calls == [("redeem", value), ("source", "/records")]


def test_gateway_changed_trust_denies_before_custody_or_source(gateway):
    owner, calls, configs, certificate = gateway
    certificate.write_bytes(b"changed")
    with pytest.raises(JournalDenied):
        owner.dispatch(
            "acquisition", "acquire", {"binding": digest(configs[0]), "receipt": "forged"}
        )
    assert calls == []


@pytest.mark.parametrize(
    "headers",
    (
        [],
        [("Content-Length", "2"), ("Content-Length", "2")],
        [("Content-Length", "999999")],
        [("Content-Length", "2"), ("Location", "/other")],
        [("Content-Length", "2"), ("Transfer-Encoding", "chunked")],
        [("Content-Length", "2"), ("Content-Encoding", "gzip")],
    ),
)
def test_gateway_strict_native_framing_matches_tls_owner(headers):
    with pytest.raises(ValueError):
        response_length(200, headers, 1024)


def test_native_transport_uses_no_credential_argv_proxy_redirect_or_url_selector(
    gateway, monkeypatch
):
    import orion.pilot.gateway as module

    owner, _, _, _ = gateway
    argv_seen, credential_pipe = [], []

    class Input(io.BytesIO):
        def close(self):
            credential_pipe.append(self.getvalue())
            super().close()

    class Native:
        stdin = Input()
        stdout = io.BytesIO(b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\n\r\n{}")

        def wait(self, timeout):
            return 0

        def poll(self):
            return 0

    def spawn(argv, **kwargs):
        argv_seen.extend(argv)
        assert kwargs["close_fds"] is True
        assert not any("proxy" in k.lower() for k in kwargs["env"])
        return Native()

    monkeypatch.setattr(module.subprocess, "Popen", spawn)
    assert CredentialGateway.read(owner, "/records") == b"{}"
    assert owner.credential not in " ".join(argv_seen)
    assert credential_pipe == [
        ('header = "Authorization: Bearer ' + owner.credential + '"\n').encode()
    ]
    assert argv_seen[:4] == ["/usr/bin/curl", "--disable", "--config", "-"]
    assert argv_seen[-1] == "https://192.0.2.2:44443/records"
    assert "--location" not in argv_seen and "--insecure" not in argv_seen
    assert argv_seen[argv_seen.index("--proxy") + 1] == ""
    assert argv_seen[argv_seen.index("--max-redirs") + 1] == "0"


def test_candidate_gateway_derives_exact_metadata_and_record_gets_from_grants(tmp_path):
    owner, calls, configs = candidate_gateway(tmp_path)
    metadata, records = configs
    catalog_request = {
        "request": metadata["grant"]["request"],
        "target": None,
    }
    record_window = records["grant"]["window"]
    record_request = {
        "request": {
            name: record_window[name]
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
    }
    record_request["request"].update(
        source_id=records["grant"]["source_id"],
        max_records=records["grant"]["max_records"],
    )

    for config, descriptor in ((metadata, catalog_request), (records, record_request)):
        binding = digest(config)
        request = {
            "receipt": "one-use-candidate-receipt",
            "binding": binding,
            "source_request": descriptor,
        }
        assert owner.dispatch("acquisition", "acquire", request) == {"body": b"{}".hex()}

    metadata_path = calls[1][1]
    record_path = calls[3][1]
    metadata_url = urlsplit(metadata_path)
    record_url = urlsplit(record_path)
    assert metadata_url.path == "/api/resource/DocType"
    assert parse_qs(metadata_url.query) == {
        "fields": ['["name"]'],
        "limit_start": ["0"],
        "limit_page_length": ["11"],
        "order_by": ["name asc"],
    }
    assert record_url.path == "/api/resource/Sales%20Invoice"
    assert parse_qs(record_url.query) == {
        "fields": ['["name","company","docstatus","posting_date","amount"]'],
        "filters": [
            (
                '[["company","=","company-1"],["docstatus","=",1],'
                '["posting_date",">=","2026-01-01"],'
                '["posting_date","<=","2026-01-07"]]'
            )
        ],
        "order_by": ["posting_date desc, name desc"],
        "limit_start": ["0"],
        "limit_page_length": ["1"],
    }
    assert calls[1][2] == {"limit": 32768}
    assert calls[3][2] == {"limit": 32768}
    assert calls[0] == (
        "redeem",
        {
            "receipt": "one-use-candidate-receipt",
            "binding": digest(metadata),
            "source_request_sha256": digest(catalog_request),
        },
    )
    assert calls[2][1]["source_request_sha256"] == digest(record_request)


@pytest.mark.parametrize(
    "mutation",
    (
        lambda request: request.update(tenant_id="other-tenant"),
        lambda request: request.update(source_id="https://other.test"),
        lambda request: request.update(resource="User"),
        lambda request: request.update(max_records=2),
    ),
)
def test_candidate_gateway_wrong_scope_denies_before_redemption_and_source(
    tmp_path, mutation
):
    owner, calls, configs = candidate_gateway(tmp_path)
    config = configs[1]
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
    mutation(request)
    with pytest.raises(JournalDenied, match="ERPNext record scope denied"):
        owner.dispatch(
            "acquisition",
            "acquire",
            {
                "receipt": "unconsumed-receipt",
                "binding": digest(config),
                "source_request": {"request": request},
            },
        )
    assert calls == []


def test_candidate_gateway_rejects_package_selected_transport_and_mixed_versions(tmp_path):
    owner, calls, configs = candidate_gateway(tmp_path)
    config = configs[0]
    with pytest.raises(ValueError, match="wire shape mismatch"):
        owner.dispatch(
            "acquisition",
            "acquire",
            {
                "receipt": "unconsumed-receipt",
                "binding": digest(config),
                "source_request": {
                    "request": config["grant"]["request"],
                    "target": None,
                },
                "url": "https://package-selected.invalid",
            },
        )
    assert calls == []

    certificate = tmp_path / "mixed-certificate"
    certificate.write_bytes(b"synthetic-mixed-trust-only")
    certificate.chmod(0o600)
    legacy = {"operation": "read", "limits": {"response_bytes": 65536}}
    with pytest.raises(ValueError, match="one versioned gateway mode required"):
        CredentialGateway(
            [configs[0], legacy],
            lambda *_: None,
            "CandidateKey123456:CandidateSecret123456",
            str(certificate),
            hashlib.sha256(certificate.read_bytes()).hexdigest(),
            "192.0.2.2",
        )


@pytest.mark.parametrize(
    "credential",
    (
        "CandidateKeyWithoutSecret",
        "short:short",
        "CandidateKey123456:secret with spaces",
        "CandidateKey123456:CandidateSecret123456:extra",
    ),
)
def test_candidate_gateway_requires_bounded_erpnext_token_credential(tmp_path, credential):
    certificate = tmp_path / "credential-certificate"
    certificate.write_bytes(b"synthetic-credential-trust-only")
    certificate.chmod(0o600)
    with pytest.raises(ValueError, match="bounded credential required"):
        CredentialGateway(
            candidate_configs(),
            lambda *_: None,
            credential,
            str(certificate),
            hashlib.sha256(certificate.read_bytes()).hexdigest(),
            "192.0.2.2",
        )
