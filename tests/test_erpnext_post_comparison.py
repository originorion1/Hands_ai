from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from datetime import timedelta

import pytest
from test_erpnext_bounded_trial import NOW, _private_json, openers
from test_erpnext_learning_comparison import baseline

from orion.discovery import erpnext_learning_comparison as comparison
from orion.discovery.erpnext_post_comparison import (
    POLICY,
    PostComparisonInputs,
    inspect_post_comparison_readiness,
    prepare_post_comparison_manifest,
    run_post_comparison,
)
from orion.stores.sqlite_checkpoint import SQLiteStudyCheckpointStore
from orion.stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore


def completed_comparison(tmp_path):
    old, manifest, digest, ledger_path, scopes = baseline(tmp_path)
    claim = comparison._ComparisonLedger(old, manifest, digest)
    claim.claim()
    for _ in range(7):
        claim.reserve("metadata")
    for _ in range(20):
        claim.reserve("study")
    claim.finish("complete", "cycle_limit")
    evidence, checkpoint = comparison._paths(old)
    store = SQLiteHistoricalEvidenceStore(evidence)
    entity = old.live().reviewed_scopes[2].entity
    previous = store.load_all(tenant_id=old.live().tenant_id, resource=entity)[-1]
    for index in range(20):
        store.append(replace(previous, sequence=previous.sequence + index + 1,
                             created_at=NOW + timedelta(seconds=3),
                             observations=previous.observations[:3]))
    checkpoints = SQLiteStudyCheckpointStore(checkpoint)
    latest = checkpoints.load_latest(tenant_id=old.live().tenant_id)
    checkpoints.append(replace(latest, sequence=3, created_at=NOW + timedelta(seconds=3)))
    report = {
        "status": "complete", "stop_reason": "cycle_limit", "source_commit": old.source_commit,
        "baseline_manifest_sha256": digest, "prior_metadata_gets": 56,
        "prior_study_gets": 100, "prior_combined_gets": 156, "new_metadata_gets": 7,
        "new_study_gets": 20, "new_combined_gets": 27, "metadata_cumulative_gets": 63,
        "study_cumulative_gets": 120, "combined_cumulative_gets": 183,
        "cycles_attempted": 20, "cycles_completed": 20, "execution_allowed": False,
        "recommendation_allowed": False, "promotion_allowed": False, "erp_writes": 0,
        "descriptive_only": True, "baseline": json.loads(manifest.read_bytes())["baseline"],
        "comparison": {
            "observations": 60, "entities_covered": 3, "fields_covered": 6,
            "newly_covered_fields": 0, "new_identities": 0, "repeated_identities": 60,
            "study_distribution": [{"entity_index": 3, "study_gets": 20}],
            "per_study_get": [{"study_get": index, "entity_index": 3, "observations": 3,
                               "new_identities": 0, "repeated_identities": 3,
                               "newly_covered_fields": 0} for index in range(1, 21)],
        },
    }
    report_path = tmp_path / "reports" / "old-comparison-report.json"
    report_digest = _private_json(report_path, report)
    inputs = PostComparisonInputs(
        old.environment, old.candidate_path, old.candidate_sha256, old.continuation_report_path,
        old.continuation_report_sha256, "e" * 40, "new-post-comparison-authorization",
        manifest, digest, report_path, report_digest,
    )
    new_manifest = tmp_path / "reports" / "post-baseline.json"
    new_digest = prepare_post_comparison_manifest(inputs, new_manifest)
    return inputs, new_manifest, new_digest, ledger_path, scopes


def test_offline_post_baseline_and_cumulative_limits(tmp_path):
    inputs, manifest, digest, ledger, _ = completed_comparison(tmp_path)
    before = ledger.read_bytes()
    report = inspect_post_comparison_readiness(inputs, manifest, digest)
    assert report["status"] == "ready"
    assert report["prior_combined_gets"] == 183
    assert report["combined_cumulative_max"] == 210
    assert report["metadata_cumulative_max"] == 70
    assert report["study_cumulative_max"] == 140
    assert json.loads(manifest.read_bytes())["baseline"]["observations"] == 364
    assert ledger.read_bytes() == before


def test_fresh_claim_preserves_original_rows_and_is_single_use(tmp_path):
    inputs, manifest, digest, ledger_path, _ = completed_comparison(tmp_path)
    with sqlite3.connect(ledger_path) as connection:
        before = POLICY.historical_rows(connection)
    ledger = comparison._ComparisonLedger(inputs, manifest, digest, policy=POLICY)
    ledger.claim()
    for _ in range(7):
        ledger.reserve("metadata")
    for _ in range(20):
        ledger.reserve("study")
    with pytest.raises(comparison.LearningComparisonError):
        ledger.reserve("study")
    ledger.finish("complete", "cycle_limit")
    with pytest.raises(comparison.LearningComparisonError):
        ledger.claim()
    with sqlite3.connect(ledger_path) as connection:
        assert POLICY.historical_rows(connection) == before


def test_old_authorization_or_artifact_tampering_refuses(tmp_path):
    inputs, manifest, digest, _, _ = completed_comparison(tmp_path)
    reused = replace(inputs, authorization_reference="synthetic-comparison-new-allowance")
    assert inspect_post_comparison_readiness(reused, manifest, digest)["status"] == "invalid"
    inputs.prior_comparison_report_path.write_text("{}")
    assert inspect_post_comparison_readiness(inputs, manifest, digest)["status"] == "invalid"


def test_actual_runner_preserves_history_and_uses_fresh_budgets(tmp_path):
    inputs, manifest, digest, ledger, scopes = completed_comparison(tmp_path)
    metadata, records, _, _ = openers(scopes, expected_limit=5)
    with sqlite3.connect(ledger) as connection:
        before = POLICY.historical_rows(connection)
    result = run_post_comparison(
        inputs, manifest, digest, metadata_opener=metadata, record_opener=records,
        clock=lambda: NOW + timedelta(seconds=4), monotonic=lambda: 0.0,
    )
    assert result["status"] == "complete"
    assert result["new_study_gets"] <= 20
    assert result["metadata_cumulative_gets"] == 70
    assert result["study_cumulative_gets"] == 120 + result["new_study_gets"]
    assert result["comparison"]["observations"] <= 100
    assert result["execution_allowed"] is False
    with sqlite3.connect(ledger) as connection:
        assert POLICY.historical_rows(connection) == before

@pytest.mark.parametrize("failure", [False, True])
def test_post_comparison_five_nonprogress_reads_stop(tmp_path, failure):
    inputs, manifest, digest, _, scopes = completed_comparison(tmp_path)
    metadata, record, _, requests = openers(
        scopes, expected_limit=5, empty_record=not failure, record_failure=failure,
    )
    result = run_post_comparison(
        inputs, manifest, digest, metadata_opener=metadata, record_opener=record,
        clock=lambda: NOW + timedelta(seconds=4), monotonic=lambda: 0.0,
    )
    assert result["new_study_gets"] == len(requests) == 5
    assert result["stop_reason"] == ("erp_contract_failure" if failure else "non_progress_limit")
    assert result["comparison"]["changed_value_observations"] == 0


def test_post_comparison_interruption_consumes_only_new_claim(tmp_path):
    inputs, manifest, digest, ledger, scopes = completed_comparison(tmp_path)
    with sqlite3.connect(ledger) as connection:
        before = POLICY.historical_rows(connection)
    metadata, record, requests, records = openers(scopes, interrupt=True)
    result = run_post_comparison(
        inputs, manifest, digest, metadata_opener=metadata, record_opener=record,
        clock=lambda: NOW + timedelta(seconds=4), monotonic=lambda: 0.0,
    )
    assert result["status"] == "interrupted"
    assert result["new_metadata_gets"] == len(requests) == 1
    assert records == []
    assert inspect_post_comparison_readiness(inputs, manifest, digest)["status"] == "already_claimed"
    with sqlite3.connect(ledger) as connection:
        assert POLICY.historical_rows(connection) == before


def test_post_comparison_concurrent_claims_have_one_winner(tmp_path):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    inputs, manifest, digest, _, _ = completed_comparison(tmp_path)
    barrier = threading.Barrier(2)

    def claim(_):
        barrier.wait()
        try:
            comparison._ComparisonLedger(inputs, manifest, digest, policy=POLICY).claim()
            return True
        except comparison.LearningComparisonError:
            return False

    with ThreadPoolExecutor(max_workers=2) as pool:
        assert sum(pool.map(claim, range(2))) == 1


def test_changed_values_are_distinct_from_repeated_identity_metrics(tmp_path):
    from urllib.parse import urlencode
    from urllib.request import Request

    from orion.discovery.erpnext_post_comparison import _PostMetrics

    inputs, manifest, _, _, _ = completed_comparison(tmp_path)
    metrics = _PostMetrics(inputs, json.loads(manifest.read_bytes()))
    evidence, _ = comparison._paths(inputs)
    store = SQLiteHistoricalEvidenceStore(evidence)
    entity = inputs.live().reviewed_scopes[2].entity
    previous = store.load_all(tenant_id=inputs.live().tenant_id, resource=entity)[-1]
    observation = previous.observations[0]
    record = dict(observation.evidence.payload["record"])
    field = next(key for key in record if key not in {"name", "company", "docstatus"})
    record[field] = "changed-authorized-value"
    request = Request("https://synthetic.invalid/api/resource/" + entity + "?" + urlencode({
        "fields": json.dumps(list(record)),
    }))
    metrics.begin(request)
    store.append(replace(
        previous, sequence=previous.sequence + 1, created_at=NOW + timedelta(seconds=4),
        observations=(replace(observation, evidence=replace(
            observation.evidence, payload={"resource": entity, "record": record},
        )),),
    ))
    report = metrics.report()
    assert report["changed_value_observations"] == 1
    assert report["repeated_identities"] == 1
    assert report["new_identities"] == report["newly_covered_fields"] == 0

    # Returning to the earlier value is still a change from the latest value.
    metrics.begin(request)
    store.append(replace(
        previous, sequence=previous.sequence + 2, created_at=NOW + timedelta(seconds=5),
        observations=(observation,),
    ))
    reverted = metrics.report()
    assert reverted["changed_value_observations"] == 2
    assert reverted["per_study_get"][-1]["changed_value_observations"] == 1
