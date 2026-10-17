"""Frozen holdout scoring on equal synthetic purchase rows; no labels in detectors."""

from __future__ import annotations

import hashlib
import json
import math
from decimal import Decimal
from pathlib import Path
from statistics import mean, median

from orion.business.procurement import Purchase, investigate_purchases

from .fixture import CASES, rows

ROOT = Path(__file__).parent


def conventional(records: tuple[dict, ...]) -> dict[str, bool]:
    purchases = [Purchase.from_record(row) for row in records]
    target = purchases[-1]
    history = purchases[:-1]
    target_price = target.normalized_unit_usd
    prices = [row.normalized_unit_usd for row in history]
    suppliers = [row.normalized_unit_usd for row in history if row.supplier != target.supplier]
    normalized_deduplicated = {
        row.invoice: row.normalized_unit_usd for row in history
    }
    return {
        "contract_threshold": (target.contract_unit_usd is not None and
                               target_price > target.contract_unit_usd * Decimal("1.15")),
        "historical_mean": target_price > mean(prices) * Decimal("1.15"),
        "historical_median": target_price > median(prices) * Decimal("1.15"),
        "supplier_comparison": bool(suppliers) and
        target_price > min(suppliers) * Decimal("1.15"),
        "duplicate_unit_rule": len(normalized_deduplicated) != len(history) and
        target_price > median(normalized_deduplicated.values()) * Decimal("1.15"),
    }


def _score(predictions: dict[str, bool], positives: set[str]) -> dict[str, object]:
    tp = sum(flag and case in positives for case, flag in predictions.items())
    fp = sum(flag and case not in positives for case, flag in predictions.items())
    fn = sum(not flag and case in positives for case, flag in predictions.items())
    reviewed = tp + fp
    interval = None
    if reviewed:
        p = tp / reviewed
        z = 1.96
        denominator = 1 + z * z / reviewed
        midpoint = (p + z * z / (2 * reviewed)) / denominator
        margin = z * math.sqrt(p * (1 - p) / reviewed + z * z /
                               (4 * reviewed * reviewed)) / denominator
        interval = [round(max(0, midpoint - margin), 3),
                    round(min(1, midpoint + margin), 3)]
    return {
        "tp": tp, "fp": fp, "fn": fn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "precision_wilson_95_interval": interval,
        "recall": tp / (tp + fn) if tp + fn else None,
        "review_minutes_assumed": 6 * (tp + fp),
    }


def evaluate() -> dict[str, object]:
    """Freeze all predictions before opening the separately defined labels."""
    orion = {}
    baselines = {name: {} for name in (
        "contract_threshold", "historical_mean", "historical_median",
        "supplier_comparison", "duplicate_unit_rule",
    )}
    conclusions = {}
    source_digests = {}
    for case in sorted(CASES):
        source = rows(case)
        source_bytes = json.dumps(source, sort_keys=True, separators=(",", ":")).encode()
        source_digests[case] = hashlib.sha256(source_bytes).hexdigest()
        result = investigate_purchases(source, target_ref=f"{case}-T",
                                       company="Synthetic Procurement Co")
        conclusions[case] = result
        orion[case] = result["status"] == "candidate_review"
        for name, flag in conventional(source).items():
            baselines[name][case] = flag
    union = {case: any(rule[case] for rule in baselines.values()) for case in CASES}
    labels_raw = (ROOT / "ground_truth.json").read_bytes()
    label_document = json.loads(labels_raw)
    positives = set(label_document["positive_cases"])
    assert positives <= set(CASES)
    scores = {name: _score(prediction, positives)
              for name, prediction in baselines.items()}
    scores["conventional_union"] = _score(union, positives)
    scores["orion"] = _score(orion, positives)
    exposure = sum(Decimal(conclusions[case]["estimated_exposure_usd"])
                   for case in CASES if orion[case])
    return {
        "schema": "orion.procurement-price-holdout.v1",
        "source_sha256_by_case": source_digests,
        "labels_sha256": hashlib.sha256(labels_raw).hexdigest(),
        "sample_cases": len(CASES), "designed_positive_cases": len(positives),
        "scores": scores,
        "synthetic_labeled_incremental_discoveries": sum(
            orion[case] and not union[case] and case in positives for case in CASES
        ),
        "independently_adjudicated_incremental_discoveries": None,
        "precision_target_80_representative_sample": False,
        "evidence_coverage": "48/48 synthetic source rows",
        "provenance_completeness": "benchmark rows digest-bound; operational custody separately tested",
        "detection_latency": "one offline evaluation pass after target period; no live latency measured",
        "evidence_requests": len(CASES),
        "evidence_request_cost_usd_assumed": "0.012",
        "investigation_compute_cost_usd_assumed": "0.0012",
        "estimated_financial_exposure_usd": str(exposure),
        "independently_verified_avoidable_cost_usd": None,
        "net_incremental_economic_value_usd": None,
        "review_effort_minutes_assumed": {
            name: value["review_minutes_assumed"] for name, value in scores.items()
        },
        "case_statuses": {case: conclusions[case]["status"] for case in CASES},
        "execution_allowed": False,
    }


if __name__ == "__main__":
    print(json.dumps(evaluate(), indent=2, sort_keys=True))
