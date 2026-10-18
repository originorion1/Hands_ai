"""Score sealed predictions against separately held synthetic labels."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from math import sqrt
from pathlib import Path


def _wilson(successes: int, total: int) -> list[float] | None:
    if total == 0:
        return None
    z = 1.96
    p = successes / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    radius = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [round(center - radius, 3), round(center + radius, 3)]


def score(predictions_path: Path, labels_path: Path, cases_path: Path) -> dict:
    sealed = json.loads(predictions_path.read_bytes())
    labels_bytes = labels_path.read_bytes()
    labels = json.loads(labels_bytes)
    if (sealed.get("schema") != "orion.study-selection-predictions.v1"
            or labels.get("schema") != "orion.study-selection-labels.v1"
            or sealed.get("cases_sha256") != hashlib.sha256(cases_path.read_bytes()).hexdigest()
            or sealed.get("labels_available_to_selector") is not False):
        raise ValueError("unsealed or inconsistent benchmark input")
    truth = labels["labels"]
    case_ids = {row["case_id"] for row in json.loads(cases_path.read_bytes())["cases"]}
    if set(truth) != case_ids:
        raise ValueError("labels do not cover the evaluation cases")
    grouped: dict[str, list[dict]] = defaultdict(list)
    for row in sealed["predictions"]:
        grouped[row["method"]].append(row)
    if set(grouped) != set(sealed["method_order"]):
        raise ValueError("benchmark methods changed")
    scores = {}
    for method, rows in grouped.items():
        if len(rows) != len(case_ids) or {row["case_id"] for row in rows} != case_ids:
            raise ValueError("missing or duplicate case prediction")
        tp = fp = fn = abstentions = selected_positive = 0
        by_domain = defaultdict(lambda: {"cases": 0, "positive_selected": 0})
        for row in rows:
            label = truth[row["case_id"]]
            target = label["positive_target"]
            conclusion = row["conclusion"]
            status = conclusion["status"]
            chosen = row["selected_target"]
            if (row["request_count"] != 2 or row["requests"] != [
                ["metadata", chosen], ["record", chosen],
            ] or row["read_only"] is not True
                    or row["execution_allowed"] is not False
                    or not row["structural_evidence_id"]
                    or len(row["structural_sha256"]) != 64
                    or not conclusion["evidence_id"]
                    or len(conclusion["record_sha256"]) != 64):
                raise ValueError("request or provenance contract failed")
            by_domain[row["domain"]]["cases"] += 1
            if chosen == target and target is not None:
                selected_positive += 1
                by_domain[row["domain"]]["positive_selected"] += 1
            if status == "candidate_review":
                if chosen == target and target is not None:
                    tp += 1
                else:
                    fp += 1
            elif status == "abstained":
                abstentions += 1
            elif status != "no_anomaly":
                raise ValueError("unrecognized conclusion")
            if target is not None and not (status == "candidate_review" and chosen == target):
                fn += 1
        predicted_positive = tp + fp
        actual_positive = tp + fn
        scores[method] = {
            "tp": tp, "fp": fp, "fn": fn, "abstentions": abstentions,
            "positive_target_selected": selected_positive,
            "precision": tp / predicted_positive if predicted_positive else None,
            "precision_wilson_95": _wilson(tp, predicted_positive),
            "recall": tp / actual_positive if actual_positive else None,
            "recall_wilson_95": _wilson(tp, actual_positive),
            "evidence_requests": sum(row["request_count"] for row in rows),
            "candidate_review_count": predicted_positive,
            "analyst_minutes_measured": None,
            "economic_value_verified_usd": None,
            "by_domain": dict(sorted(by_domain.items())),
        }
    return {
        "schema": "orion.study-selection-score.v1",
        "case_count": len(case_ids), "domain_count": len({
            row["domain"] for row in sealed["predictions"]
        }),
        "designed_positive_count": sum(
            label["positive_target"] is not None for label in truth.values()
        ),
        "label_origin": "designed synthetic; no independent adjudication",
        "labels_sha256": hashlib.sha256(labels_bytes).hexdigest(),
        "predictions_sha256": hashlib.sha256(predictions_path.read_bytes()).hexdigest(),
        "same_evidence_access": True,
        "scores": scores,
        "incremental_value_verified": False,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--labels", type=Path, default=Path(__file__).with_name("labels.json"))
    parser.add_argument("--cases", type=Path, default=Path(__file__).with_name("cases.json"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(
        score(args.predictions, args.labels, args.cases), indent=2, sort_keys=True,
    ) + "\n")
