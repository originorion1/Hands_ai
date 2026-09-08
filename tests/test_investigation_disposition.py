import json
from dataclasses import replace

import pytest
from test_retained_quality_investigation import batch, envelope, model

from orion.learning.investigation_disposition import (
    disposition_from_finding,
    disposition_from_json,
    disposition_to_json,
)
from orion.learning.retained_quality_investigation import investigate_retained_missing_values


def plan(batches, dispositions=(), understanding=None):
    return investigate_retained_missing_values(
        understanding or model(), batches, authorization=envelope(), company="company",
        dispositions=dispositions,
    )


def test_reload_inconclusive_redirects_without_new_observations():
    data = (batch(1, {"name": "private-identity", "alpha": None, "beta": None}),)
    first = plan(data)
    disposition = disposition_from_finding(first.finding)
    encoded = disposition_to_json(disposition)
    assert "private-identity" not in encoded
    assert first.finding.field == "alpha"
    assert plan(data, (disposition_from_json(encoded),)).finding.field == "beta"
    assert len(data) == len(data[0].observations) == 1
    assert disposition.status == "inconclusive"
    assert disposition.uncertainty == "applicability_unknown"
    assert disposition.business_defect_validated is False


def test_repeats_and_unrelated_value_changes_do_not_reopen_question():
    data = (batch(1, {"name": "one", "alpha": None, "beta": None}),)
    remembered = disposition_from_finding(plan(data).finding)
    repeated = (*data, batch(2, {"name": "one", "alpha": None, "beta": "different"}))
    assert plan(repeated, (remembered,)).finding is None
    fresh = plan(repeated).finding
    assert fresh.repeated_observation_count == 1
    assert fresh.relevant_evidence_sha256 == remembered.relevant_evidence_sha256
    assert fresh.source_provenance_sha256 != remembered.source_provenance_sha256


def test_changed_relevant_value_stales_disposition_even_if_still_missing():
    data = (batch(1, {"name": "one", "alpha": None, "beta": None}),)
    remembered = disposition_from_finding(plan(data).finding)
    changed = (*data, batch(2, {"name": "one", "alpha": " "}))
    assert plan(changed, (remembered,)).finding.field == "alpha"


def test_changed_structure_stales_disposition():
    data = (batch(1, {"name": "one", "alpha": None}),)
    remembered = disposition_from_finding(plan(data).finding)
    assert plan(data, (remembered,), understanding=model(required=False)).finding.field == "alpha"


@pytest.mark.parametrize("change", [{"company": "other"}, {"tenant_id": "other"}, {"field": "outside"}])
def test_disposition_cannot_cross_scope(change):
    data = (batch(1, {"name": "one", "alpha": None}),)
    remembered = replace(disposition_from_finding(plan(data).finding), **change)
    with pytest.raises(ValueError, match="scope"):
        plan(data, (remembered,))


def test_tampered_support_cannot_suppress_matching_digest():
    data = (batch(1, {"name": "one", "alpha": None}),)
    remembered = replace(disposition_from_finding(plan(data).finding), missing_identities=99)
    with pytest.raises(ValueError, match="support"):
        plan(data, (remembered,))


@pytest.mark.parametrize("change", [
    {"execution_allowed": True}, {"promotion_allowed": 0}, {"missing_identities": True},
    {"source_provenance_sha256": "bad"}, {"status": "validated"}, {"extra": "private"},
])
def test_invalid_serialized_dispositions_are_rejected(change):
    finding = plan((batch(1, {"name": "one", "alpha": None}),)).finding
    value = json.loads(disposition_to_json(disposition_from_finding(finding)))
    value.update(change)
    with pytest.raises((TypeError, ValueError)):
        disposition_from_json(json.dumps(value))


def test_mutable_collection_and_duplicate_json_keys_are_rejected():
    data = (batch(1, {"name": "one", "alpha": None}),)
    with pytest.raises(TypeError):
        plan(data, [])
    with pytest.raises(ValueError, match="duplicate"):
        disposition_from_json('{"schema":1,"schema":1}')
