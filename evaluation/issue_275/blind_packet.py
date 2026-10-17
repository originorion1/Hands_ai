"""Export source-only synthetic cases for a reviewer blinded to predictions."""

from __future__ import annotations

import hashlib
import json

from evaluation.procurement_price_270.fixture import CASES, rows


def packet() -> dict[str, object]:
    cases = []
    for case in sorted(CASES):
        source = rows(case)
        source_body = json.dumps(source, sort_keys=True, separators=(",", ":")).encode()
        cases.append({
            "case_id": case,
            "source_sha256": hashlib.sha256(source_body).hexdigest(),
            "source_rows": source,
            "review_question": (
                "Is the target purchase an avoidable price difference after checking "
                "unit, tax, discount, currency, contract, supplier, quality, "
                "delivery and conflicting records? If evidence is insufficient, abstain."
            ),
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
