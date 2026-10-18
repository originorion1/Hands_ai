"""Equal-access comparison; evaluation labels are not imported by selectors."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from uuid import UUID

from evaluation.native_intelligence_v1.benchmark import calibrate, run_case
from orion.learning.adaptive_study import rank_adaptive_questions

METHODS = ("existing", "enhanced", "heuristic", "adaptive_hybrid")


def _choose_hybrid(method, candidates, case, belief, calibration,
                   understanding, authorization):
    if method != "adaptive_hybrid":
        raise ValueError("hybrid chooser requires its own method identity")
    verifier = {UUID(value) for feature in calibration["features"].values()
                for value in feature["evidence_ids"]}
    ranked = rank_adaptive_questions(
        belief, candidates, understanding=understanding, authorization=authorization,
        calibration_verifier=lambda identity: identity in verifier,
        max_request_cost=1, max_privacy_risk=0.2, max_operational_risk=0.2,
    )
    if not ranked:
        raise ValueError("no authorized question remains")
    by_id = {question.question_id: question for question in candidates}
    selected = ranked[0]
    return by_id[selected.question_id], selected.rationale


def run_benchmark(cases_path: Path, calibration_path: Path) -> dict:
    source = cases_path.read_bytes()
    cases = json.loads(source)
    calibration = json.loads(calibration_path.read_bytes())
    training = Path(__file__).resolve().parents[1] / "native_intelligence_v1"
    expected = calibrate(training / "training_cases.json", training / "training_labels.json")
    if (cases.get("schema") != "orion.native-lab-cases.v1"
            or cases.get("split") == "training"
            or calibration != expected):
        raise ValueError("evaluation cases or calibration failed integrity check")
    predictions = []
    for case in cases["cases"]:
        for method in METHODS:
            predictions.append(run_case(
                case, calibration, method,
                chooser=_choose_hybrid if method == "adaptive_hybrid" else None,
            ))
    return {
        "schema": "orion.native-lab-predictions.v1", "split": cases["split"],
        "cases_sha256": hashlib.sha256(source).hexdigest(),
        "calibration_sha256": hashlib.sha256(calibration_path.read_bytes()).hexdigest(),
        "method_order": list(METHODS), "labels_available_to_selector": False,
        "strategy_version": "decision-risk-v1; no outcome update",
        "predictions": predictions,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--cases", type=Path, required=True)
    parser.add_argument("--calibration", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(run_benchmark(args.cases, args.calibration),
                                      sort_keys=True, separators=(",", ":")) + "\n")
