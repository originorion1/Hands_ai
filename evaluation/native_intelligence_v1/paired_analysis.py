"""Post-seal paired uncertainty analysis; never imported by selection code."""

from __future__ import annotations

import hashlib
import json
import math
import random
from pathlib import Path


def _interval(values: list[float]) -> list[float]:
    values.sort()
    return [round(values[int(0.025 * len(values))], 4),
            round(values[int(0.975 * len(values)) - 1], 4)]


def compare(predictions_path: Path, labels_path: Path, baseline: str) -> dict:
    source = predictions_path.read_bytes()
    sealed = json.loads(source)
    labels = json.loads(labels_path.read_bytes())["labels"]
    by_method = {}
    for row in sealed["predictions"]:
        by_method.setdefault(row["method"], {})[row["case_id"]] = row
    if baseline not in by_method or "enhanced" not in by_method:
        raise ValueError("comparison method absent")
    cases = sorted(labels)
    if set(by_method[baseline]) != set(cases) or set(by_method["enhanced"]) != set(cases):
        raise ValueError("paired cases differ")
    deltas = {"brier": [], "information_gain": [], "designed_true_positives": []}
    wins = losses = 0
    for case_id in cases:
        actual = int(labels[case_id]["issue"])
        enhanced = by_method["enhanced"][case_id]
        control = by_method[baseline][case_id]
        e_hit = int(actual and enhanced["status"] == "candidate_review")
        c_hit = int(actual and control["status"] == "candidate_review")
        wins += int(e_hit > c_hit)
        losses += int(e_hit < c_hit)
        deltas["brier"].append(
            (control["posterior_issue_probability"] - actual) ** 2
            - (enhanced["posterior_issue_probability"] - actual) ** 2
        )
        deltas["information_gain"].append(
            enhanced["realized_information_gain_bits"]
            - control["realized_information_gain_bits"]
        )
        deltas["designed_true_positives"].append(e_hit - c_hit)
    rng = random.Random(81293)
    intervals = {}
    for metric, values in deltas.items():
        samples = []
        for _ in range(5000):
            samples.append(sum(values[rng.randrange(len(values))]
                               for _ in values) / len(values))
        intervals[metric] = _interval(samples)
    discordant = wins + losses
    p_value = (min(1.0, 2 * sum(math.comb(discordant, index)
                                for index in range(min(wins, losses) + 1))
                   / (2 ** discordant)) if discordant else 1.0)
    return {
        "schema": "orion.native-lab-paired-analysis.v1",
        "split": sealed["split"], "baseline": baseline, "case_count": len(cases),
        "predictions_sha256": hashlib.sha256(source).hexdigest(),
        "enhanced_only_designed_hits": wins, "baseline_only_designed_hits": losses,
        "discordant_exact_two_sided_p": p_value,
        "mean_differences": {key: sum(value) / len(value) for key, value in deltas.items()},
        "paired_bootstrap_95": intervals,
        "uncertainty_scope": "designed synthetic cases; bootstrap does not cover generator shift",
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser()
    parser.add_argument("--predictions", type=Path, required=True)
    parser.add_argument("--labels", type=Path, required=True)
    parser.add_argument("--baseline", choices=("existing", "heuristic", "fixed", "random"),
                        required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.write_text(json.dumps(
        compare(args.predictions, args.labels, args.baseline), sort_keys=True,
        separators=(",", ":"), allow_nan=False,
    ) + "\n")
