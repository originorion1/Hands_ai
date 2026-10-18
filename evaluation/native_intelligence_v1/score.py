"""Post-seal scoring with source-reference and equal-access verification."""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from math import sqrt
from pathlib import Path

from .benchmark import AT, METHODS, _digest, _record_id, _report_digest, _run_id


def _wilson(successes: int, total: int) -> list[float] | None:
    if total == 0:
        return None
    z = 1.96
    p = successes / total
    scale = 1 + z * z / total
    midpoint = (p + z * z / (2 * total)) / scale
    radius = z * sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / scale
    return [round(midpoint - radius, 3), round(midpoint + radius, 3)]


def score(predictions_path: Path, cases_path: Path, labels_path: Path, *,
          methods: tuple[str, ...] = METHODS) -> dict:
    predictions_bytes = predictions_path.read_bytes()
    cases_bytes = cases_path.read_bytes()
    labels_bytes = labels_path.read_bytes()
    sealed = json.loads(predictions_bytes)
    data = json.loads(cases_bytes)
    labels = json.loads(labels_bytes)
    if (sealed.get("schema") != "orion.native-lab-predictions.v1"
            or sealed.get("labels_available_to_selector") is not False
            or sealed.get("cases_sha256") != hashlib.sha256(cases_bytes).hexdigest()
            or sealed.get("split") != data.get("split")
            or labels.get("split") != data.get("split")
            or tuple(sealed.get("method_order", ())) != methods):
        raise ValueError("unsealed or inconsistent evaluation inputs")
    cases = {case["case_id"]: case for case in data["cases"]}
    if set(labels["labels"]) != set(cases):
        raise ValueError("labels do not cover cases")
    grouped = defaultdict(list)
    for row in sealed["predictions"]:
        grouped[row["method"]].append(row)
    if set(grouped) != set(methods):
        raise ValueError("method set is incomplete")
    results = {}
    for method, rows in grouped.items():
        if len(rows) != len(cases) or {row["case_id"] for row in rows} != set(cases):
            raise ValueError("missing or duplicate prediction")
        tp = fp = fn = tn = correct_abstain = 0
        entropy_gain = compute_ns = evidence_cost = requests = 0
        brier = 0.0
        domains = defaultdict(lambda: {"cases": 0, "tp": 0, "fp": 0, "fn": 0})
        for row in rows:
            case = cases[row["case_id"]]
            issue = labels["labels"][row["case_id"]]["issue"]
            if type(issue) is not bool:
                raise ValueError("outcome label must be boolean")
            if (row["tenant_id"] != case["tenant_id"]
                    or row["source_id"] != case["source_id"]
                    or row["request_count"] != 2
                    or len(row["acquisitions"]) != 2
                    or row["read_only"] is not True
                    or row["execution_allowed"] is not False
                    or row["run_id"] != _run_id(case["case_id"], method)
                    or row["report_sha256"] != _report_digest(
                        case["case_id"], method, row["status"],
                        row["posterior_issue_probability"], row["acquisitions"],
                    )):
                raise ValueError("scope or request-budget contract failed")
            selected = set()
            question_by_id = {question["question_id"]: question
                              for question in case["questions"]}
            for item in row["acquisitions"]:
                question = question_by_id.get(item["question_id"])
                if (question is None or not question["visible"] or not question["permitted"]
                        or item["question_id"] in selected
                        or item["target"] != question["target"]
                        or item["tenant_id"] != case["tenant_id"]
                        or item["source_id"] != case["source_id"]):
                    raise ValueError("unauthorized or repeated acquisition")
                selected.add(item["question_id"])
                record = case["records"][question["target"]]
                if (item["source_record_ref"] != record["record"]["record_ref"]
                        or item["answer"] != record["record"]["answer"]
                        or item["source_record_sha256"] != record["sha256"]
                        or _digest(record["record"]) != record["sha256"]
                        or item["evidence_id"] != str(_record_id(case["case_id"], question["target"]))
                        or item["acquired_at"] != AT.isoformat()):
                    raise ValueError("source-to-conclusion evidence mismatch")
            status = row["status"]
            if status not in {"candidate_review", "no_anomaly", "abstained"}:
                raise ValueError("invalid conclusion status")
            domains[row["domain"]]["cases"] += 1
            if status == "candidate_review" and issue:
                tp += 1
                domains[row["domain"]]["tp"] += 1
            elif status == "candidate_review":
                fp += 1
                domains[row["domain"]]["fp"] += 1
            elif issue:
                fn += 1
                domains[row["domain"]]["fn"] += 1
            else:
                tn += 1
            if status == "abstained" and (row["contradiction"] or
                                           all(item["answer"] == "missing"
                                               for item in row["acquisitions"])):
                correct_abstain += 1
            probability = row["posterior_issue_probability"]
            if not isinstance(probability, (int, float)) or not 0 <= probability <= 1:
                raise ValueError("invalid posterior probability")
            brier += (probability - int(issue)) ** 2
            entropy_gain += row["realized_information_gain_bits"]
            compute_ns += row["compute_ns"]
            evidence_cost += row["evidence_cost"]
            requests += row["request_count"]
        results[method] = {
            "designed_tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "precision_wilson_95": _wilson(tp, tp + fp),
            "recall": tp / (tp + fn) if tp + fn else None,
            "recall_wilson_95": _wilson(tp, tp + fn),
            "false_positive_rate": fp / (fp + tn) if fp + tn else None,
            "correct_abstentions": correct_abstain,
            "request_count": requests, "evidence_cost": evidence_cost,
            "realized_information_gain_bits_total": entropy_gain,
            "information_gain_bits_per_request": entropy_gain / requests,
            "brier_score": brier / len(rows),
            "compute_ms_total": compute_ns / 1e6,
            "analyst_review_minutes_measured": None,
            "independently_validated_discoveries": None,
            "net_economic_value_verified": None,
            "provenance_completeness": 1.0,
            "by_domain": dict(sorted(domains.items())),
        }
    return {
        "schema": "orion.native-lab-score.v1", "split": data["split"],
        "case_count": len(cases), "domain_count": len({case["domain"] for case in cases.values()}),
        "designed_positive_count": sum(label["issue"] for label in labels["labels"].values()),
        "label_origin": "designed synthetic; no independent reviewer",
        "same_authorization_and_two_read_budget": True,
        "cases_sha256": hashlib.sha256(cases_bytes).hexdigest(),
        "labels_sha256": hashlib.sha256(labels_bytes).hexdigest(),
        "predictions_sha256": hashlib.sha256(predictions_bytes).hexdigest(),
        "methods": results,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(
        score(args.predictions, args.cases, args.labels), sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ) + "\n")
