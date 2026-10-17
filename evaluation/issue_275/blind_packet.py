"""Export source-only synthetic cases for a reviewer blinded to predictions."""

from __future__ import annotations

import hashlib
import json
from decimal import ROUND_HALF_UP, Decimal

from evaluation.procurement_price_270.fixture import CASES, rows

ALTERNATIVES = [
    "Check whether item grade or specification differs.",
    "Check freight, delivery urgency and other landed-cost terms.",
    "Check tax, discount and supplier rebate treatment.",
    "Check pack size and unit-of-measure equivalence.",
    "Check currency-rate date and independent FX quote.",
    "Check signed contract or price-list effective dates.",
    "Check market-price changes during the period.",
    "Check duplicate and corrected source records.",
]


def _normalized(row: dict) -> dict[str, str]:
    """Show mechanical arithmetic without a finding or exposure conclusion."""
    money = Decimal(row["unit_price"])
    discount = Decimal(row["discount_rate"])
    fx = Decimal(row["fx_to_usd"])
    tax = Decimal(row["tax_rate"])
    pack = Decimal(row["units_per_pack"])
    unit = money * (1 - discount) * fx / ((1 + tax) * pack)
    quantity = Decimal(row["quantity"]) * pack
    return {
        "source_ref": row["name"],
        "quantity_each": str(quantity),
        "net_unit_usd_display": str(unit.quantize(Decimal("0.0001"), rounding=ROUND_HALF_UP)),
        "formula": "unit_price * (1-discount_rate) * fx_to_usd / ((1+tax_rate) * units_per_pack)",
        "fx_provenance": "source field only; independent rate document unavailable",
        "rounding_note": "display rounded to four decimals; use unrounded Decimal for exposure",
    }


def packet() -> dict[str, object]:
    cases = []
    for case in sorted(CASES):
        source = rows(case)
        source_body = json.dumps(source, sort_keys=True, separators=(",", ":")).encode()
        cases.append({
            "case_id": case,
            "source_sha256": hashlib.sha256(source_body).hexdigest(),
            "source_rows": source,
            "normalized_calculations": [_normalized(row) for row in source],
            "alternative_explanations_to_test": ALTERNATIVES,
            "review_question": (
                "Is the target purchase an avoidable price difference after checking "
                "unit, tax, discount, currency, contract, supplier, quality, "
                "delivery and conflicting records? If evidence is insufficient, abstain."
            ),
            "adjudication_questions": [
                "Which source records are genuinely comparable, and which are excluded?",
                "What independent contract, rebate, freight and FX documents are missing?",
                "Does the normalized comparison support avoidability or only a candidate?",
                "What exposure, if any, follows from your own unrounded calculation?",
                "Which alternative explanation would overturn your decision?",
            ],
        })
    return {
        "schema": "orion.procurement-blind-review.v1",
        "source_type": "designed synthetic records",
        "labels_or_detector_predictions_included": False,
        "reviewer_fields": [
            "decision: avoidable | legitimate | insufficient",
            "calculated_exposure_usd or null",
            "source_refs_used",
            "alternative_explanations_checked",
            "missing_or_contradictory_evidence",
            "review_minutes_measured",
            "reviewer_identity_and_independence_attestation",
        ],
        "cases": cases,
    }


if __name__ == "__main__":
    print(json.dumps(packet(), indent=2, sort_keys=True))
