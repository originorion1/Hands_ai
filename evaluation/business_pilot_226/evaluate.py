"""Deterministic synthetic procurement baseline versus canonical ORION investigation.

No customer inputs, network access, credentials, ERP writes, predictions, or grants.
The fixture's review labels are synthetic annotations, not customer ground truth.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.history.evidence import HistoricalEvidenceBatch
from orion.learning.autonomous_loop import AuthorizationEnvelope
from orion.learning.retained_quality_investigation import investigate_retained_missing_values
from orion.understanding.metadata import MetadataUnderstanding, StructuralEntity, StructuralField

CANONICAL_SHA = "eabda90593f00ddc9b6ace6a4b9b9301fb3a01dd"
FIXTURE = Path(__file__).with_name("fixture.json")


def missing(value: object) -> bool:
    """Conventional report rule implemented independently of ORION's helper."""
    return value is None or (type(value) is str and value.strip() == "")


def batch(config: dict, period: dict, sequence: int, records: list[dict]) -> HistoricalEvidenceBatch:
    observed_at = datetime.fromisoformat(period["start"]).replace(tzinfo=UTC)
    observations = tuple(
        Observation(
            evidence=Evidence(
                kind=EvidenceKind.API,
                source="offline-synthetic-fixture-v1",
                payload={"resource": config["resource"], "record": record},
                observed_at=observed_at,
                evidence_id=uuid5(
                    NAMESPACE_URL, f"pilot-226:{period['id']}:{sequence}:{record['name']}:evidence"
                ),
                tenant_id=config["tenant_id"],
            ),
            observation_id=uuid5(
                NAMESPACE_URL, f"pilot-226:{period['id']}:{sequence}:{record['name']}:observation"
            ),
        )
        for record in records
    )
    return HistoricalEvidenceBatch(
        config["tenant_id"], config["resource"], sequence, observed_at, observations
    )


def metrics(predicted: set[tuple[str, str]], positive: set[tuple[str, str]],
            universe: set[tuple[str, str]]) -> dict:
    tp = len(predicted & positive)
    fp = len(predicted - positive)
    fn = len(positive - predicted)
    tn = len(universe - predicted - positive)
    return {
        "true_positive": tp,
        "false_positive": fp,
        "false_negative": fn,
        "true_negative": tn,
        "precision": tp / (tp + fp) if tp + fp else None,
        "false_positive_rate": fp / (fp + tn) if fp + tn else None,
        "recall": tp / (tp + fn) if tp + fn else None,
    }


def main() -> dict:
    raw = FIXTURE.read_bytes()
    config = json.loads(raw)
    assert config["synthetic"] is True
    fields = tuple(config["fields"])
    resource = config["resource"]
    metadata = MetadataUnderstanding(config["tenant_id"], (
        StructuralEntity(
            resource, None, False, False, False,
            tuple(StructuralField(resource, name, "Data", None, None,
                                  config["fields"][name]["required"], False, False, False)
                  for name in fields),
            (uuid5(NAMESPACE_URL, "pilot-226:synthetic-metadata"),),
        ),
    ))
    authorization = AuthorizationEnvelope(
        config["tenant_id"], "synthetic-quality-review",
        allowed_record_entities=frozenset({resource}),
        allowed_record_fields=((resource, fields),),
        max_records_per_proposal=10,
        max_cumulative_records=20,
    )
    conventional: set[tuple[str, str]] = set()
    orion: set[tuple[str, str]] = set()
    positive: set[tuple[str, str]] = set()
    universe: set[tuple[str, str]] = set()
    period_results = []
    batches = {}
    for period in config["periods"]:
        period_id = period["id"]
        records = period["records"]
        baseline_fields = tuple(
            name for name in fields if any(missing(record.get(name)) for record in records)
        )
        conventional.update((period_id, name) for name in baseline_fields)
        positive.update((period_id, name) for name in period["independent_review_fields"])
        universe.update((period_id, name) for name in fields)

        evidence = batch(config, period, 1, records)
        batches[period_id] = evidence
        investigation = investigate_retained_missing_values(
            metadata, (evidence,), authorization=authorization, company=config["company"]
        )
        finding = investigation.finding
        if finding is not None:
            orion.add((period_id, finding.field))
        period_results.append({
            "period": period_id,
            "window": [period["start"], period["end"]],
            "records": len(records),
            "review_positive_fields": len(period["independent_review_fields"]),
            "baseline_candidate_fields": list(baseline_fields),
            "orion_candidate_field": finding.field if finding else None,
            "orion_aggregate": investigation.aggregate(),
            "orion_evidence_count": len(finding.evidence) if finding else 0,
            "metadata_provenance_count": len(finding.metadata_provenance_ids) if finding else 0,
            "relevant_evidence_sha256": finding.relevant_evidence_sha256 if finding else None,
            "source_provenance_sha256": finding.source_provenance_sha256 if finding else None,
        })

    holdout = next(item for item in config["periods"] if item["id"] == "holdout")
    correction = batch(config, holdout, 2, config["holdout_correction"])
    corrected = investigate_retained_missing_values(
        metadata, (batches["holdout"], correction),
        authorization=authorization, company=config["company"],
    )
    assert corrected.finding is None, "corrected evidence must invalidate stale candidate"
    wrong_tenant_denied = False
    try:
        investigate_retained_missing_values(
            replace(metadata, tenant_id="different-synthetic-tenant"),
            (batches["holdout"],), authorization=authorization, company=config["company"],
        )
    except ValueError:
        wrong_tenant_denied = True
    assert wrong_tenant_denied
    duplicate_sequence_denied = False
    try:
        investigate_retained_missing_values(
            metadata, (batches["holdout"], batches["holdout"]),
            authorization=authorization, company=config["company"],
        )
    except ValueError:
        duplicate_sequence_denied = True
    assert duplicate_sequence_denied

    return {
        "canonical_source_sha": CANONICAL_SHA,
        "fixture_sha256": hashlib.sha256(raw).hexdigest(),
        "synthetic_only": True,
        "unit": "period-field review candidate",
        "periods": period_results,
        "baseline_metrics": metrics(conventional, positive, universe),
        "orion_metrics": metrics(orion, positive, universe),
        "incremental_orion_discoveries": len(orion - conventional),
        "heldout_learning_improvement": None,
        "stale_candidate_invalidated_after_correction": True,
        "wrong_tenant_denied": wrong_tenant_denied,
        "duplicate_sequence_denied": duplicate_sequence_denied,
        "verified_customer_findings": 0,
        "verified_financial_impact": None,
        "execution_allowed": False,
    }


if __name__ == "__main__":
    print(json.dumps(main(), indent=2, sort_keys=True))
