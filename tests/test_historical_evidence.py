import json
from collections import UserDict
from datetime import UTC, datetime
from uuid import uuid4

import pytest

from orion.contracts import Evidence, EvidenceKind, Observation, ObservationMode
from orion.history.evidence import (
    HistoricalEvidenceBatch,
    HistoricalEvidenceError,
    historical_evidence_checksum,
    historical_evidence_from_json,
    historical_evidence_to_json,
)


def observation(*, tenant_id="customer-a", resource="Purchase Invoice", record=None, **kwargs):
    return Observation(
        evidence=Evidence(
            kind=kwargs.pop("kind", EvidenceKind.API),
            source="historical-test",
            tenant_id=tenant_id,
            observed_at=kwargs.pop("observed_at", datetime(2026, 9, 2, 10, tzinfo=UTC)),
            payload={"resource": resource, "record": record or {"name": "PINV-001", "total": 12.5}},
            confidence=kwargs.pop("confidence", 0.75),
            evidence_id=kwargs.pop("evidence_id", uuid4()),
        ),
        mode=kwargs.pop("mode", ObservationMode.READ_ONLY),
        observation_id=kwargs.pop("observation_id", uuid4()),
    )


def batch(**kwargs):
    return HistoricalEvidenceBatch(
        tenant_id=kwargs.pop("tenant_id", "customer-a"),
        resource=kwargs.pop("resource", "Purchase Invoice"),
        sequence=kwargs.pop("sequence", 1),
        created_at=kwargs.pop("created_at", datetime(2026, 9, 2, 11, tzinfo=UTC)),
        observations=kwargs.pop("observations", (observation(),)),
    )


def test_canonical_round_trip_preserves_observation_and_evidence_ids():
    original = batch()
    restored = historical_evidence_from_json(historical_evidence_to_json(original))

    assert restored == original
    assert restored.observations[0].observation_id == original.observations[0].observation_id
    assert restored.observations[0].evidence.evidence_id == original.observations[0].evidence.evidence_id


def test_serialization_and_checksum_are_deterministic():
    current = batch()
    payload = historical_evidence_to_json(current)

    assert payload == historical_evidence_to_json(current)
    assert historical_evidence_checksum(payload) == historical_evidence_checksum(payload)
    assert json.loads(payload)["format_version"] == 1


def test_rejects_cross_tenant_batch():
    with pytest.raises(HistoricalEvidenceError, match="tenant"):
        batch(observations=(observation(tenant_id="customer-b"),))


def test_rejects_wildcard_resource():
    with pytest.raises(HistoricalEvidenceError, match="wildcard"):
        batch(resource="*")


def test_rejects_non_read_only_observation():
    with pytest.raises(HistoricalEvidenceError, match="READ_ONLY"):
        batch(observations=(observation(mode=ObservationMode.SHADOW),))


def test_rejects_non_api_evidence():
    with pytest.raises(HistoricalEvidenceError, match="API"):
        batch(observations=(observation(kind=EvidenceKind.METADATA),))


def test_rejects_resource_mismatch():
    with pytest.raises(HistoricalEvidenceError, match="resource"):
        batch(observations=(observation(resource="Sales Invoice"),))


def test_rejects_duplicate_document_identity():
    with pytest.raises(HistoricalEvidenceError, match="duplicate document"):
        batch(
            observations=(
                observation(record={"name": "PINV-001"}),
                observation(record={"name": "PINV-001"}),
            )
        )


def test_rejects_unsupported_payload_fields():
    current = historical_evidence_to_json(batch())
    payload = json.loads(current)
    payload["observations"][0]["evidence"]["payload"]["unexpected"] = True

    with pytest.raises(HistoricalEvidenceError, match="exactly"):
        historical_evidence_from_json(json.dumps(payload))


def test_rejects_non_finite_numeric_payload():
    with pytest.raises(HistoricalEvidenceError, match="non-finite"):
        batch(observations=(observation(record={"name": "PINV-001", "total": float("nan")}),))


def test_rejects_duplicate_observation_and_evidence_ids():
    duplicate_observation_id = uuid4()
    duplicate_evidence_id = uuid4()
    with pytest.raises(HistoricalEvidenceError, match="duplicate observation"):
        batch(
            observations=(
                observation(observation_id=duplicate_observation_id),
                observation(observation_id=duplicate_observation_id, record={"name": "PINV-002"}),
            )
        )
    with pytest.raises(HistoricalEvidenceError, match="duplicate evidence"):
        batch(
            observations=(
                observation(evidence_id=duplicate_evidence_id),
                observation(evidence_id=duplicate_evidence_id, record={"name": "PINV-002"}),
            )
        )


def test_rejects_timezone_naive_timestamp():
    with pytest.raises(HistoricalEvidenceError, match="timezone-aware"):
        batch(created_at=datetime(2026, 9, 2, 11, tzinfo=UTC).replace(tzinfo=None))


def test_rejects_timezone_naive_evidence_timestamp():
    with pytest.raises(HistoricalEvidenceError, match="timezone-aware"):
        batch(
            observations=(
                observation(
                    observed_at=datetime(2026, 9, 2, 10, tzinfo=UTC).replace(tzinfo=None)
                ),
            )
        )


def test_mapping_payload_is_canonicalized_to_plain_json():
    current = batch(
        observations=(
            observation(
                record=UserDict({"name": "PINV-001", "nested": UserDict({"amount": 12.5})})
            ),
        )
    )
    restored = historical_evidence_from_json(historical_evidence_to_json(current))

    assert restored.observations[0].evidence.payload["record"]["nested"]["amount"] == 12.5
