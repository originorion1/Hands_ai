"""Evaluator-only, predeclared unseen organizations; never imported by policy.

Three seeded cohorts vary domains, issue prevalence, observation reliability,
and missingness. Labels are designed synthetic outcomes, not adjudications.
"""

from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

FEATURES = ("F0", "F1", "F2", "F3", "F4")
ANSWERS = ("flag", "clear", "missing")
COHORTS = {
    "unseen_a": {
        "seed": 871123, "domains": ("education", "logistics", "hospitality"),
        "count": 72, "prevalence": 0.4,
        "rates": {
            "F0": ((.15, .85, 0), (.85, .15, 0)),
            "F1": ((.30, .70, 0), (.70, .30, 0)),
            "F2": ((.50, .50, 0), (.50, .50, 0)),
            "F3": ((.80, .20, 0), (.20, .80, 0)),
            "F4": ((.28, .42, .30), (.42, .28, .30)),
        },
    },
    "unseen_b": {
        "seed": 442191, "domains": ("agriculture", "maintenance", "publishing"),
        "count": 72, "prevalence": 0.32,
        "rates": {
            "F0": ((.35, .65, 0), (.65, .35, 0)),
            "F1": ((.18, .82, 0), (.82, .18, 0)),
            "F2": ((.50, .50, 0), (.50, .50, 0)),
            "F3": ((.70, .30, 0), (.30, .70, 0)),
            "F4": ((.24, .36, .40), (.36, .24, .40)),
        },
    },
    "unseen_c": {
        "seed": 994703, "domains": ("media", "construction", "research"),
        "count": 72, "prevalence": 0.48,
        "rates": {
            "F0": ((.30, .70, 0), (.70, .30, 0)),
            "F1": ((.40, .60, 0), (.60, .40, 0)),
            "F2": ((.50, .50, 0), (.50, .50, 0)),
            "F3": ((.58, .42, 0), (.42, .58, 0)),
            "F4": ((.20, .30, .50), (.30, .20, .50)),
        },
    },
}


def _draw(rng: random.Random, rates: tuple[float, ...]) -> str:
    value = rng.random()
    for answer, probability in zip(ANSWERS, rates, strict=True):
        value -= probability
        if value < 0:
            return answer
    return ANSWERS[-1]


def generate(cohort: str) -> tuple[dict, dict]:
    config = COHORTS[cohort]
    graph = random.Random(config["seed"])
    outcomes = random.Random(config["seed"] ^ 0x74A3F)
    cases = []
    labels = {}
    for index in range(config["count"]):
        case_id = f"{cohort.upper()}-{index:03d}"
        domain = config["domains"][index % len(config["domains"])]
        issue = outcomes.random() < config["prevalence"]
        order = list(FEATURES)
        graph.shuffle(order)
        questions = []
        records = {}
        for position, feature in enumerate(order):
            target = f"{domain.title()} {case_id} Node {graph.randrange(1000, 9999)}"
            visible = not (index % 7 == 0 and position == 4)
            permitted = visible and not (index % 5 == 0 and position == 3)
            answer = _draw(outcomes, config["rates"][feature][int(issue)])
            record = {
                "record_ref": f"{case_id}-R{position + 1}", "answer": answer,
                "source_period": "2026-04-01/2026-06-01",
                "available_after_period": feature == "F4" and answer == "missing",
            }
            digest = hashlib.sha256(json.dumps(
                record, sort_keys=True, separators=(",", ":"),
            ).encode()).hexdigest()
            records[target] = {"record": record, "sha256": digest}
            questions.append({
                "question_id": f"{case_id}-Q{position + 1}", "target": target,
                "feature": feature, "relationship_count": graph.randint(1, 4),
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
            "issue": issue, "label_origin": "designed_synthetic", "reviewed": False,
        }
    return (
        {"schema": "orion.native-lab-cases.v1", "split": cohort, "cases": cases},
        {"schema": "orion.native-lab-labels.v1", "split": cohort, "labels": labels},
    )


def write_cohort(cohort: str, root: Path) -> None:
    cases, labels = generate(cohort)
    root.mkdir(parents=True, exist_ok=True)
    for suffix, value in (("cases", cases), ("labels", labels)):
        (root / f"{cohort}_{suffix}.json").write_text(json.dumps(
            value, sort_keys=True, separators=(",", ":"), allow_nan=False,
        ) + "\n")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    for name in COHORTS:
        write_cohort(name, args.output_dir)
