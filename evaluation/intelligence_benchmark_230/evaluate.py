"""Compare actual ORION missing-evidence capability with conventional baselines.

Labels are read only after all detector predictions are frozen in memory.
"""

from __future__ import annotations

import hashlib
import json
from collections import defaultdict
from dataclasses import replace
from pathlib import Path
from statistics import median
from uuid import NAMESPACE_URL, uuid5

from evaluation.business_pilot_226.evaluate import batch
from orion.learning.autonomous_loop import AuthorizationEnvelope
from orion.learning.retained_quality_investigation import investigate_retained_missing_values
from orion.understanding.metadata import MetadataUnderstanding, StructuralEntity, StructuralField

ROOT = Path(__file__).parent
KINDS = (
    "price_variance", "duplicate_invoice", "incorrect_invoice", "inventory_mismatch",
    "margin_deterioration", "missing_document", "contradictory_receipt",
)
RESOURCE = "Synthetic Purchase Receipt"
COMPANY = "Synthetic Benchmark Company"


def conventional(events: list[dict]) -> set[tuple[str, int, str]]:
    findings = set()
    seen_invoices: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for row in events:
        scope = row["tenant"], row["period"]
        identity = (row["tenant"], row["period"], row["supplier"])
        unit_cost = row["unit_cost_per_pack"] / row["pack_size"]
        if unit_cost > row["contract_unit_cost"] * 1.15:
            findings.add((*scope, "price_variance"))
        if row["invoice"] in seen_invoices[identity]:
            findings.add((*scope, "duplicate_invoice"))
        seen_invoices[identity].add(row["invoice"])
        if abs(row["billed_amount"] - row["quantity"] * unit_cost) > 1:
            findings.add((*scope, "incorrect_invoice"))
        if abs(row["opening_stock"] + row["received_qty"] - row["sold_qty"]
               - row["closing_stock"]) > 2:
            findings.add((*scope, "inventory_mismatch"))
        if row["prior_margin"] - row["current_margin"] > 0.1:
            findings.add((*scope, "margin_deterioration"))
        if not row["report_doc"]:
            findings.add((*scope, "missing_document"))
        if row["witness_received_qty"] != row["received_qty"]:
            findings.add((*scope, "contradictory_receipt"))
    return findings


def statistical_price(events: list[dict]) -> set[tuple[str, int, str]]:
    training: dict[tuple[str, str], list[float]] = defaultdict(list)
    for row in events:
        if row["period"] < 2:
            training[(row["tenant"], row["category"])].append(
                row["unit_cost_per_pack"] / row["pack_size"]
            )
    findings = set()
    for row in events:
        if row["period"] != 2:
            continue
        values = training[(row["tenant"], row["category"])]
        center = median(values)
        mad = median(abs(value - center) for value in values)
        if row["unit_cost_per_pack"] / row["pack_size"] > center + max(0.15 * center, 3 * mad):
            findings.add((row["tenant"], row["period"], "price_variance"))
    return findings


def orion_missing_evidence(events: list[dict]) -> tuple[set[tuple[str, int, str]], dict]:
    findings = set()
    provenance = {}
    grouped: dict[tuple[str, int], list[dict]] = defaultdict(list)
    for row in events:
        grouped[(row["tenant"], row["period"])].append(row)
    for (tenant, period), rows in grouped.items():
        metadata = MetadataUnderstanding(tenant, (
            StructuralEntity(RESOURCE, None, False, False, False, (
                StructuralField(RESOURCE, "report_doc", "Data", None, None,
                                True, False, False, False),
            ), (uuid5(NAMESPACE_URL, f"benchmark:{tenant}:{period}:metadata"),)),
        ))
        auth = AuthorizationEnvelope(
            tenant, "synthetic-quality-review", allowed_record_entities=frozenset({RESOURCE}),
            allowed_record_fields=((RESOURCE, ("report_doc",)),),
            max_records_per_proposal=100, max_cumulative_records=100,
        )
        config = {"tenant_id": tenant, "resource": RESOURCE}
        period_scope = {"id": f"{tenant}-{period}", "start": f"2026-0{period + 1}-01"}
        records = [{"name": row["id"], "company": COMPANY,
                    "report_doc": row["report_doc"]} for row in rows]
        evidence = batch(config, period_scope, 1, records)
        result = investigate_retained_missing_values(
            metadata, (evidence,), authorization=auth, company=COMPANY,
        )
        if result.finding:
            findings.add((tenant, period, "missing_document"))
            provenance[f"{tenant}:{period}"] = {
                "relevant_evidence_sha256": result.finding.relevant_evidence_sha256,
                "source_provenance_sha256": result.finding.source_provenance_sha256,
                "evidence_count": len(result.finding.evidence),
            }
            corrected_records = [
                {**record, "report_doc": "synthetic-corrected-ref"}
                if record["report_doc"] is None else record
                for record in records
            ]
            correction = batch(config, period_scope, 2, corrected_records)
            corrected = investigate_retained_missing_values(
                metadata, (evidence, correction), authorization=auth, company=COMPANY,
            )
            assert corrected.finding is None, "superseded missing evidence must clear"
        if period == 0:
            denied = False
            try:
                investigate_retained_missing_values(
                    replace(metadata, tenant_id="other-synthetic-tenant"), (evidence,),
                    authorization=auth, company=COMPANY,
                )
            except ValueError:
                denied = True
            assert denied, "cross-tenant metadata must fail closed"
            try:
                investigate_retained_missing_values(
                    metadata, (evidence,), authorization=None, company=COMPANY,
                )
            except TypeError:
                pass
            else:
                raise AssertionError("missing authorization must fail closed")
    return findings, provenance


def scores(predicted: set, positives: set, universe: set) -> dict:
    tp = len(predicted & positives)
    fp = len(predicted - positives)
    fn = len(positives - predicted)
    tn = len(universe - predicted - positives)
    return {"tp": tp, "fp": fp, "fn": fn, "tn": tn,
            "precision": tp / (tp + fp) if tp + fp else None,
            "recall": tp / (tp + fn) if tp + fn else None,
            "false_positive_rate": fp / (fp + tn) if fp + tn else None}


def main() -> dict:
    raw = (ROOT / "events.json").read_bytes()
    events = json.loads(raw)["events"]
    assert len(events) == 72
    assert len({row["evidence_id"] for row in events}) == 72
    base = conventional(events)
    statistical = statistical_price(events)
    orion, provenance = orion_missing_evidence(events)
    # Independent scoring boundary: labels are opened after detector outputs exist.
    labels_raw = (ROOT / "ground_truth.json").read_bytes()
    labels = json.loads(labels_raw)["labels"]
    positives = {(row["tenant"], row["period"], row["kind"]) for row in labels}
    universe = {(row["tenant"], row["period"], kind)
                for row in events for kind in KINDS}
    holdout = {item for item in universe if item[1] == 2}
    baseline_score = scores(base, positives, universe)
    orion_score = scores(orion, positives, universe)
    price_holdout = {item for item in holdout if item[2] == "price_variance"}
    statistical_score = scores(statistical, positives & price_holdout, price_holdout)
    label_ids = {item["id"] for item in labels if item["kind"] == "price_variance"}
    potential_price = sum(
        max(0, row["unit_cost_per_pack"] / row["pack_size"]
            - row["contract_unit_cost"]) * row["quantity"]
        for row in events if row["id"] in label_ids
        and row["unit_cost_per_pack"] / row["pack_size"] > row["contract_unit_cost"]
    )
    demand = defaultdict(int)
    for row in events:
        demand[(row["tenant"], row["category"], row["period"])] += row["sold_qty"]
    forecast_errors = [
        abs(demand[(tenant, category, 1)] - demand[(tenant, category, 2)])
        for tenant in sorted({row["tenant"] for row in events})
        for category in sorted({row["category"] for row in events})
    ]
    assert ("synthetic-distributor-b", 2, "inventory_mismatch") not in base
    return {
        "schema": "orion.intelligence-benchmark.v1",
        "source_base_sha": "2c7fe3ea34cbb2258f03854b2002884214a76d9e",
        "events_sha256": hashlib.sha256(raw).hexdigest(),
        "ground_truth_sha256": hashlib.sha256(labels_raw).hexdigest(),
        "synthetic_events": len(events), "synthetic_positive_units": len(positives),
        "evaluation_unit": "tenant-period-anomaly-class",
        "conventional": baseline_score, "statistical_price_holdout": statistical_score,
        "naive_previous_period_demand_mae": sum(forecast_errors) / len(forecast_errors),
        "naive_forecast_evaluation_groups": len(forecast_errors),
        "orion_actual_missing_document_only": orion_score,
        "orion_supported_classes": ["missing_document"],
        "incremental_orion_validated_discoveries": len((orion - base) & positives),
        "orion_provenance": provenance,
        "cross_tenant_denied": True, "missing_authorization_denied": True,
        "corrected_evidence_invalidates_candidate": True,
        "candidate_review_minutes_assumption": 6,
        "analyst_hourly_usd_assumption": 45,
        "baseline_review_cost_usd_estimate": len(base) * 6 * 45 / 60,
        "orion_review_cost_usd_estimate": len(orion) * 6 * 45 / 60,
        "potential_price_exposure_usd_estimate": round(potential_price, 2),
        "independently_verified_avoidable_usd": 0,
        "realized_customer_savings_usd": 0,
        "heldout_learning_improvement": None,
        "execution_allowed": False,
    }


if __name__ == "__main__":
    result = main()
    (ROOT / "results.json").write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, indent=2, sort_keys=True))
