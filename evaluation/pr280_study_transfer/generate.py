"""Generate a sealed, synthetic transfer set without reading the old benchmark.

The structural graph and outcome location use independent seeded streams. More
references to a target therefore do not imply that it contains an anomaly.
"""

from __future__ import annotations

import json
import random
from pathlib import Path

DOMAINS = ("utilities", "logistics", "facilities", "subscriptions")
TARGETS = (
    "Alpha Register", "Beta Ledger", "Gamma Schedule", "Delta Notice",
)
GRAPH_SEED = 732815
OUTCOME_SEED = 991247


def generate() -> tuple[dict, dict]:
    graph_rng = random.Random(GRAPH_SEED)
    outcome_rng = random.Random(OUTCOME_SEED)
    cases = []
    labels = {}
    for domain in DOMAINS:
        for number in range(1, 9):
            case_id = f"{domain[:3].upper()}{number:02d}"
            targets = list(TARGETS)
            graph_rng.shuffle(targets)
            weights = [graph_rng.randint(1, 3) for _ in targets]
            fields = [
                {
                    "fieldname": f"reference_{index}_{copy}",
                    "fieldtype": "Table" if (index + copy) % 3 == 0 else "Link",
                    "options": target,
                }
                for index, (target, weight) in enumerate(zip(targets, weights, strict=True))
                for copy in range(weight)
            ]
            graph_rng.shuffle(fields)
            positive = outcome_rng.choice(targets) if number % 4 != 0 else None
            records = {}
            for index, target in enumerate(targets):
                is_positive = target == positive
                records[target] = {
                    "record_ref": f"{case_id}-R{index + 1}",
                    "source_period": "2026-01-01/2026-03-01",
                    "observed_unit_cost": "17.00" if is_positive else "10.00",
                    "reference_unit_cost": None if positive is None and index == 0 else "10.00",
                    "quantity": str(number + 5),
                    "currency": "USD",
                    "unit": "each",
                    "approved_exception": False,
                }
            cases.append({
                "case_id": case_id,
                "domain": domain,
                "tenant_id": f"synthetic-{case_id.lower()}",
                "source_id": f"synthetic-source-{case_id.lower()}",
                "seed_doctype": f"{domain.title()} Index",
                "fields": fields,
                "allowed_targets": targets,
                "records": records,
            })
            labels[case_id] = {
                "label_origin": "designed_synthetic_independent_stream",
                "positive_target": positive,
            }
    return (
        {"schema": "orion.study-selection-cases.v1", "cases": cases},
        {"schema": "orion.study-selection-labels.v1", "labels": labels},
    )


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    cases, labels = generate()
    for name, value in (("cases.json", cases), ("labels.json", labels)):
        (root / name).write_text(json.dumps(value, indent=2, sort_keys=True) + "\n")
