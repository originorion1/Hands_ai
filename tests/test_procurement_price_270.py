"""Price arithmetic and exact source-to-review projection regressions."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from uuid import uuid4

import pytest

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
