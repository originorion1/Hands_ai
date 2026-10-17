"""Offline value accounting. A synthetic label is never a customer outcome."""

from __future__ import annotations

import hashlib
import json
from base64 import b64decode
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

ROOT = Path(__file__).parent


def amount(value: str) -> Decimal:
    result = Decimal(value)
    if not result.is_finite() or result < 0 or result.as_tuple().exponent < -2:
        raise ValueError("amount must be a nonnegative finite decimal in cents")
    return result


def digest(value: str) -> str:
    if len(value) != 64 or any(char not in "0123456789abcdef" for char in value):
        raise ValueError("evidence digest must be lowercase SHA-256")
    return value


@dataclass(frozen=True)
class Finding:
    finding_id: str
    tenant_id: str
    evidence_sha256: str
    source: str  # synthetic or customer
    estimated_exposure_usd: str

    def __post_init__(self) -> None:
        if self.source not in {"synthetic", "customer"}:
            raise ValueError("unknown evidence source")
        if not self.finding_id or not self.tenant_id:
            raise ValueError("finding and tenant identity required")
        digest(self.evidence_sha256)
        amount(self.estimated_exposure_usd)


@dataclass(frozen=True)
class Adjudication:
    finding_id: str
    tenant_id: str
    reviewer_id: str
    decision: str  # accepted or rejected
    verified_avoidable_usd: str
    review_evidence_sha256: str | None
    signature_b64: str | None = None


@dataclass(frozen=True)
class Outcome:
    finding_id: str
    tenant_id: str
    realized_savings_usd: str
    independent_outcome_sha256: str
    verifier_id: str | None = None
    signature_b64: str | None = None


def signed_payload(domain: str, fields: dict) -> bytes:
    return json.dumps({"domain": domain, **fields}, sort_keys=True,
                      separators=(",", ":")).encode()


def verify_signature(key_bytes: bytes, payload: bytes, signature_b64: str | None) -> None:
    if not signature_b64:
        raise ValueError("independent signature required")
    try:
        Ed25519PublicKey.from_public_bytes(key_bytes).verify(
            b64decode(signature_b64, validate=True), payload,
        )
    except (InvalidSignature, ValueError) as exc:
        raise ValueError("invalid independent signature") from exc


class ValueLedger:
    def __init__(self, *, review_keys: dict[str, bytes] | None = None,
                 outcome_keys: dict[str, bytes] | None = None) -> None:
        self.findings: dict[str, Finding] = {}
        self.adjudications: dict[str, Adjudication] = {}
        self.outcomes: dict[str, Outcome] = {}
        self.review_keys = review_keys or {}
        self.outcome_keys = outcome_keys or {}

    def record_finding(self, item: Finding) -> None:
        if item.finding_id in self.findings:
            raise ValueError("duplicate finding")
        self.findings[item.finding_id] = item

    def adjudicate(self, item: Adjudication) -> None:
        finding = self.findings.get(item.finding_id)
        if finding is None or finding.tenant_id != item.tenant_id:
            raise ValueError("finding tenant mismatch")
        if item.finding_id in self.adjudications:
            raise ValueError("adjudication is immutable")
        value = amount(item.verified_avoidable_usd)
        if item.decision not in {"accepted", "rejected"}:
            raise ValueError("invalid reviewer decision")
        if value:
            if (finding.source != "customer" or item.decision != "accepted"
                    or not item.reviewer_id or not item.review_evidence_sha256):
                raise ValueError("verified cost requires independent customer review")
            digest(item.review_evidence_sha256)
            key = self.review_keys.get(item.reviewer_id)
            if key is None:
                raise ValueError("reviewer is not trusted")
            verify_signature(key, signed_payload("orion.value.review.v1", {
                "finding_id": item.finding_id, "tenant_id": item.tenant_id,
                "decision": item.decision, "verified_avoidable_usd": item.verified_avoidable_usd,
                "review_evidence_sha256": item.review_evidence_sha256,
            }), item.signature_b64)
        if value > amount(finding.estimated_exposure_usd):
            raise ValueError("verified cost exceeds estimated exposure")
        self.adjudications[item.finding_id] = item

    def record_outcome(self, item: Outcome) -> None:
        finding = self.findings.get(item.finding_id)
        review = self.adjudications.get(item.finding_id)
        if finding is None or finding.tenant_id != item.tenant_id or review is None:
            raise ValueError("outcome lacks tenant-bound review")
        if item.finding_id in self.outcomes:
            raise ValueError("outcome is immutable")
        value = amount(item.realized_savings_usd)
        if finding.source != "customer" and value:
            raise ValueError("synthetic outcomes cannot realize savings")
        if value > amount(review.verified_avoidable_usd):
            raise ValueError("realized savings exceeds independently verified cost")
        digest(item.independent_outcome_sha256)
        if value:
            key = self.outcome_keys.get(item.verifier_id or "")
            if key is None or item.verifier_id == review.reviewer_id:
                raise ValueError("independent outcome verifier required")
            verify_signature(key, signed_payload("orion.value.outcome.v1", {
                "finding_id": item.finding_id, "tenant_id": item.tenant_id,
                "realized_savings_usd": item.realized_savings_usd,
                "independent_outcome_sha256": item.independent_outcome_sha256,
                "verifier_id": item.verifier_id,
            }), item.signature_b64)
        self.outcomes[item.finding_id] = item

    def totals(self, tenant_id: str) -> dict[str, str | int]:
        findings = [item for item in self.findings.values() if item.tenant_id == tenant_id]
        return {
            "findings": len(findings),
            "estimated_exposure_usd": str(sum(
                (amount(item.estimated_exposure_usd) for item in findings), Decimal(0)
            )),
            "verified_avoidable_usd": str(sum((
                amount(self.adjudications[item.finding_id].verified_avoidable_usd)
                for item in findings if item.finding_id in self.adjudications
            ), Decimal(0))),
            "realized_savings_usd": str(sum((
                amount(self.outcomes[item.finding_id].realized_savings_usd)
                for item in findings if item.finding_id in self.outcomes
            ), Decimal(0))),
        }

    def roi_scenario(self, tenant_id: str, *, conventional_realized_usd: str,
                     analyst_cost_usd: str, investigation_cost_usd: str,
                     onboarding_cost_usd: str, support_cost_usd: str,
                     subscription_cost_usd: str, provider_delivery_cost_usd: str) -> dict:
        """Calculate a labeled scenario; assumptions do not become verified facts."""
        if not any(item.tenant_id == tenant_id for item in self.findings.values()):
            raise ValueError("tenant has no findings")
        if any(item.source == "synthetic" for item in self.findings.values()
               if item.tenant_id == tenant_id):
            raise ValueError("synthetic findings cannot yield customer ROI")
        inputs = {name: amount(value) for name, value in {
            "conventional_realized_usd": conventional_realized_usd,
            "analyst_cost_usd": analyst_cost_usd,
            "investigation_cost_usd": investigation_cost_usd,
            "onboarding_cost_usd": onboarding_cost_usd,
            "support_cost_usd": support_cost_usd,
            "subscription_cost_usd": subscription_cost_usd,
        }.items()}
        realized = amount(self.totals(tenant_id)["realized_savings_usd"])
        provider_delivery = amount(provider_delivery_cost_usd)
        subscription = inputs["subscription_cost_usd"]
        cost = sum((value for name, value in inputs.items()
                    if name != "conventional_realized_usd"), Decimal(0))
        net = realized - inputs["conventional_realized_usd"] - cost
        return {
            "classification": "ESTIMATED_SCENARIO",
            "tenant_id": tenant_id,
            "verified_realized_savings_usd": str(realized),
            "conventional_realized_usd_assumption": str(inputs["conventional_realized_usd"]),
            "total_incremental_cost_usd_assumption": str(cost),
            "net_incremental_value_usd_estimate": str(net),
            "roi_estimate": str(net / cost) if cost else None,
            "gross_margin_estimate": str((subscription - provider_delivery) / subscription)
            if subscription else None,
            "provider_delivery_cost_usd_assumption": str(provider_delivery),
        }


def evaluate_synthetic() -> dict:
    """Reproduce the existing benchmark's economic boundary without new labels."""
    raw = (ROOT / "results.json").read_bytes()
    benchmark = json.loads(raw)
    ledger = ValueLedger()
    for scoped_id, source in sorted(benchmark["orion_provenance"].items()):
        tenant, _period = scoped_id.rsplit(":", 1)
        ledger.record_finding(Finding(
            finding_id=scoped_id, tenant_id=tenant,
            evidence_sha256=source["relevant_evidence_sha256"],
            source="synthetic", estimated_exposure_usd="0.00",
        ))
        ledger.adjudicate(Adjudication(
            finding_id=scoped_id, tenant_id=tenant,
            reviewer_id="synthetic-design-label", decision="accepted",
            verified_avoidable_usd="0.00", review_evidence_sha256=None,
        ))
    minutes = Decimal(benchmark["candidate_review_minutes_assumption"])
    rate = Decimal(benchmark["analyst_hourly_usd_assumption"])
    conventional_cost = Decimal(benchmark["conventional"]["tp"] +
                                benchmark["conventional"]["fp"]) * minutes * rate / 60
    orion_cost = Decimal(benchmark["orion_actual_missing_document_only"]["tp"] +
                         benchmark["orion_actual_missing_document_only"]["fp"]) * minutes * rate / 60
    return {
        "schema": "orion.customer-value-ledger.v1",
        "data_class": "synthetic",
        "benchmark_results_sha256": hashlib.sha256(raw).hexdigest(),
        "orion_tenant_totals": {tenant: ledger.totals(tenant) for tenant in sorted({
            finding.tenant_id for finding in ledger.findings.values()
        })},
        "conventional_review_cost_usd_estimate": str(conventional_cost),
        "orion_review_cost_usd_estimate": str(orion_cost),
        "incremental_validated_discoveries": benchmark["incremental_orion_validated_discoveries"],
        "verified_avoidable_usd": "0.00",
        "realized_customer_savings_usd": "0.00",
        "investigation_cost_usd": None,
        "onboarding_cost_usd": None,
        "support_cost_usd": None,
        "subscription_revenue_usd": None,
        "gross_margin": None,
        "net_incremental_value_usd": None,
        "roi": None,
        "verdict": "NOT_ESTABLISHED",
        "execution_allowed": False,
    }


if __name__ == "__main__":
    print(json.dumps(evaluate_synthetic(), indent=2, sort_keys=True))
