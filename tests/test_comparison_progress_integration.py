"""Exercise durable progress policy through the actual comparison entrypoint."""

import json
from urllib.parse import parse_qs, unquote, urlparse

from test_erpnext_bounded_trial import FakeResponse, openers
from test_erpnext_learning_comparison import _state, baseline, launch


def test_comparison_stops_after_five_reads_without_new_authorized_evidence(tmp_path):
    inputs, manifest, digest, _, scopes = baseline(tmp_path)
    _, _, batches, _ = _state(inputs)
    identities = {
        batch.resource: batch.observations[0].evidence.payload["record"]["name"]
        for batch in batches
    }
    metadata, _, _, _ = openers(scopes, expected_limit=5)
    reads = []

    def reader(request, *, timeout):
        entity = unquote(urlparse(request.full_url).path.rsplit("/", 1)[-1])
        reads.append(entity)
        # Audit-only repeats do not invent values for unobserved study fields.
        records = [] if entity not in identities else [{
            "name": identities[entity], "company": "Synthetic Exact Company",
        }]
        return FakeResponse(request, {"data": records})

    result = launch(inputs, manifest, digest, metadata_opener=metadata, record_opener=reader)
    assert len(reads) == result["new_study_gets"] == 5
    assert result["stop_reason"] == "non_progress_limit"
    assert result["comparison"]["new_identities"] == 0
    assert result["comparison"]["newly_covered_fields"] == 0
    assert result["combined_cumulative_gets"] == 168
    assert not result["execution_allowed"]


def test_comparison_accepts_changed_values_on_retained_identities_as_progress(tmp_path):
    inputs, manifest, digest, _, scopes = baseline(tmp_path)
    _, _, batches, _ = _state(inputs)
    identities = {
        batch.resource: batch.observations[0].evidence.payload["record"]["name"]
        for batch in batches
    }
    metadata, _, _, _ = openers(scopes, expected_limit=5)
    reads = []

    def reader(request, *, timeout):
        entity = unquote(urlparse(request.full_url).path.rsplit("/", 1)[-1])
        reads.append(entity)
        fields = json.loads(parse_qs(urlparse(request.full_url).query)["fields"][0])
        record = dict.fromkeys(fields, f"synthetic-changing-value-{len(reads)}")
        record.update(name=identities.get(entity, "synthetic-first-identity"),
                      company="Synthetic Exact Company")
        return FakeResponse(request, {"data": [record]})

    result = launch(inputs, manifest, digest, metadata_opener=metadata, record_opener=reader)
    assert result["stop_reason"] == "cycle_limit"
    assert len(reads) == result["new_study_gets"] == 20
    assert result["comparison"]["observations"] == 20
    assert result["comparison"]["repeated_identities"] > 0
    assert not result["execution_allowed"]
