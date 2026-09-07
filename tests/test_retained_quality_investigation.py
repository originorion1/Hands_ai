import json
from dataclasses import replace
from datetime import UTC, datetime

import pytest

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.history.evidence import HistoricalEvidenceBatch
from orion.learning.autonomous_loop import AuthorizationEnvelope
from orion.learning.retained_quality_investigation import investigate_retained_missing_values
from orion.understanding.metadata import MetadataUnderstanding, StructuralEntity, StructuralField


def model(required=True):
    fields = tuple(StructuralField("Thing", name, "Data", None, None,
                                   required, False, False, False) for name in ("alpha", "beta"))
    return MetadataUnderstanding("tenant", (StructuralEntity(
        "Thing", None, False, False, False, fields, (),
    ),))


def envelope():
    return AuthorizationEnvelope("tenant", "quality", allowed_record_entities=frozenset({"Thing"}),
                                 allowed_record_fields=(("Thing", ("alpha", "beta")),))


def batch(sequence, *records):
    now = datetime(2026, 9, 7, tzinfo=UTC)
    return HistoricalEvidenceBatch("tenant", "Thing", sequence, now, tuple(
        Observation(evidence=Evidence(kind=EvidenceKind.API, source="synthetic",
                    tenant_id="tenant", observed_at=now,
                    payload={"resource": "Thing", "record": {"company": "company", **record}}))
        for record in records
    ))


def investigate(*batches, understanding=None, authorization=None):
    return investigate_retained_missing_values(
        understanding or model(), batches, authorization=authorization or envelope(), company="company",
    )


def test_repeated_identity_does_not_inflate_support_and_keeps_latest_provenance():
    first = batch(1, {"name": "one", "alpha": None})
    second = batch(2, {"name": "one", "alpha": None})
    result = investigate(first, second)
    assert result.aggregate()["missing_identities"] == 1
    assert result.finding.evidence[0].evidence_id == second.observations[0].evidence.evidence_id
    assert not result.finding.evidence[0].prior_different_value


def test_independent_identity_counts_and_absence_are_distinct():
    result = investigate(batch(1, {"name": "one", "alpha": None},
                               {"name": "two", "alpha": " "}, {"name": "three", "beta": 0}))
    assert result.aggregate()["missing_identities"] == 2
    assert result.aggregate()["absent_identities"] == 1


def test_zero_false_are_valid_not_missing():
    result = investigate(batch(1, {"name": "one", "alpha": 0},
                               {"name": "two", "alpha": False}, {"name": "three", "alpha": None}))
    assert result.aggregate()["valid_identities"] == 2
    assert result.aggregate()["missing_identities"] == 1


def test_absent_later_projection_does_not_erase_observed_missing_field():
    result = investigate(batch(1, {"name": "one", "alpha": None}),
                         batch(2, {"name": "one", "beta": 5}))
    assert result.finding.field == "alpha"
    assert result.finding.evidence[0].sequence == 1


def test_later_valid_value_resolves_missing_question_and_input_order_is_irrelevant():
    first = batch(1, {"name": "one", "alpha": None})
    second = batch(2, {"name": "one", "alpha": "present"})
    assert investigate(second, first).finding is None


def test_previous_different_value_is_temporal_change_not_business_defect():
    result = investigate(batch(1, {"name": "one", "alpha": "present"}),
                         batch(2, {"name": "one", "alpha": None}))
    assert result.aggregate()["changed_identities"] == 1
    assert not result.aggregate()["business_defect_validated"]
    assert result.aggregate()["applicability"] == "unknown"


def test_metadata_requirement_changes_priority_without_asserting_applicability():
    data = batch(1, {"name": "one", "alpha": None, "beta": None})
    understanding = model()
    structure = understanding.entities[0]
    changed = replace(understanding, entities=(replace(structure, fields=(
        replace(structure.fields[0], required=False), structure.fields[1],
    )),))
    assert investigate(data).finding.field == "alpha"
    assert investigate(data, understanding=changed).finding.field == "beta"
    assert investigate(data, understanding=model(False)).aggregate()["applicability"] == "unknown"


def test_aggregate_has_no_identifiers_values_or_provenance():
    result = investigate(batch(1, {"name": "private-identity", "alpha": None}))
    serialized = json.dumps(result.aggregate())
    for forbidden in ("private-identity", "tenant", "company", "Thing", "alpha", "evidence_id"):
        assert forbidden not in serialized
    assert not result.aggregate()["prediction_validated"]
    assert not result.aggregate()["execution_allowed"]


@pytest.mark.parametrize("record", [
    {"name": "one", "company": "other", "alpha": None},
    {"name": "one", "unauthorized": 1},
    {"name": "one", "alpha": [1]},
])
def test_company_field_and_non_scalar_scope_rejection(record):
    with pytest.raises((ValueError, TypeError)):
        investigate(batch(1, record))


def test_wrong_tenant_audit_scope_and_duplicate_sequence_rejected():
    data = batch(1, {"name": "one", "alpha": None})
    with pytest.raises(ValueError, match="tenant"):
        investigate(data, understanding=replace(model(), tenant_id="other"))
    with pytest.raises(ValueError, match="target"):
        investigate(data, authorization=replace(envelope(), allowed_record_fields=(("Thing", ("name",)),)))
    with pytest.raises(ValueError, match="sequence"):
        investigate(data, data)


def test_no_candidate_for_no_evidence_or_only_absent_keys():
    assert investigate().finding is None
    assert investigate(batch(1, {"name": "one"})).finding is None


def test_revalidates_mutated_payload_and_rejects_new_hidden_metadata():
    data = batch(1, {"name": "one", "alpha": None})
    data.observations[0].evidence.payload["record"]["name"] = ""
    with pytest.raises(ValueError):
        investigate(data)
    structure = model().entities[0]
    hidden = replace(model(), entities=(replace(structure, fields=(
        replace(structure.fields[0], hidden=True), structure.fields[1],
    )),))
    with pytest.raises(ValueError, match="unsafe"):
        investigate(understanding=hidden)
