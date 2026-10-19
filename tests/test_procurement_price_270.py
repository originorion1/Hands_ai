"""Price arithmetic and exact source-to-review projection regressions."""

from __future__ import annotations

import json
import threading
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from deployment.customer.bounded_metadata import MetadataGrant, MetadataSessionDenied
from deployment.customer.enrollment import enroll_erpnext
from deployment.customer.journey import run_customer_synthetic_lineage
from deployment.customer.login import LoginDenied
from deployment.customer.metadata_approval import ApprovedMetadataGrantLedger, approval_packet
from deployment.customer.metadata_connection import (
    EnrolledMetadataConnection,
    run_confined_metadata_session,
    run_enrolled_metadata_session,
)
from deployment.customer.portal import AuthorizationProposal
from deployment.customer.portal_http import CustomerPortalHTTP
from deployment.customer.service import CustomerPortalService, CustomerReportBinding
from deployment.intelligence.procurement_report import (
    ProcurementReportSource,
    invalidate_with_successor,
    persist_procurement_finding,
)
from deployment.intelligence.review_ledger import record_review
from deployment.release_candidate.continuous_lineage import run_synthetic_lineage
from deployment.release_candidate.lineage_preview_drill import _synthetic_customer_session
from deployment.vercel.report import _validated_view
from evaluation.procurement_price_270.evaluate import evaluate
from evaluation.procurement_price_270.fixture import CASES, FIELDS, rows
from orion.business.procurement import ProcurementEvidenceError, investigate_purchases
from orion.contracts import EvidenceKind
from orion.discovery.credential_references import CredentialEnvironmentReferences
from orion.discovery.pilot_read import PilotAuthorization, PilotRequest
from orion.discovery.read_window import ReviewedReadWindow
from orion.presentation.report_interface import BoundReportSource, ReportInterfaceError
from orion_connector_sdk import ReviewedConnector, discover
from orion_connector_synthetic import SyntheticRecordConnector


def test_holdout_arithmetic_traps_and_baseline_can_win():
    result = evaluate()
    assert result["sample_cases"] == len(CASES) == 12
    assert result["scores"]["orion"] == {
        "tp": 3, "fp": 0, "fn": 0, "precision": 1.0, "recall": 1.0,
        "review_minutes_assumed": 18, "precision_wilson_95_interval": [0.438, 1.0],
    }
    assert result["scores"]["conventional_union"]["tp"] == 3
    assert result["synthetic_labeled_incremental_discoveries"] == 0
    assert result["independently_adjudicated_incremental_discoveries"] is None
    assert result["precision_target_80_representative_sample"] is False
    assert result["independently_verified_avoidable_cost_usd"] is None
    assert result["execution_allowed"] is False
    assert investigate_purchases(rows("holdout_a"), target_ref="holdout_a-T",
                                 company="Synthetic Procurement Co")[
        "estimated_exposure_usd"] == "60.00"
    stated_contract = investigate_purchases(
        rows("holdout_a"), target_ref="holdout_a-T", company="Synthetic Procurement Co"
    )
    assert "independent contract or supplier price-list record" in stated_contract[
        "missing_evidence"
    ]
    assert "contemporaneous market-price context" in stated_contract["missing_evidence"]
    assert stated_contract["independently_verified_savings_usd"] is None
    fx_case = investigate_purchases(
        rows("holdout_e"), target_ref="holdout_e-T", company="Synthetic Procurement Co"
    )
    assert "independent FX-rate evidence" in fx_case["missing_evidence"]
    for case in ("holdout_c", "holdout_g", "holdout_i"):
        assert result["case_statuses"][case] == "abstained"
    for case in ("holdout_d", "holdout_e", "holdout_f", "holdout_k", "holdout_l"):
        assert result["case_statuses"][case] == "no_anomaly"


def test_invalid_conversion_correction_and_scope_abstain():
    original = rows("holdout_a")
    for field, bad in (("units_per_pack", "0"), ("fx_to_usd", "0"),
                       ("fx_to_usd", "NaN"), ("fx_to_usd", "1.2"),
                       ("currency", "XYZ"), ("units_per_pack", "1e-20"),
                       ("unit_price", "1e30")):
        broken = (*original[:-1], original[-1] | {field: bad})
        with pytest.raises(ProcurementEvidenceError):
            investigate_purchases(broken, target_ref="holdout_a-T",
                                   company="Synthetic Procurement Co")
    extreme = (*original[:-1], original[-1] | {
        "units_per_pack": "1e-9", "unit_price": "1e12",
    })
    with pytest.raises(ProcurementEvidenceError, match="bound"):
        investigate_purchases(extreme, target_ref="holdout_a-T",
                               company="Synthetic Procurement Co")
    with pytest.raises(ProcurementEvidenceError, match="company"):
        investigate_purchases(original, target_ref="holdout_a-T", company="Other Co")
    corrected = (*original[:-1], original[-1] | {"corrected_by": "holdout_a-T2"})
    finding = investigate_purchases(corrected, target_ref="holdout_a-T",
                                    company="Synthetic Procurement Co")
    assert finding["status"] == "abstained"
    assert finding["confidence"] == "stale_source_record"
    assert finding["estimated_exposure_usd"] == "0.00"


def _admitted_run(tmp_path: Path, records=None):
    now = datetime.now(UTC)
    tenant = "synthetic-procurement-tenant"
    company = "Synthetic Procurement Co"
    records = records or rows("holdout_a")
    connector = SyntheticRecordConnector(
        tenant_id=tenant, source_id="synthetic-procurement-source",
        dataset_id="synthetic-procurement-holdout-a", records=records,
    )
    window = ReviewedReadWindow(
        tenant_id=tenant, company=company, resource="Synthetic Purchase",
        fields=FIELDS, date_field="posting_date",
        start=datetime(2026, 1, 1).date(), end=datetime(2026, 3, 1).date(),
        expires_at=now + timedelta(minutes=10),
    )
    grant = PilotAuthorization(
        authorization_id="synthetic-procurement-read", source_id=connector.source_id,
        window=window, identity_field="name", company_field="company",
        provenance_source="synthetic-record-v1", evidence_kind=EvidenceKind.SOURCE,
        max_records=len(records),
    )
    request = PilotRequest(
        tenant_id=tenant, company=company, source_id=connector.source_id,
        resource="Synthetic Purchase", fields=FIELDS, date_field="posting_date",
        start=window.start, end=window.end, max_records=len(records),
    )
    registration = discover(ReviewedConnector(
        "synthetic-records", "originorion-connector-synthetic", "0.2.0rc2",
        "orion_connector_synthetic:SyntheticRecordConnector",
    ))
    private = tmp_path / "run"
    private.mkdir(mode=0o700, parents=True)
    result = run_synthetic_lineage(
        registration=registration, connector=connector, request=request,
        authorization_id=grant.authorization_id,
        lookup=lambda identity: grant if identity == grant.authorization_id else None,
        run_id=uuid4(), private_dir=private, now=now,
    )
    return result, private


def test_admitted_source_persists_exact_finding_and_tampering_denies(tmp_path):
    result, private = _admitted_run(tmp_path)
    base = BoundReportSource.from_private_binding(result.binding_path)
    path, digest = persist_procurement_finding(
        base=base, target_ref="holdout_a-T", company="Synthetic Procurement Co"
    )
    source = ProcurementReportSource(base, path, digest)
    view = source.read_view()
    assert view["lineage"]["run_id"] == str(result.run_id)
    assert view["procurement"]["source_data_sha256"] == result.source_sha256
    assert view["procurement"]["evidence_ids"] == list(result.evidence_ids)
    assert view["procurement"]["estimated_exposure_usd"] == "60.00"
    assert _validated_view(json.dumps(view).encode()) == json.dumps(view).encode()
    for name in ("source.json", "evidence.json", "procurement_finding.json"):
        original = (private / name).read_bytes()
        (private / name).write_bytes(original + b" ")
        with pytest.raises(ReportInterfaceError):
            source.read_view()
        (private / name).write_bytes(original)
    assert source.read_view() == view
    unsafe = view | {"procurement": view["procurement"] | {"independently_verified_savings_usd": "60.00"}}
    with pytest.raises(ValueError):
        _validated_view(json.dumps(unsafe).encode())

    login, secret, csrf = _synthetic_customer_session(
        source.tenant_id, tmp_path / "review-auth", datetime.now(UTC)
    )
    with pytest.raises(ValueError):
        record_review(source=source, login=login, session_secret=secret,
                      csrf_token="forged", decision="accepted_for_followup",
                      rationale="Synthetic analyst requests contract review")
    digest = record_review(
        source=source, login=login, session_secret=secret, csrf_token=csrf,
        decision="accepted_for_followup",
        rationale="Synthetic analyst requests contract review",
    )
    reviewed = source.read_view()
    assert reviewed["procurement"]["reviewer_decision"] == "accepted_for_followup"
    assert reviewed["procurement"]["review_event_sha256"] == digest
    with pytest.raises(ValueError, match="unavailable"):
        record_review(source=source, login=login, session_secret=secret, csrf_token=csrf,
                      decision="rejected", rationale="Duplicate decision denied")
    review_path = private / "review-1.json"
    review_path.write_bytes(review_path.read_bytes() + b" ")
    with pytest.raises(ReportInterfaceError):
        source.read_view()


def test_customer_portal_reads_exact_persisted_finding_and_denies_revocation(tmp_path):
    result, _private = _admitted_run(tmp_path)
    base = BoundReportSource.from_private_binding(result.binding_path)
    path, digest = persist_procurement_finding(
        base=base, target_ref="holdout_a-T", company="Synthetic Procurement Co"
    )
    source = ProcurementReportSource(base, path, digest)
    login, secret, _csrf = _synthetic_customer_session(
        source.tenant_id, tmp_path / "portal-auth", datetime.now(UTC)
    )
    active = {"value": True}
    lock = threading.Lock()

    @contextmanager
    def commit_scope(_authorization_id):
        with lock:
            yield

    binding = CustomerReportBinding(
        source, "synthetic-procurement-read", lambda _id: active["value"],
        commit_scope,
    )
    service = CustomerPortalService(
        login, login._registry,
        report_lookup=lambda tenant: binding if tenant == source.tenant_id else None,
    )
    app = CustomerPortalHTTP(
        login=login, service=service, origin="https://command.synthetic.invalid",
    )

    def get(path_name, *, cookie=secret):
        response = {}

        def start(status, headers):
            response["status"] = status
            response["headers"] = headers

        body = b"".join(app({
            "wsgi.url_scheme": "https", "HTTP_HOST": "command.synthetic.invalid",
            "PATH_INFO": path_name, "REQUEST_METHOD": "GET",
            "HTTP_COOKIE": f"__Host-orion_session={cookie}",
        }, start))
        return response["status"], json.loads(body), response["headers"]

    status, report, _ = get("/customer/api/report")
    assert status == "200 OK"
    assert report == source.read_view()
    assert report["procurement"]["estimated_exposure_usd"] == "60.00"
    assert report["lineage"]["authorization_id"] == binding.authorization_id
    status, overview, _ = get("/customer/api/overview")
    assert status == "200 OK"
    assert overview["report_available"] is True
    assert overview["findings"] == [{
        "run_id": str(result.run_id), "status": "candidate_review",
    }]
    assert get("/customer/api/report", cookie="forged")[0] == "401 Unauthorized"
    original = path.read_bytes()
    path.write_bytes(original + b" ")
    assert get("/customer/api/report")[0] == "503 Service Unavailable"
    path.write_bytes(original)
    with lock:
        active["value"] = False
    status, denied, headers = get("/customer/api/report")
    assert status == "401 Unauthorized" and denied == {"error": "request denied"}
    assert any(name == "Set-Cookie" and "Max-Age=0" in value
               for name, value in headers)


def test_signed_metadata_to_procurement_to_authenticated_report_journey(tmp_path, monkeypatch):
    """One synthetic tenant and session traverse both governed acquisition stages."""
    now = datetime.now(UTC)
    tenant = "synthetic-procurement-tenant"
    company = "Synthetic Procurement Co"
    login, secret, csrf = _synthetic_customer_session(
        tenant, tmp_path / "identity", now,
    )
    scope = (("Purchase Order", ("next",)), ("Purchase Invoice", ("next",)))
    proposal = AuthorizationProposal(
        tenant_id=tenant, companies=(company,), fields_by_doctype=scope,
        sensitive_exclusions=("payroll", "personal records", "attachments"),
        historical_start=now.date(), historical_end=now.date(),
        max_requests=25, max_duration_seconds=1800, max_evidence_bytes=65536,
        retention_days=1, revocation_operator="Synthetic owner",
    )
    proposal_service = CustomerPortalService(login, login._registry)
    proposal_id = proposal_service.submit_authorization(
        secret, csrf_token=csrf, proposal=proposal,
    )
    review_packet = proposal_service.authorization_proposal(secret, proposal_id=proposal_id)
    reviewed_connector = ReviewedConnector(
        "erpnext", "originorion-connector-erpnext-discovery", "0.2.0rc4",
        "orion.discovery.erpnext_adapter:ERPNextDiscoveryAdapter",
    )
    schema = json.dumps({
        "schema_version": "orion.connector-configuration.v1", "connector": "erpnext",
        "fields": [
            {"name": name, "label": name.replace("_", " ").title(),
             "kind": kind, "required": True, "write_only": kind == "secret"}
            for name, kind in (("endpoint", "https_url"), ("api_key", "secret"),
                               ("api_secret", "secret"), ("company", "text"))
        ],
    }, sort_keys=True).encode()
    vault = {}

    def store(tenant_id, connector_name, key, secret_value):
        assert tenant_id == tenant and connector_name == "erpnext"
        reference = "vault:" + "v" * 28
        vault[reference] = (key, secret_value)
        return reference

    connection = enroll_erpnext(
        login=login, registry=login._registry, session_secret=secret,
        csrf_token=csrf, reviewed=reviewed_connector, schema_bytes=schema,
        schema_sha256=sha256(schema).hexdigest(), label="Synthetic ERPNext",
        endpoint="https://erp.example.com", approved_origin="https://erp.example.com",
        company=company, api_key="synthetic-key", api_secret="synthetic-secret",
        vault_store=store, vault_revoke=lambda reference: vault.pop(reference, None),
    )
    assert connection.execution_allowed is False
    metadata_grant = MetadataGrant(
        authorization_id="synthetic-metadata-read", tenant_id=tenant,
        external_customer_reference="Synthetic External Company", company=company,
        origin="https://erp.example.com",
        credential_references=CredentialEnvironmentReferences(
            "SYNTHETIC_KEY_REF", "SYNTHETIC_SECRET_REF",
        ),
        credential_vault_reference=connection.credential_ref,
        fields_by_doctype=scope, seed_doctype="Purchase Order",
        starts_at=now - timedelta(seconds=1), expires_at=now + timedelta(minutes=29),
        max_attempts=25, max_evidence_bytes=65536,
    )
    approved = approval_packet(review_packet, metadata_grant, proposal_id=proposal_id)
    signer = Ed25519PrivateKey.generate()
    signature = signer.sign(
        b"ORION METADATA APPROVAL V1\0" + json.dumps(
            approved, sort_keys=True, separators=(",", ":"),
        ).encode()
    )
    approval_dir = tmp_path / "approved"
    approval_dir.mkdir(mode=0o700)
    ledger = ApprovedMetadataGrantLedger(
        approval_dir / "ledger.sqlite", grant=metadata_grant, proposal=review_packet,
        packet=approved, signature=signature, approval_key=signer.public_key(),
    )
    enrolled = EnrolledMetadataConnection(
        login._registry, ledger, "Synthetic ERPNext",
        vault_read=lambda reference: vault[reference],
    )
    studied = []

    class Response:
        def __init__(self, url, payload):
            self.url = url
            self.payload = json.dumps(payload).encode()

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return None

        def geturl(self):
            return self.url

        def read(self, size):
            return self.payload[:size]

    def transport(request, *, timeout):
        assert timeout == 20 and request.get_method() == "GET"
        current = "Purchase Order" if not studied else "Purchase Invoice"
        studied.append(current)
        target = "Purchase Invoice" if current == "Purchase Order" else "Outside Scope"
        return Response(request.full_url, {"message": {"docs": [{
            "name": current, "fields": [{
                "fieldname": "next", "fieldtype": "Link", "options": target,
            }],
        }]}})

    metadata_dir = tmp_path / "metadata"
    metadata_dir.mkdir(mode=0o700)
    monkeypatch.setattr(
        "deployment.customer.metadata_connection.ReviewedMetadataTransport",
        lambda origin, ca_bundle: transport if origin == metadata_grant.origin
        and ca_bundle is None else None,
    )
    structural, study_source = run_confined_metadata_session(
        enrolled,
        state_directory=metadata_dir / "session",
        clock=lambda: now, monotonic=lambda: 0.0,
    )
    assert structural.studied_doctypes == ("Purchase Order", "Purchase Invoice")
    assert studied == list(structural.studied_doctypes)
    study_binding = enrolled.report_binding(study_source)
    study_portal = CustomerPortalService(
        login, login._registry,
        report_lookup=lambda chosen: study_binding if chosen == tenant else None,
    )
    study_view = study_portal.report(secret)
    assert study_view["lineage"]["evidence_ids"] == list(structural.evidence_digests)
    assert study_view["study"]["observed_doctypes"] == list(structural.studied_doctypes)
    assert study_portal.overview(secret)["autonomous_study_proposals"] == [
        ("What permitted structure does Purchase Invoice expose, given "
         "the observed authorized relationship?")
    ]
    study_app = CustomerPortalHTTP(
        login=login, service=study_portal, origin="https://command.synthetic.invalid",
    )
    study_response = {}

    def study_start(status, _headers):
        study_response["status"] = status

    study_body = b"".join(study_app({
        "wsgi.url_scheme": "https", "HTTP_HOST": "command.synthetic.invalid",
        "PATH_INFO": "/customer/api/report", "REQUEST_METHOD": "GET",
        "HTTP_COOKIE": f"__Host-orion_session={secret}",
    }, study_start))
    assert study_response["status"] == "200 OK" and json.loads(study_body) == study_view
    assert ledger.lookup(metadata_grant.authorization_id) is None
    with pytest.raises(MetadataSessionDenied, match="already used"):
        run_enrolled_metadata_session(
            enrolled, transport=transport, state_directory=metadata_dir / "replay",
            clock=lambda: now, monotonic=lambda: 0.0,
        )

    records = rows("holdout_a")
    connector = SyntheticRecordConnector(
        tenant_id=tenant, source_id="synthetic-procurement-source",
        dataset_id="synthetic-procurement-holdout-a", records=records,
    )
    window = ReviewedReadWindow(
        tenant_id=tenant, company=company, resource="Synthetic Purchase",
        fields=FIELDS, date_field="posting_date",
        start=datetime(2026, 1, 1).date(), end=datetime(2026, 3, 1).date(),
        expires_at=now + timedelta(minutes=10),
    )
    purchase_grant = PilotAuthorization(
        authorization_id="synthetic-procurement-read", source_id=connector.source_id,
        window=window, identity_field="name", company_field="company",
        provenance_source="synthetic-record-v1", evidence_kind=EvidenceKind.SOURCE,
        max_records=len(records),
    )
    request = PilotRequest(
        tenant_id=tenant, company=company, source_id=connector.source_id,
        resource="Synthetic Purchase", fields=FIELDS, date_field="posting_date",
        start=window.start, end=window.end, max_records=len(records),
    )
    registration = discover(ReviewedConnector(
        "synthetic-records", "originorion-connector-synthetic", "0.2.0rc2",
        "orion_connector_synthetic:SyntheticRecordConnector",
    ))
    purchase_dir = tmp_path / "purchase"
    purchase_dir.mkdir(mode=0o700)
    admitted = run_customer_synthetic_lineage(
        login=login, session_secret=secret, registration=registration,
        connector=connector, request=request,
        authorization_id=purchase_grant.authorization_id,
        lookup=lambda identity: purchase_grant if identity == purchase_grant.authorization_id else None,
        run_id=uuid4(), private_dir=purchase_dir, now=now,
    )
    base = BoundReportSource.from_private_binding(admitted.binding_path)
    finding_path, finding_digest = persist_procurement_finding(
        base=base, target_ref="holdout_a-T", company=company,
    )
    source = ProcurementReportSource(base, finding_path, finding_digest)
    grant_lock = threading.Lock()
    active = {"value": True}

    @contextmanager
    def report_commit(_authorization_id):
        with grant_lock:
            yield

    binding = CustomerReportBinding(
        source, purchase_grant.authorization_id,
        lambda _identity: active["value"], report_commit,
    )
    portal = CustomerPortalService(
        login, login._registry, report_lookup=lambda chosen: binding if chosen == tenant else None,
    )
    report = portal.report(secret)
    assert report == source.read_view()
    assert report["lineage"]["run_id"] == str(admitted.run_id)
    assert report["lineage"]["source_sha256"] == admitted.source_sha256
    assert report["procurement"]["estimated_exposure_usd"] == "60.00"
    assert report["procurement"]["independently_verified_savings_usd"] is None
    assert portal.overview(secret)["report_available"] is True
    app = CustomerPortalHTTP(
        login=login, service=portal, origin="https://command.synthetic.invalid",
    )
    response = {}

    def start(status, _headers):
        response["status"] = status

    api_body = b"".join(app({
        "wsgi.url_scheme": "https", "HTTP_HOST": "command.synthetic.invalid",
        "PATH_INFO": "/customer/api/report", "REQUEST_METHOD": "GET",
        "HTTP_COOKIE": f"__Host-orion_session={secret}",
    }, start))
    assert response["status"] == "200 OK" and json.loads(api_body) == report
    def revoke_during_vault(reference):
        login._registry.revoke_credential_reference(
            login._verified_session_identity(secret), tenant_id=tenant,
            label="Synthetic ERPNext",
        )
        return vault[reference]

    interrupted_vault = EnrolledMetadataConnection(
        login._registry, ledger, "Synthetic ERPNext",
        vault_read=revoke_during_vault,
    )
    with pytest.raises(MetadataSessionDenied, match="scope changed"):
        interrupted_vault.environment()
    assert enrolled.lookup(metadata_grant.authorization_id) is None
    with pytest.raises(MetadataSessionDenied, match="scope changed"):
        study_portal.report(secret)
    ledger.revoke()
    assert ledger.lookup(metadata_grant.authorization_id) is None
    with pytest.raises(MetadataSessionDenied, match="report authorization revoked"):
        study_portal.report(secret)
    with grant_lock:
        active["value"] = False
    with pytest.raises(LoginDenied, match="revoked"):
        portal.report(secret)


def test_authorized_correction_invalidates_persisted_candidate(tmp_path):
    original, _ = _admitted_run(tmp_path / "old")
    old_base = BoundReportSource.from_private_binding(original.binding_path)
    old_path, old_digest = persist_procurement_finding(
        base=old_base, target_ref="holdout_a-T", company="Synthetic Procurement Co"
    )
    old = ProcurementReportSource(old_base, old_path, old_digest)
    assert old.read_view()["procurement"]["status"] == "candidate_review"
    corrected = (*rows("holdout_a")[:-1],
                 rows("holdout_a")[-1] | {"corrected_by": "holdout_a-T2"})
    next_run, _ = _admitted_run(tmp_path / "new", corrected)
    next_base = BoundReportSource.from_private_binding(next_run.binding_path)
    next_path, next_digest = persist_procurement_finding(
        base=next_base, target_ref="holdout_a-T", company="Synthetic Procurement Co"
    )
    successor = ProcurementReportSource(next_base, next_path, next_digest)
    assert successor.read_view()["procurement"]["status"] == "abstained"
    invalidate_with_successor(previous=old, successor=successor)
    updated = old.read_view()["procurement"]
    assert updated["status"] == "invalidated"
    assert updated["invalidation_history"][0]["successor_run_id"] == str(next_run.run_id)
    invalidated_view = old.read_view()
    assert _validated_view(json.dumps(invalidated_view).encode()) == json.dumps(
        invalidated_view
    ).encode()
    invalidation_path = old_path.parent / "invalidation-1.json"
    digest_path = invalidation_path.with_suffix(".sha256")
    original_event = invalidation_path.read_bytes()
    original_digest = digest_path.read_bytes()
    invalidation = json.loads(original_event)
    invalidation["reason"] = "forged reason"
    forged = (json.dumps(invalidation, sort_keys=True) + "\n").encode()
    invalidation_path.write_bytes(forged)
    digest_path.write_bytes((sha256(forged).hexdigest() + "\n").encode())
    with pytest.raises(ReportInterfaceError):
        old.read_view()
    invalidation_path.write_bytes(original_event)
    digest_path.write_bytes(original_digest)
    invalidation_path.write_bytes(invalidation_path.read_bytes() + b" ")
    with pytest.raises(ReportInterfaceError):
        old.read_view()
    invalidation_path.write_bytes(invalidation_path.read_bytes()[:-1])
    next_path.write_bytes(next_path.read_bytes() + b" ")
    with pytest.raises(ReportInterfaceError):
        old.read_view()
