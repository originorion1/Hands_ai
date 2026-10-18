"""Evaluator-only synthetic organizations; never imported by a selector.

Training outcomes are visible for calibration. Development, holdout and
transfer labels are written separately from the cases presented to selectors.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

FEATURES = ("F0", "F1", "F2", "F3", "F4")
RATES = {
    "F0": ((0.15, 0.85, 0.0), (0.85, 0.15, 0.0)),
    "F1": ((0.30, 0.70, 0.0), (0.70, 0.30, 0.0)),
    "F2": ((0.50, 0.50, 0.0), (0.50, 0.50, 0.0)),
    "F3": ((0.80, 0.20, 0.0), (0.20, 0.80, 0.0)),
    "F4": ((0.28, 0.42, 0.30), (0.42, 0.28, 0.30)),
}
TRANSFER_RATES = {
    **RATES,
    "F0": ((0.40, 0.60, 0.0), (0.60, 0.40, 0.0)),
    "F3": ((0.55, 0.45, 0.0), (0.45, 0.55, 0.0)),
}
SPLITS = {
    "training": (14399, ("fleet", "facilities", "billing"), 96),
    "development": (68321, ("fleet", "facilities", "billing"), 24),
    "holdout": (39017, ("mobility", "utilities", "service"), 72),
    "transfer": (85243, ("manufacturing", "energy", "distribution"), 72),
}


def _draw(rng: random.Random, probabilities: tuple[float, ...]) -> str:
    threshold = rng.random()
    for index, value in enumerate(probabilities):
        threshold -= value
        if threshold < 0:
            return ("flag", "clear", "missing")[index]
    return "missing"


def generate_split(split: str) -> tuple[dict, dict]:
    """Generate one organization split without importing policy code."""
    seed, domains, count = SPLITS[split]
    graph = random.Random(seed)
    outcomes = random.Random(seed ^ 0x74A3F)
    rates = TRANSFER_RATES if split == "transfer" else RATES
    cases = []
    labels = {}
    for index in range(count):
        case_id = f"{split.upper()}-{index:03d}"
        domain = domains[index % len(domains)]
        issue = outcomes.random() < 0.4
        feature_order = list(FEATURES)
        graph.shuffle(feature_order)
        questions = []
        records = {}
        for position, feature in enumerate(feature_order):
            target = f"{domain.title()} {case_id} Node {graph.randrange(1000, 9999)}"
            count_edges = graph.randint(1, 4)
            visible = not (index % 7 == 0 and position == 4)
            permitted = visible and not (index % 5 == 0 and position == 3)
            answer = _draw(outcomes, rates[feature][int(issue)])
            ref = f"{case_id}-R{position + 1}"
            record = {"record_ref": ref, "answer": answer,
                      "source_period": "2026-01-01/2026-03-01",
                      "available_after_period": feature == "F4" and answer == "missing"}
            digest = hashlib.sha256(json.dumps(record, sort_keys=True,
                                                separators=(",", ":")).encode()).hexdigest()
            records[target] = {"record": record, "sha256": digest}
            questions.append({
                "question_id": f"{case_id}-Q{position + 1}", "target": target,
                "feature": feature, "relationship_count": count_edges,
                "fieldtype": "Table" if position % 2 else "Link",
                "visible": visible, "permitted": permitted,
                "request_cost": 1.0, "privacy_risk": 0.1 if feature == "F4" else 0.0,
                "operational_risk": 0.0, "relevance": 1.0,
            })
        cases.append({
            "case_id": case_id, "domain": domain,
            "tenant_id": f"synthetic-{case_id.lower()}",
            "source_id": f"synthetic-{domain}-{case_id.lower()}",
            "seed_doctype": f"{domain.title()} {case_id} Index",
            "questions": questions, "records": records,
        })
        labels[case_id] = {
            "issue": issue, "label_origin": "designed_synthetic",
            "reviewed": False,
        }
    return (
        {"schema": "orion.native-lab-cases.v1", "split": split,
         "cases": cases},
        {"schema": "orion.native-lab-labels.v1", "split": split,
         "labels": labels},
    )


if __name__ == "__main__":
    root = Path(__file__).resolve().parent
    for name in SPLITS:
        cases, labels = generate_split(name)
        for suffix, value in (("cases", cases), ("labels", labels)):
            (root / f"{name}_{suffix}.json").write_text(json.dumps(
                value, sort_keys=True, separators=(",", ":"), allow_nan=False,
            ) + "\n")
