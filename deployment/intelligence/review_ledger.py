"""Private one-use human review event for a persisted synthetic finding."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

from deployment.customer.login import CustomerLogin, LoginDenied
from deployment.release_candidate.continuous_lineage import _json, _write_private

from .procurement_report import ProcurementReportSource

DECISIONS = frozenset({"accepted_for_followup", "rejected", "needs_evidence"})


def record_review(
    *, source: ProcurementReportSource, login: CustomerLogin, session_secret: str,
    csrf_token: str, decision: str, rationale: str,
) -> str:
    """Bind one authenticated analyst decision to the exact candidate digest."""
    if decision not in DECISIONS or type(rationale) is not str or not 1 <= len(rationale) <= 500:
        raise ValueError("bounded reviewer decision required")
    access = login.authorize(session_secret, required_role="analyst", mutating=True,
                             csrf_token=csrf_token)
    if access.tenant_id != source.tenant_id:
        raise LoginDenied("reviewer tenant differs from finding")
    view = source.read_view()
    if view["procurement"]["status"] != "candidate_review" or (
            source.finding_path.parent / "review-1.json").exists():
        raise ValueError("review candidate unavailable")
    body = _json({
        "schema": "orion.procurement-review.v1",
        "tenant_id": access.tenant_id,
        "run_id": view["lineage"]["run_id"],
        "source_sha256": view["lineage"]["source_sha256"],
        "finding_sha256": source.finding_sha256,
        "reviewer_subject": access.subject,
        "decision": decision,
        "rationale": rationale,
        "reviewed_at": datetime.now(UTC).isoformat(),
        "independently_verified_savings_usd": None,
    })
    digest = hashlib.sha256(body).hexdigest()
    _write_private(source.finding_path.parent / "review-1.json", body)
    _write_private(source.finding_path.parent / "review-1.sha256", (digest + "\n").encode())
    return digest
