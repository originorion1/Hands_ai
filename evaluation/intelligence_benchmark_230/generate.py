"""Generate a fixed synthetic benchmark; labels are frozen separately from detector input."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).parent
TENANTS = ("synthetic-hospitality-a", "synthetic-distributor-b")
CATEGORIES = ("produce", "protein", "dry-goods")
LABELS = {
    (0, 1, 0, 1): "price_variance",
    (1, 2, 2, 2): "price_variance",
    (0, 0, 1, 3): "duplicate_invoice",
    (1, 1, 0, 2): "incorrect_invoice",
    (0, 2, 1, 1): "inventory_mismatch",
    (1, 2, 0, 3): "margin_deterioration",
    (0, 0, 2, 1): "missing_document",
    (1, 1, 2, 0): "contradictory_receipt",
}


def generate() -> tuple[dict, dict]:
    events = []
    labels = []
    for t, tenant in enumerate(TENANTS):
        for p in range(3):
            for c, category in enumerate(CATEGORIES):
                for i in range(4):
                    identity = f"t{t}-p{p}-c{c}-r{i}"
                    pack_size = 2 if (t, p, c, i) == (0, 2, 2, 0) else 1
                    contracted = 8 + 2 * c + t
                    unit_cost = contracted * pack_size
                    quantity = 10 + i + p
                    sold = 5 + i + (p if t == 0 else p % 2)
                    event = {
                        "id": identity, "tenant": tenant, "period": p,
                        "category": category, "supplier": f"supplier-{c}",
                        "invoice": f"inv-{p}-{c}-{i}", "quantity": quantity,
                        "pack_size": pack_size, "unit_cost_per_pack": unit_cost,
                        "contract_unit_cost": contracted,
                        "billed_amount": quantity * unit_cost / pack_size,
                        "received_qty": quantity, "witness_received_qty": quantity,
                        "opening_stock": 20 + i, "sold_qty": sold,
                        "closing_stock": 20 + i + quantity - sold,
                        "prior_margin": 0.4, "current_margin": 0.4,
                        "promotion_approved": False, "report_doc": "synthetic-ref",
                        "evidence_id": hashlib.sha256(identity.encode()).hexdigest(),
                    }
                    kind = LABELS.get((t, p, c, i))
                    if kind == "price_variance":
                        event["unit_cost_per_pack"] = round(unit_cost * 1.3, 2)
                        event["billed_amount"] = round(quantity * event["unit_cost_per_pack"] / pack_size, 2)
                    elif kind == "duplicate_invoice":
                        event["invoice"] = f"inv-{p}-{c}-2"
                    elif kind == "incorrect_invoice":
                        event["billed_amount"] += 35
                    elif kind == "inventory_mismatch":
                        event["closing_stock"] -= 6
                    elif kind == "margin_deterioration":
                        event["current_margin"] = 0.2
                    elif kind == "missing_document":
                        event["report_doc"] = None
                    elif kind == "contradictory_receipt":
                        event["witness_received_qty"] += 5
                    if (t, p, c, i) == (0, 2, 0, 3):
                        event["current_margin"] = 0.2
                        event["promotion_approved"] = True
                    if (t, p, c, i) == (1, 2, 1, 2):
                        event["prior_version"] = {"closing_stock": 0, "superseded": True}
                    if kind:
                        labels.append({"id": identity, "tenant": tenant, "period": p,
                                       "kind": kind, "review": "synthetic_design_label"})
                    events.append(event)
    return (
        {"schema": "orion.synthetic-procurement.v1", "events": events},
        {"schema": "orion.synthetic-ground-truth.v1", "labels": labels},
    )


def main() -> None:
    events, labels = generate()
    (ROOT / "events.json").write_text(json.dumps(events, indent=2, sort_keys=True) + "\n")
    (ROOT / "ground_truth.json").write_text(json.dumps(labels, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
