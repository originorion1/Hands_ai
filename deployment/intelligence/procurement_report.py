"""Persist and recheck an evidence-linked procurement finding on every read."""

from __future__ import annotations

import hashlib
import hmac
import json
from dataclasses import dataclass
from pathlib import Path

from deployment.release_candidate.continuous_lineage import _json, _write_private
from orion.business.procurement import ProcurementEvidenceError, investigate_purchases
from orion.presentation.report_interface import BoundReportSource, ReportInterfaceError
from orion.stores.private_files import PrivateFileError, read_private_file


def _read_json(path: Path, label: str) -> dict:
    try:
        body = read_private_file(path, label=label, maximum_bytes=64 * 1024)
        value = json.loads(body)
    except (PrivateFileError, OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ReportInterfaceError(f"{label} unavailable") from exc
    if type(value) is not dict:
        raise ReportInterfaceError(f"{label} invalid")
    return value


def persist_procurement_finding(
    *, base: BoundReportSource, target_ref: str, company: str,
) -> tuple[Path, str]:
    """Create one immutable derived report from an already admitted source."""
    view = base.read_view()
    if view.get("report_kind") != "synthetic_lineage":
        raise ReportInterfaceError("synthetic lineage required")
    source = _read_json(base.report_path.parent / "source.json", "admitted source")
    evidence = _read_json(base.report_path.parent / "evidence.json", "admitted evidence")
    if (source.get("tenant_id") != base.tenant_id or
            source.get("run_id") != view["lineage"]["run_id"] or
            source.get("source_sha256") != view["lineage"]["source_sha256"] or
            type(source.get("source")) is not list):
        raise ReportInterfaceError("source identity mismatch")
    source_ids = {item["provenance"]["source_id"] for item in evidence["evidence"]}
    if len(source_ids) != 1:
        raise ReportInterfaceError("connector source identity mismatch")
    try:
        finding = investigate_purchases(tuple(source["source"]), target_ref=target_ref,
                                        company=company)
    except ProcurementEvidenceError as exc:
        raise ReportInterfaceError("procurement evidence invalid") from exc
    document = {
        "schema": "orion.procurement-finding.v1",
        "tenant_id": base.tenant_id,
        "run_id": view["lineage"]["run_id"],
        "source_sha256": view["lineage"]["source_sha256"],
        "base_report_sha256": base.report_sha256,
        "evidence_acquired_at": max(item["observed_at"] for item in evidence["evidence"]),
        "evidence_ids": view["lineage"]["evidence_ids"],
        "authorization_id": view["lineage"]["authorization_id"],
        "connector_identity": "originorion-connector-synthetic@0.2.0rc2",
        "source_id": next(iter(source_ids)),
        "finding": finding,
    }
    body = _json(document)
    path = base.report_path.parent / "procurement_finding.json"
    _write_private(path, body)
    return path, hashlib.sha256(body).hexdigest()


def invalidate_with_successor(
    *, previous: ProcurementReportSource, successor: ProcurementReportSource,
) -> str:
    """Record a new admitted run that supersedes an earlier candidate."""
    prior = previous.read_view()
    current = successor.read_view()
    old = prior["procurement"]
    new = current["procurement"]
    if (old["status"] != "candidate_review" or
            current["lineage"]["run_id"] == prior["lineage"]["run_id"] or
            previous.tenant_id != successor.tenant_id or
            old["company"] != new["company"] or old["target_ref"] != new["target_ref"] or
            old["source_data_sha256"] == new["source_data_sha256"] or
            new["status"] == "candidate_review"):
        raise ReportInterfaceError("successor does not invalidate candidate")
    body = _json({
        "schema": "orion.procurement-invalidation.v1",
        "tenant_id": previous.tenant_id,
        "previous_run_id": prior["lineage"]["run_id"],
        "previous_finding_sha256": previous.finding_sha256,
        "successor_run_id": current["lineage"]["run_id"],
        "successor_source_sha256": new["source_data_sha256"],
        "successor_binding_path": str(successor.base.report_path.parent / "binding.json"),
        "successor_finding_sha256": successor.finding_sha256,
        "reason": new["confidence"],
    })
    path = previous.finding_path.parent / "invalidation-1.json"
    _write_private(path, body)
    digest = hashlib.sha256(body).hexdigest()
    _write_private(path.with_suffix(".sha256"), (digest + "\n").encode())
    return digest


@dataclass(frozen=True, slots=True)
class ProcurementReportSource:
    base: BoundReportSource
    finding_path: Path
    finding_sha256: str

    @property
    def tenant_id(self) -> str:
        return self.base.tenant_id

    def read_view(self) -> dict[str, object]:
        """Recompute from the original source after base custody validation."""
        base_view = self.base.read_view()
        if (base_view.get("report_kind") != "synthetic_lineage" or
                self.finding_path != self.base.report_path.parent / "procurement_finding.json" or
                self.finding_path.is_symlink()):
            raise ReportInterfaceError("procurement report binding invalid")
        try:
            finding_body = read_private_file(
                self.finding_path, label="procurement finding", maximum_bytes=64 * 1024
            )
            if not hmac.compare_digest(hashlib.sha256(finding_body).hexdigest(),
                                       self.finding_sha256):
                raise ReportInterfaceError("procurement finding digest mismatch")
            document = json.loads(finding_body)
            source = _read_json(self.base.report_path.parent / "source.json", "admitted source")
            evidence = _read_json(self.base.report_path.parent / "evidence.json", "admitted evidence")
            if (type(document) is not dict or set(document) != {
                "schema", "tenant_id", "run_id", "source_sha256", "base_report_sha256",
                "evidence_acquired_at", "evidence_ids", "authorization_id", "finding",
                "connector_identity", "source_id",
            } or document["schema"] != "orion.procurement-finding.v1" or
                    document["tenant_id"] != self.tenant_id or
                    document["run_id"] != base_view["lineage"]["run_id"] or
                    document["source_sha256"] != base_view["lineage"]["source_sha256"] or
                    document["base_report_sha256"] != self.base.report_sha256 or
                    document["evidence_ids"] != base_view["lineage"]["evidence_ids"] or
                    document["authorization_id"] != base_view["lineage"]["authorization_id"] or
                    document["connector_identity"] !=
                    "originorion-connector-synthetic@0.2.0rc2" or
                    {item["provenance"]["source_id"] for item in evidence["evidence"]} !=
                    {document["source_id"]} or
                    document["evidence_acquired_at"] != max(
                        item["observed_at"] for item in evidence["evidence"]
                    )):
                raise ReportInterfaceError("procurement lineage mismatch")
            finding = document["finding"]
            if type(finding) is not dict:
                raise ReportInterfaceError("procurement finding invalid")
            recalculated = investigate_purchases(
                tuple(source["source"]), target_ref=finding["target_ref"],
                company=finding["company"],
            )
            if recalculated != finding:
                raise ReportInterfaceError("procurement conclusion changed")
        except (PrivateFileError, UnicodeError, json.JSONDecodeError, KeyError,
                TypeError, ProcurementEvidenceError) as exc:
            raise ReportInterfaceError("procurement evidence unavailable") from exc
        review_decision = "pending"
        review_sha256 = None
        review_path = self.finding_path.parent / "review-1.json"
        review_digest_path = self.finding_path.parent / "review-1.sha256"
        if review_path.exists() or review_digest_path.exists():
            if review_path.is_symlink():
                raise ReportInterfaceError("review ledger unavailable")
            try:
                review_body = read_private_file(
                    review_path, label="procurement review", maximum_bytes=4096
                )
                digest_body = read_private_file(
                    review_digest_path,
                    label="procurement review digest", maximum_bytes=65,
                )
                if digest_body != (hashlib.sha256(review_body).hexdigest() + "\n").encode():
                    raise ReportInterfaceError("review ledger digest mismatch")
                review = json.loads(review_body)
            except (PrivateFileError, UnicodeError, json.JSONDecodeError) as exc:
                raise ReportInterfaceError("review ledger unavailable") from exc
            if (type(review) is not dict or set(review) != {
                "schema", "tenant_id", "run_id", "source_sha256", "finding_sha256",
                "reviewer_subject", "decision", "rationale", "reviewed_at",
                "independently_verified_savings_usd",
            } or review["schema"] != "orion.procurement-review.v1" or
                    review["tenant_id"] != self.tenant_id or
                    review["run_id"] != base_view["lineage"]["run_id"] or
                    review["source_sha256"] != base_view["lineage"]["source_sha256"] or
                    review["finding_sha256"] != self.finding_sha256 or
                    review["decision"] not in {
                        "accepted_for_followup", "rejected", "needs_evidence"
                    } or review["independently_verified_savings_usd"] is not None):
                raise ReportInterfaceError("review ledger identity mismatch")
            review_decision = review["decision"]
            review_sha256 = hashlib.sha256(review_body).hexdigest()
        invalidation_history = []
        status = finding["status"]
        invalidation_path = self.finding_path.parent / "invalidation-1.json"
        invalidation_digest_path = invalidation_path.with_suffix(".sha256")
        if invalidation_path.exists() or invalidation_digest_path.exists():
            if invalidation_path.is_symlink():
                raise ReportInterfaceError("invalidation ledger unavailable")
            try:
                invalidation_body = read_private_file(
                    invalidation_path, label="invalidation ledger", maximum_bytes=4096
                )
                expected_digest = read_private_file(
                    invalidation_digest_path, label="invalidation digest", maximum_bytes=65
                )
                if expected_digest != (hashlib.sha256(invalidation_body).hexdigest() + "\n").encode():
                    raise ReportInterfaceError("invalidation digest mismatch")
                invalidation = _read_json(invalidation_path, "invalidation ledger")
                if (Path(invalidation["successor_binding_path"]).resolve() ==
                        (self.base.report_path.parent / "binding.json").resolve()):
                    raise ReportInterfaceError("invalidation successor repeats source")
                successor_base = BoundReportSource.from_private_binding(
                    Path(invalidation["successor_binding_path"])
                )
                successor = ProcurementReportSource(
                    successor_base,
                    successor_base.report_path.parent / "procurement_finding.json",
                    invalidation["successor_finding_sha256"],
                ).read_view()
            except (KeyError, TypeError, PrivateFileError) as exc:
                raise ReportInterfaceError("invalidation ledger invalid") from exc
            if (set(invalidation) != {
                "schema", "tenant_id", "previous_run_id", "previous_finding_sha256",
                "successor_run_id", "successor_source_sha256", "successor_binding_path",
                "successor_finding_sha256", "reason",
            } or invalidation["schema"] != "orion.procurement-invalidation.v1" or
                    invalidation["tenant_id"] != self.tenant_id or
                    invalidation["previous_run_id"] != base_view["lineage"]["run_id"] or
                    invalidation["previous_finding_sha256"] != self.finding_sha256 or
                    invalidation["successor_run_id"] != successor["lineage"]["run_id"] or
                    invalidation["successor_source_sha256"] !=
                    successor["procurement"]["source_data_sha256"] or
                    successor["procurement"]["status"] == "candidate_review" or
                    successor["procurement"]["company"] != finding["company"] or
                    successor["procurement"]["target_ref"] != finding["target_ref"]):
                raise ReportInterfaceError("invalidation lineage mismatch")
            status = "invalidated"
            invalidation_history = [{
                "successor_run_id": invalidation["successor_run_id"],
                "successor_source_sha256": invalidation["successor_source_sha256"],
                "reason": invalidation["reason"],
            }]
        return base_view | {
            "interface_version": "orion.readonly-report.v3",
            "report_kind": "procurement_price_leakage",
            "procurement": finding | {
                "status": status,
                "invalidation_history": invalidation_history,
                "reviewer_decision": review_decision,
                "review_event_sha256": review_sha256,
                "finding_sha256": self.finding_sha256,
                "base_report_sha256": self.base.report_sha256,
                "source_system": "synthetic-records",
                "connector_identity": document["connector_identity"],
                "source_id": document["source_id"],
                "authorization_status": "read_only_admitted",
                "evidence_acquired_at": document["evidence_acquired_at"],
                "evidence_ids": document["evidence_ids"],
                "authorization_id": document["authorization_id"],
                "tenant_id": document["tenant_id"],
                "run_id": document["run_id"],
                "source_data_sha256": document["source_sha256"],
            },
        }
