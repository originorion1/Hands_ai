"""Post-seal paired utility; unavailable to the study selector."""

from __future__ import annotations

import json
import random
from pathlib import Path

from evaluation.native_intelligence_v1.score import score

from .benchmark import METHODS

COHORTS = ("unseen_a", "unseen_b", "unseen_c")
RESAMPLE_SEED = 113507


def _utility(row: dict, issue: bool) -> int:
    candidate = row["status"] == "candidate_review"
    return int(issue and candidate) - 2 * int(not issue and candidate)


def compare(root: Path) -> dict:
    differences = []
    by_cohort = {}
    for name in COHORTS:
        cases_path = root / f"{name}_cases.json"
        predictions_path = root / f"{name}_predictions.json"
        labels_path = root / f"{name}_labels.json"
        verified = score(predictions_path, cases_path, labels_path, methods=METHODS)
        predictions = json.loads(predictions_path.read_bytes())
        labels = json.loads(labels_path.read_bytes())["labels"]
        rows = {(row["case_id"], row["method"]): row
                for row in predictions["predictions"]}
        cohort_differences = []
        for case_id, label in sorted(labels.items()):
            cohort_differences.append(
                _utility(rows[(case_id, "adaptive_hybrid")], label["issue"])
                - _utility(rows[(case_id, "heuristic")], label["issue"]),
            )
        differences.extend(cohort_differences)
        by_cohort[name] = {
            "utility_difference": sum(cohort_differences),
            "hybrid_wins": sum(value > 0 for value in cohort_differences),
            "heuristic_wins": sum(value < 0 for value in cohort_differences),
            "ties": sum(value == 0 for value in cohort_differences),
            "verified_predictions_sha256": verified["predictions_sha256"],
        }
    rng = random.Random(RESAMPLE_SEED)
    means = sorted(sum(rng.choices(differences, k=len(differences))) / len(differences)
                   for _ in range(10_000))
    scores = [score(root / f"{name}_predictions.json", root / f"{name}_cases.json",
                    root / f"{name}_labels.json", methods=METHODS)
              for name in COHORTS]
    extra_fp = sum(item["methods"]["adaptive_hybrid"]["fp"]
                   - item["methods"]["heuristic"]["fp"] for item in scores)
    passed = (means[249] > 0 and extra_fp <= 0 and
              sum(row["utility_difference"] > 0 for row in by_cohort.values()) >= 2)
    return {
        "schema": "orion.adaptive-paired-analysis.v1",
        "predeclared_utility": "TP - 2*FP", "organizations": len(differences),
        "paired_utility_difference_total": sum(differences),
        "paired_utility_difference_mean": sum(differences) / len(differences),
        "paired_bootstrap_95": [means[249], means[9749]],
        "hybrid_extra_false_positives": extra_fp,
        "preregistered_synthetic_improvement": passed,
        "independent_outcome_validation": False,
        "cohorts": by_cohort,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(compare(args.root), sort_keys=True,
                                      separators=(",", ":")) + "\n")
