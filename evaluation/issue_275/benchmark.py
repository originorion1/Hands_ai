"""Three-way equal-access evaluation on frozen synthetic purchase cases.

The labels are opened only after all predictions have been recorded. This is
engineering evaluation, not independent customer adjudication or realized ROI.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal
from pathlib import Path
from time import perf_counter_ns

from evaluation.procurement_price_270.evaluate import _score, conventional
from evaluation.procurement_price_270.fixture import CASES, rows
from orion.business.procurement import investigate_purchases

LABELS = Path(__file__).parents[1] / "procurement_price_270" / "ground_truth.json"
COMPANY = "Synthetic Procurement Co"
ARMS = ("conventional_union", "existing_orion", "hybrid_investigation")


def _source_digest(source: tuple[dict, ...]) -> str:
    return hashlib.sha256(json.dumps(
        source, sort_keys=True, separators=(",", ":"), allow_nan=False,
    ).encode()).hexdigest()


def _hybrid(source: tuple[dict, ...], *, flags: dict[str, bool], case: str) -> dict:
    """Use conventional detection to select a bounded ORION investigation.

    This fixed policy can miss an ORION-only candidate. It receives the same
    source rows and no additional evidence or label access.
    """
    if not any(flags.values()):
        return {"status": "not_selected", "estimated_exposure_usd": "0.00",
                "next_question": None, "missing_evidence": [],
                "alternative_explanations": []}
    return investigate_purchases(source, target_ref=f"{case}-T", company=COMPANY)


def evaluate_three_way() -> dict[str, object]:
    predictions: dict[str, dict[str, bool]] = {arm: {} for arm in ARMS}
    statuses: dict[str, dict[str, str]] = {arm: {} for arm in ARMS}
    digests: dict[str, str] = {}
    exposure: dict[str, Decimal] = {arm: Decimal(0) for arm in ARMS}
    elapsed_ns: dict[str, int] = {arm: 0 for arm in ARMS}
    evidence_rows = 0
    questions: dict[str, int] = {arm: 0 for arm in ARMS}
    for case in sorted(CASES):
        source = rows(case)
        digests[case] = _source_digest(source)
        evidence_rows += len(source)
        start = perf_counter_ns()
        flags = conventional(source)
        elapsed_ns["conventional_union"] += perf_counter_ns() - start
        conventional_candidate = any(flags.values())
        predictions["conventional_union"][case] = conventional_candidate
        statuses["conventional_union"][case] = (
            "candidate_review" if conventional_candidate else "no_anomaly"
        )
        start = perf_counter_ns()
        existing = investigate_purchases(source, target_ref=f"{case}-T", company=COMPANY)
        elapsed_ns["existing_orion"] += perf_counter_ns() - start
        start = perf_counter_ns()
        hybrid = _hybrid(source, flags=flags, case=case)
        elapsed_ns["hybrid_investigation"] += perf_counter_ns() - start
        for arm, result in (("existing_orion", existing),
                            ("hybrid_investigation", hybrid)):
            status = result["status"]
            predictions[arm][case] = status == "candidate_review"
            statuses[arm][case] = status
            if status == "candidate_review":
                exposure[arm] += Decimal(result["estimated_exposure_usd"])
            questions[arm] += result["next_question"] is not None

    # This file is not imported by the fixture or any detector.
    label_bytes = LABELS.read_bytes()
    labels = json.loads(label_bytes)
    positives = set(labels["positive_cases"])
    if positives - set(CASES):
        raise ValueError("holdout label outside frozen case set")
    scores = {arm: _score(predictions[arm], positives) for arm in ARMS}
    for arm in ARMS:
        scores[arm]["correct_negative_abstentions"] = sum(
            statuses[arm][case] == "abstained" and case not in positives
            for case in CASES
        )
        scores[arm]["false_findings"] = scores[arm]["fp"]
        scores[arm]["estimated_exposure_usd"] = (
            None if arm == "conventional_union"
            else str(exposure[arm].quantize(Decimal("0.01")))
        )
        scores[arm]["compute_elapsed_ms_diagnostic"] = round(elapsed_ns[arm] / 1e6, 3)
        scores[arm]["logical_evidence_batches"] = len(CASES)
        scores[arm]["source_rows_available"] = evidence_rows
        scores[arm]["next_evidence_questions"] = questions[arm]
        scores[arm]["measured_analyst_review_minutes"] = None
        scores[arm]["billed_compute_cost_usd"] = None
        scores[arm]["billed_acquisition_cost_usd"] = None
        scores[arm]["net_economic_value_usd"] = None
    return {
        "schema": "orion.procurement-three-way.v1",
        "source_sha256_by_case": digests,
        "labels_sha256": hashlib.sha256(label_bytes).hexdigest(),
        "labels_origin": "designed synthetic, not independently adjudicated",
        "case_count": len(CASES), "designed_positive_count": len(positives),
        "equal_access": {"cases": sorted(CASES), "rows": evidence_rows,
                         "period": "2026-01-01 through 2026-03-01",
                         "permission": "same synthetic admitted read scope"},
        "fixed_threshold_fraction": "0.15",
        "scores": scores,
        "case_statuses": statuses,
        "incremental_designed_positive_conclusions": sum(
            predictions["hybrid_investigation"][case]
            and not predictions["conventional_union"][case]
            and not predictions["existing_orion"][case]
            and case in positives for case in CASES
        ),
        "independently_adjudicated_incremental_conclusions": None,
        "traceability": "all 48 source rows and labels separately digest-bound",
        "calibration": "not applicable: categorical confidence, no probability forecast",
        "detection_latency": "offline complete-period batch; live latency unmeasured",
        "financial_outcome": "estimated exposure only; avoidability and savings unverified",
        "execution_allowed": False,
    }


if __name__ == "__main__":
    print(json.dumps(evaluate_three_way(), indent=2, sort_keys=True))
