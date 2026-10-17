"""Reproduce the small synthetic request-ordering fixture byte for byte."""

from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).parent
REQUESTS = ("invoice_detail", "stock_witness", "document_status")


def case(identifier, tenant, period, symptom, needed, *, authorized=True,
         allowed=REQUESTS, budget=2, priority="document_status"):
    reduction = {request: (9 if request == priority else 1) for request in REQUESTS}
    return {
        "id": identifier, "tenant": tenant, "period": period, "symptom": symptom,
        "authorized": authorized, "allowed_requests": list(allowed),
        "max_requests": budget, "expected_reduction": reduction,
        "potential_value": 1, "request_cost": dict.fromkeys(REQUESTS, 1),
        "privacy_risk": dict.fromkeys(REQUESTS, 0),
    }, {"period": period + 1, "needed": needed}


def generate() -> tuple[dict, dict]:
    designs = (
        case("missing-a", "synthetic-hospitality-a", 0, "missing_document",
             "document_status", priority="document_status"),
        case("contradiction-a", "synthetic-hospitality-a", 0, "contradictory_receipt",
             "stock_witness", priority="stock_witness"),
        case("price-b", "synthetic-distributor-b", 0, "price_variance",
             "invoice_detail", priority="invoice_detail"),
        case("stale-b", "synthetic-distributor-b", 1, "stale_document",
             "document_status", priority="document_status"),
        case("no-evidence-a", "synthetic-hospitality-a", 1, "ambiguous", None,
             priority="invoice_detail"),
        # Designed counterexample: a fixed invoice-first sequence wins here.
        case("fixed-wins-b", "synthetic-distributor-b", 1, "missing_document",
             "invoice_detail", priority="document_status"),
        case("scoped-stock-b", "synthetic-distributor-b", 2, "contradictory_receipt",
             "stock_witness", allowed=("stock_witness",), budget=1,
             priority="stock_witness"),
        case("revoked-a", "synthetic-hospitality-a", 2, "missing_document", None,
             authorized=False, budget=0),
    )
    cases = [item[0] for item in designs]
    outcomes = {item[0]["id"]: item[1] for item in designs}
    return {"schema": "orion.synthetic-active-cases.v1", "cases": cases}, {
        "schema": "orion.synthetic-active-outcomes.v1", "outcomes": outcomes,
    }


if __name__ == "__main__":
    cases, outcomes = generate()
    for name, document in (("active_cases.json", cases), ("active_outcomes.json", outcomes)):
        (ROOT / name).write_text(json.dumps(document, indent=2, sort_keys=True) + "\n")
