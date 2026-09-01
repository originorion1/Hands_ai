import inspect
import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import pytest

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.history.evidence import HistoricalEvidenceBatch, HistoricalEvidenceError
from orion.learning import (
    SampleSufficiency,
    customer_patterns,
    format_safe_pattern_summary,
    project_customer_patterns,
)

TENANT = "synthetic-tenant"
RESOURCE = "Purchase Invoice"


def make_batch(sequence=1, records=None, tenant_id=TENANT, resource=RESOURCE):
    if records is None:
        records = [
            {
                "name": f"SYN-{index}",
                "supplier": "Supplier Alpha" if index % 2 else "Supplier Beta",
                "currency": "USD" if index < 3 else "EUR",
                "grand_total": float(index * 10),
                "posting_date": f"2026-09-0{index + 1}",
                "due_date": f"2026-09-{index + 8:02d}",
            }
            for index in range(1, 6)
        ]
    observations = tuple(
        Observation(
            evidence=Evidence(
                kind=EvidenceKind.API,
                source="synthetic-fixture",
                tenant_id=tenant_id,
                observed_at=datetime(2026, 9, 2, tzinfo=UTC),
                payload={"resource": resource, "record": record},
            )
        )
        for record in records
    )
    return HistoricalEvidenceBatch(
        tenant_id=tenant_id,
        resource=resource,
        sequence=sequence,
        created_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
        observations=observations,
    )


def batch_from_observations(sequence, observations):
    return HistoricalEvidenceBatch(
        tenant_id=TENANT,
        resource=RESOURCE,
        sequence=sequence,
        created_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
        observations=tuple(observations),
    )


def test_deterministic_projection_and_conservative_sufficiency():
    history = (make_batch(),)
    snapshot = project_customer_patterns(history, tenant_id=TENANT, resource=RESOURCE)

    assert snapshot == project_customer_patterns(history, tenant_id=TENANT, resource=RESOURCE)
    assert snapshot.observation_count == 5
    assert snapshot.sample_sufficiency is SampleSufficiency.PRELIMINARY
    assert snapshot.batch_sequences == (1,)
    assert snapshot.supplier_frequencies == (("Supplier Alpha", 3), ("Supplier Beta", 2))
    assert snapshot.currency_frequencies == (("EUR", 3), ("USD", 2))
    assert snapshot.amount_count == 5
    assert snapshot.amount_min == 10.0
    assert snapshot.amount_max == 50.0
    assert snapshot.amount_mean == 30.0
    assert snapshot.amount_median == 30.0
    assert snapshot.due_interval_count == 5
    assert snapshot.due_interval_min_days == 7
    assert snapshot.due_interval_max_days == 7
    assert snapshot.posting_date_min == "2026-09-02"
    assert snapshot.posting_date_max == "2026-09-06"


def test_provenance_uuid_references_are_preserved():
    history = (make_batch(),)
    snapshot = project_customer_patterns(history, tenant_id=TENANT, resource=RESOURCE)
    expected_observations = tuple(item.observation_id for item in history[0].observations)
    expected_evidence = tuple(item.evidence.evidence_id for item in history[0].observations)

    assert snapshot.observation_ids == expected_observations
    assert snapshot.evidence_ids == expected_evidence


def test_scope_and_consecutive_sequence_fail_closed():
    history = (make_batch(),)
    with pytest.raises(HistoricalEvidenceError, match="tenant"):
        project_customer_patterns(history, tenant_id="other-tenant", resource=RESOURCE)
    with pytest.raises(HistoricalEvidenceError, match="resource"):
        project_customer_patterns(history, tenant_id=TENANT, resource="Sales Invoice")
    with pytest.raises(HistoricalEvidenceError, match="boundary"):
        project_customer_patterns((make_batch(), make_batch(sequence=3)), tenant_id=TENANT, resource=RESOURCE)


def test_history_wide_duplicate_document_identity_fails_closed():
    first = make_batch(records=[{"name": "SYN-DUP", "supplier": "Supplier Alpha", "currency": "USD"}])
    second = make_batch(sequence=2, records=[{"name": "SYN-DUP", "supplier": "Supplier Alpha", "currency": "USD"}])

    with pytest.raises(HistoricalEvidenceError, match="duplicate document identity"):
        project_customer_patterns((first, second), tenant_id=TENANT, resource=RESOURCE)


def test_history_wide_duplicate_observation_uuid_fails_closed():
    first = make_batch(records=[{"name": "SYN-OBS-1"}])
    original = first.observations[0]
    second_observation = replace(
        original,
        evidence=replace(
            original.evidence,
            payload={"resource": RESOURCE, "record": {"name": "SYN-OBS-2"}},
        ),
    )
    second = batch_from_observations(2, (second_observation,))

    with pytest.raises(HistoricalEvidenceError, match="duplicate observation UUID"):
        project_customer_patterns((first, second), tenant_id=TENANT, resource=RESOURCE)


def test_history_wide_duplicate_evidence_uuid_fails_closed():
    first = make_batch(records=[{"name": "SYN-EVID-1"}])
    original = first.observations[0]
    second_observation = replace(
        original,
        observation_id=uuid4(),
        evidence=replace(
            original.evidence,
            payload={"resource": RESOURCE, "record": {"name": "SYN-EVID-2"}},
        ),
    )
    second = batch_from_observations(2, (second_observation,))

    with pytest.raises(HistoricalEvidenceError, match="duplicate evidence UUID"):
        project_customer_patterns((first, second), tenant_id=TENANT, resource=RESOURCE)


def test_non_overlapping_two_batch_history_counts_each_observation_once():
    history = (
        make_batch(records=[{"name": "SYN-BATCH-1", "supplier": "Supplier Alpha", "currency": "USD", "grand_total": 10}]),
        make_batch(sequence=2, records=[{"name": "SYN-BATCH-2", "supplier": "Supplier Beta", "currency": "EUR", "grand_total": 20}]),
    )
    snapshot = project_customer_patterns(history, tenant_id=TENANT, resource=RESOURCE)

    assert snapshot.observation_count == 2
    assert snapshot.batch_sequences == (1, 2)
    assert snapshot.supplier_frequencies == (("Supplier Alpha", 1), ("Supplier Beta", 1))
    assert snapshot.amount_mean == 15.0
    assert snapshot == project_customer_patterns(history, tenant_id=TENANT, resource=RESOURCE)


def test_quality_counters_handle_invalid_amounts_dates_and_negative_intervals():
    records = [
        {
            "name": "SYN-BOOL",
            "supplier": "Supplier Alpha",
            "currency": "USD",
            "grand_total": True,
            "posting_date": "2026-09-10",
            "due_date": "2026-09-01",
        },
        {
            "name": "SYN-BAD-DATE",
            "supplier": "",
            "currency": " EUR ",
            "grand_total": float("inf"),
            "posting_date": "not-a-date",
            "due_date": "2026-09-20",
        },
        {
            "name": "SYN-MISSING",
            "supplier": "Supplier Beta",
            "currency": "USD",
            "grand_total": 5,
            "posting_date": "2026-09-01",
            "due_date": "bad-date",
        },
    ]
    # Non-finite values are rejected by the evidence contract, so replace it
    # with a JSON-compatible bool to exercise the projection quality counter.
    records[1]["grand_total"] = False
    snapshot = project_customer_patterns(
        (make_batch(records=records),), tenant_id=TENANT, resource=RESOURCE
    )

    assert snapshot.amount_count == 1
    assert dict(snapshot.invalid_field_counts)["grand_total"] == 2
    assert dict(snapshot.invalid_field_counts)["supplier"] == 1
    assert dict(snapshot.invalid_field_counts)["currency"] == 1
    assert dict(snapshot.invalid_field_counts)["posting_date"] == 1
    assert dict(snapshot.invalid_field_counts)["due_date"] == 1
    assert dict(snapshot.invalid_field_counts)["due_interval"] == 1


def test_empty_history_is_explicitly_insufficient():
    snapshot = project_customer_patterns((), tenant_id=TENANT, resource=RESOURCE)

    assert snapshot.observation_count == 0
    assert snapshot.sample_sufficiency is SampleSufficiency.INSUFFICIENT
    assert snapshot.distinct_supplier_count == 0
    assert snapshot.amount_count == 0


def test_safe_summary_excludes_raw_customer_values_amounts_and_ids():
    snapshot = project_customer_patterns((make_batch(),), tenant_id=TENANT, resource=RESOURCE)
    summary = format_safe_pattern_summary(snapshot)
    rendered = json.dumps(summary, sort_keys=True)

    assert "Supplier Alpha" not in rendered
    assert "Supplier Beta" not in rendered
    assert "SYN-" not in rendered
    assert "Example Company" not in rendered
    assert "30.0" not in rendered
    assert str(snapshot.evidence_ids[0]) not in rendered
    assert summary["execution_allowed"] is False
    assert summary["recommendation_allowed"] is False
    assert summary["promotion_allowed"] is False


def test_learning_core_has_no_erp_or_external_capability():
    source = inspect.getsource(customer_patterns).lower()
    assert "erpnext" not in source
    assert "http" not in source
    assert "checkpoint" not in source
    assert "knowledge" not in source
    assert "requests" not in source
    assert not Path(customer_patterns.__file__).name.startswith("erpnext")
