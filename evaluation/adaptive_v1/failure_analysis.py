"""Post-seal diagnostic only. Never imported by a study-selection policy."""

from __future__ import annotations

import hashlib
import itertools
import json
from pathlib import Path

from evaluation.native_intelligence_v1.score import score


def _posterior(prior: tuple[float, float], likelihoods: list[list[float]],
               answer: str) -> tuple[float, float]:
    index = ("flag", "clear", "missing").index(answer)
    joint = (prior[0] * likelihoods[0][index], prior[1] * likelihoods[1][index])
    total = sum(joint)
    if total <= 0:
        raise ValueError("zero-likelihood answer")
    return joint[0] / total, joint[1] / total


def _pair_status(case: dict, calibration: dict, pair: tuple[dict, dict]) -> str:
    belief = tuple(calibration["prior"])
    shifts = []
    for question in pair:
        before = belief[1]
        answer = case["records"][question["target"]]["record"]["answer"]
        belief = _posterior(belief, calibration["features"][question["feature"]]["likelihoods"],
                            answer)
        shifts.append(belief[1] - before)
    contradiction = any(value > 1e-9 for value in shifts) and any(value < -1e-9
                                                                   for value in shifts)
    if contradiction or 0.2 < belief[1] < 0.8:
        return "abstained"
    return "candidate_review" if belief[1] >= 0.8 else "no_anomaly"


def analyze(cases_path: Path, labels_path: Path, predictions_path: Path,
            calibration_path: Path) -> dict:
    # Recheck every source reference and report digest before diagnosing misses.
    score(predictions_path, cases_path, labels_path)
    source = predictions_path.read_bytes()
    predictions = json.loads(source)
    cases = json.loads(cases_path.read_bytes())
    labels = json.loads(labels_path.read_bytes())
    calibration = json.loads(calibration_path.read_bytes())
    if (cases["split"] != labels["split"] or cases["split"] != predictions["split"]
            or predictions["labels_available_to_selector"] is not False):
        raise ValueError("split or sealing mismatch")
    by_method = {(row["case_id"], row["method"]): row for row in predictions["predictions"]}
    details = []
    all_method_failures = []
    for case in cases["cases"]:
        case_id = case["case_id"]
        issue = labels["labels"][case_id]["issue"]
        enhanced = by_method[case_id, "enhanced"]
        heuristic = by_method[case_id, "heuristic"]
        allowed = tuple(q for q in case["questions"] if q["visible"] and q["permitted"])
        pairs = tuple(pair for pair in itertools.combinations(allowed, 2)
                      if _pair_status(case, calibration, pair) == "candidate_review")
        for method in ("existing", "enhanced", "heuristic"):
            row = by_method[case_id, method]
            if (issue and row["status"] == "candidate_review"
                    or not issue and row["status"] != "candidate_review"):
                continue
            reason = ("misleading_evidence_or_calibration_shift" if not issue else
                      "insufficient_two_read_evidence" if not pairs else
                      "prioritization_under_two_read_budget")
            all_method_failures.append({
                "case_id": case_id, "method": method, "classification": reason,
                "designed_issue": issue, "status": row["status"],
                "posterior": row["posterior_issue_probability"],
                "qualifying_pair_count_posthoc": len(pairs),
                "source_refs": [item["source_record_ref"] for item in row["acquisitions"]],
                "source_digests": [item["source_record_sha256"]
                                   for item in row["acquisitions"]],
            })
        if (issue and enhanced["status"] == "candidate_review"
                or not issue and enhanced["status"] != "candidate_review"):
            continue
        if not issue:
            reason = "misleading_evidence_or_calibration_shift"
        elif not pairs:
            reason = "insufficient_two_read_evidence"
        else:
            reason = "prioritization_under_two_read_budget"
        details.append({
            "case_id": case_id, "domain": case["domain"], "designed_issue": issue,
            "classification": reason, "authorized_target_count": len(allowed),
            "qualifying_pair_count_posthoc": len(pairs),
            "enhanced_status": enhanced["status"],
            "enhanced_posterior": enhanced["posterior_issue_probability"],
            "enhanced_contradiction": enhanced["contradiction"],
            "enhanced_reads": [
                {key: acquisition[key] for key in
                 ("feature", "answer", "source_record_ref", "source_record_sha256")}
                for acquisition in enhanced["acquisitions"]
            ],
            "heuristic_status": heuristic["status"],
            "heuristic_same_reads": [item["question_id"] for item in
                                     heuristic["acquisitions"]] == [
                                         item["question_id"] for item in enhanced["acquisitions"]
                                     ],
        })
    return {
        "schema": "orion.adaptive-failure-analysis.v1", "split": cases["split"],
        "predictions_sha256": hashlib.sha256(source).hexdigest(),
        "label_origin": "designed synthetic, not independently adjudicated",
        "posthoc_oracle_visible_to_policy": False,
        "details": details,
        "all_method_failures": all_method_failures,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(analyze(args.cases, args.labels,
                                              args.predictions, args.calibration),
                                      sort_keys=True, separators=(",", ":")) + "\n")
