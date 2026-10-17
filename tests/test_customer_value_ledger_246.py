from __future__ import annotations

from base64 import b64encode
from dataclasses import replace

import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from evaluation.intelligence_benchmark_230.value_ledger import (
    Adjudication,
    Finding,
    Outcome,
    ValueLedger,
    amount,
    evaluate_synthetic,
    signed_payload,
)

DIGEST = "a" * 64


def test_synthetic_fixture_keeps_realized_and_roi_unproven():
    report = evaluate_synthetic()
    assert report["data_class"] == "synthetic"
    assert report["incremental_validated_discoveries"] == 0
    assert report["realized_customer_savings_usd"] == "0.00"
    assert report["net_incremental_value_usd"] is None
    assert report["roi"] is None
    assert report["execution_allowed"] is False


def test_synthetic_cannot_gain_verified_or_realized_value():
    ledger = ValueLedger()
    ledger.record_finding(Finding("f", "tenant-a", DIGEST, "synthetic", "20.00"))
    with pytest.raises(ValueError, match="independent customer review"):
        ledger.adjudicate(Adjudication("f", "tenant-a", "reviewer", "accepted", "1.00", DIGEST))
    ledger.adjudicate(Adjudication("f", "tenant-a", "synthetic-label", "accepted", "0.00", None))
    with pytest.raises(ValueError, match="synthetic outcomes"):
        ledger.record_outcome(Outcome("f", "tenant-a", "1.00", DIGEST))
    with pytest.raises(ValueError, match="synthetic findings"):
        ledger.roi_scenario(
            "tenant-a", conventional_realized_usd="0.00", analyst_cost_usd="0.00",
            investigation_cost_usd="0.00", onboarding_cost_usd="0.00",
            support_cost_usd="0.00", subscription_cost_usd="0.00",
            provider_delivery_cost_usd="0.00",
        )


def test_customer_review_and_outcome_are_tenant_bound_and_immutable():
    review_key = Ed25519PrivateKey.generate()
    outcome_key = Ed25519PrivateKey.generate()
    def public(key):
        return key.public_key().public_bytes(serialization.Encoding.Raw,
                                             serialization.PublicFormat.Raw)
    ledger = ValueLedger(review_keys={"independent-reviewer": public(review_key)},
                         outcome_keys={"independent-outcome": public(outcome_key)})
    finding = Finding("f", "tenant-a", DIGEST, "customer", "20.00")
    ledger.record_finding(finding)
    review_signature = b64encode(review_key.sign(signed_payload("orion.value.review.v1", {
        "finding_id": "f", "tenant_id": "tenant-a", "decision": "accepted",
        "verified_avoidable_usd": "10.00", "review_evidence_sha256": DIGEST,
    }))).decode()
    review = Adjudication("f", "tenant-a", "independent-reviewer", "accepted", "10.00",
                          DIGEST, review_signature)
    with pytest.raises(ValueError, match="tenant mismatch"):
        ledger.adjudicate(replace(review, tenant_id="tenant-b"))
    ledger.adjudicate(review)
    with pytest.raises(ValueError, match="immutable"):
        ledger.adjudicate(review)
    with pytest.raises(ValueError, match="tenant-bound"):
        ledger.record_outcome(Outcome("f", "tenant-b", "5.00", DIGEST))
    with pytest.raises(ValueError, match="exceeds"):
        ledger.record_outcome(Outcome("f", "tenant-a", "11.00", DIGEST))
    with pytest.raises(ValueError, match="verifier"):
        ledger.record_outcome(Outcome("f", "tenant-a", "5.00", DIGEST))
    outcome_signature = b64encode(outcome_key.sign(signed_payload("orion.value.outcome.v1", {
        "finding_id": "f", "tenant_id": "tenant-a", "realized_savings_usd": "5.00",
        "independent_outcome_sha256": DIGEST, "verifier_id": "independent-outcome",
    }))).decode()
    outcome = Outcome("f", "tenant-a", "5.00", DIGEST, "independent-outcome",
                      outcome_signature)
    with pytest.raises(ValueError, match="invalid independent signature"):
        ledger.record_outcome(replace(outcome, signature_b64=review_signature))
    ledger.record_outcome(outcome)
    assert ledger.totals("tenant-a")["realized_savings_usd"] == "5.00"
    assert ledger.totals("tenant-b")["realized_savings_usd"] == "0"
    scenario = ledger.roi_scenario(
        "tenant-a", conventional_realized_usd="1.00", analyst_cost_usd="1.00",
        investigation_cost_usd="1.00", onboarding_cost_usd="1.00",
        support_cost_usd="1.00", subscription_cost_usd="1.00",
        provider_delivery_cost_usd="0.50",
    )
    assert scenario["net_incremental_value_usd_estimate"] == "-1.00"
    assert scenario["roi_estimate"] == "-0.2"
    assert scenario["gross_margin_estimate"] == "0.5"
    assert scenario["classification"] == "ESTIMATED_SCENARIO"


@pytest.mark.parametrize("bad", ["-1", "NaN", "Infinity", "1.001"])
def test_invalid_money_denied(bad):
    with pytest.raises(ValueError):
        amount(bad)


def test_invalid_provenance_and_duplicate_finding_denied():
    ledger = ValueLedger()
    with pytest.raises(ValueError, match="digest"):
        ledger.record_finding(Finding("f", "tenant-a", "not-a-hash", "synthetic", "1.00"))
    item = Finding("f", "tenant-a", DIGEST, "synthetic", "1.00")
    ledger.record_finding(item)
    with pytest.raises(ValueError, match="duplicate"):
        ledger.record_finding(item)
