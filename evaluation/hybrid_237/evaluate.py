"""Frozen, synthetic-only comparison of existing ORION and simple composition.

This evaluator does not install a runtime pipeline or grant any capability.
Detector outputs are frozen before the separate design-label file is opened.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

from evaluation.intelligence_benchmark_230.evaluate import (
    KINDS,
    conventional,
    orion_missing_evidence,
    scores,
    statistical_price,
)

ROOT = Path(__file__).resolve().parent
REFERENCE = ROOT.parent / "intelligence_benchmark_230"
EVENTS_SHA256 = "e063690c7ebd20658fee5983680ee6f80536646a95e8206adef662e87c4020a6"
LABELS_SHA256 = "7d50d7ea096f2ad37b727b9bf3375cd3e0ac7822ffdbc6e45252fe2874b24b75"
REQUESTS = ("invoice_detail", "stock_witness", "document_status")
FIXED_ORDER = REQUESTS
SYMPTOM_FIRST = {
    "missing_document": "document_status",
    "contradictory_receipt": "stock_witness",
    "stale_document": "document_status",
}


def _read_frozen(path: Path, digest: str) -> dict:
    raw = path.read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError(f"frozen fixture changed: {path.name}")
    return json.loads(raw)


def _freeze(predictions: dict[str, set[tuple[str, int, str]]]) -> str:
    serialized = json.dumps(
        {name: sorted([list(item) for item in values]) for name, values in predictions.items()},
        sort_keys=True, separators=(",", ":"),
    ).encode()
    return hashlib.sha256(serialized).hexdigest()


def three_way() -> dict:
    events = _read_frozen(REFERENCE / "events.json", EVENTS_SHA256)["events"]
    rules = conventional(events)
    statistical = statistical_price(events)
    orion, provenance = orion_missing_evidence(events)
    baseline = rules | statistical
    # This is a candidate composition, not a new ORION detector. It cannot
    # invent a finding: every item comes from an evaluated component.
    hybrid = baseline | orion
    predictions = {"conventional_statistical": baseline,
                   "existing_orion": orion, "hybrid_candidate": hybrid}
    frozen_digest = _freeze(predictions)

    labels = _read_frozen(REFERENCE / "ground_truth.json", LABELS_SHA256)["labels"]
    positives = {(row["tenant"], row["period"], row["kind"]) for row in labels}
    universe = {(row["tenant"], row["period"], kind)
                for row in events for kind in KINDS}
    evidence = {}
    for name, findings in predictions.items():
        evidence[name] = {
            f"{tenant}:{period}:{kind}": sorted(
                row["evidence_id"] for row in events
                if row["tenant"] == tenant and row["period"] == period
            ) for tenant, period, kind in findings
        }
    return {
        "schema": "orion.hybrid-offline-comparison.v1",
        "source_reference_sha": "ef67bfe3a6c3ab41391c208957deb0d7d4842c49",
        "events_sha256": EVENTS_SHA256,
        "labels_sha256": LABELS_SHA256,
        "predictions_frozen_before_labels_sha256": frozen_digest,
        "results": {name: scores(findings, positives, universe)
                    for name, findings in predictions.items()},
        "incremental_hybrid_true_positives": len((hybrid - baseline) & positives),
        "incremental_hybrid_false_positives": len((hybrid - baseline) - positives),
        "candidate_evidence_refs": evidence,
        "orion_provenance": provenance,
        "candidate_review_minutes_assumption": 6,
        "baseline_review_minutes_estimate": 6 * len(baseline),
        "hybrid_review_minutes_estimate": 6 * len(hybrid),
        "verified_avoidable_cost_usd": 0,
        "realized_savings_usd": 0,
        "calibrated_probabilities": None,
        "execution_allowed": False,
    }


def _order(case: dict, strategy: str) -> tuple[str, ...]:
    allowed = set(case["allowed_requests"])
    if not allowed.issubset(REQUESTS):
        raise ValueError("unreviewed request capability")
    if (type(case["authorized"]) is not bool or type(case["max_requests"]) is not int
            or not 0 <= case["max_requests"] <= len(REQUESTS)):
        raise ValueError("invalid authority or request budget")
    if not case["authorized"]:
        return ()
    if strategy == "fixed":
        return tuple(request for request in FIXED_ORDER if request in allowed)
    if strategy == "symptom_first":
        preferred = SYMPTOM_FIRST.get(case["symptom"], "invoice_detail")
        return tuple(request for request in dict.fromkeys((preferred, *FIXED_ORDER))
                     if request in allowed)
    if strategy == "value_ranked":
        # All terms are visible before outcome disclosure. No hidden label or
        # future result is an input to ranking.
        return tuple(sorted(allowed, key=lambda request: (
            -(case["expected_reduction"][request] * case["potential_value"]
              - case["request_cost"][request] - case["privacy_risk"][request]),
            request,
        )))
    raise ValueError("unknown strategy")


def active_investigation() -> dict:
    cases_raw = (ROOT / "active_cases.json").read_bytes()
    cases = json.loads(cases_raw)["cases"]
    if len({case["id"] for case in cases}) != len(cases):
        raise ValueError("duplicate active case")
    plans = {strategy: {case["id"]: _order(case, strategy) for case in cases}
             for strategy in ("fixed", "symptom_first", "value_ranked")}
    plan_digest = hashlib.sha256(json.dumps(plans, sort_keys=True).encode()).hexdigest()
    outcomes_raw = (ROOT / "active_outcomes.json").read_bytes()
    outcomes = json.loads(outcomes_raw)["outcomes"]
    if set(outcomes) != {case["id"] for case in cases}:
        raise ValueError("active outcome identity mismatch")
    results = {}
    for strategy, entries in plans.items():
        requests = 0
        resolved = 0
        total_request_cost = 0
        unresolved = 0
        for case in cases:
            order = entries[case["id"]][:case["max_requests"]]
            adjudication = outcomes[case["id"]]
            if (type(adjudication) is not dict
                    or adjudication["period"] <= case["period"]):
                raise ValueError("outcome must follow the evidence period")
            answer = adjudication["needed"]
            if not case["authorized"]:
                assert not order, "revoked case must not request evidence"
                continue
            if answer is not None and answer not in case["allowed_requests"]:
                raise ValueError("outcome requires unauthorized capability")
            used = order.index(answer) + 1 if answer in order else len(order)
            requests += used
            total_request_cost += sum(case["request_cost"][request] for request in order[:used])
            resolved += answer in order
            unresolved += answer is not None and answer not in order
        results[strategy] = {"requests": requests, "resolved": resolved,
                             "unresolved": unresolved,
                             "request_cost_units": total_request_cost,
                             "resolved_per_request": resolved / requests if requests else None}
    return {"cases_sha256": hashlib.sha256(cases_raw).hexdigest(),
            "outcomes_sha256": hashlib.sha256(outcomes_raw).hexdigest(),
            "plans_frozen_before_outcomes_sha256": plan_digest,
            "results": results, "execution_allowed": False}


def main() -> dict:
    return {"three_way": three_way(), "active_investigation": active_investigation()}


if __name__ == "__main__":
    result = main()
    (ROOT / "results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
