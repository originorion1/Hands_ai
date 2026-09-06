from __future__ import annotations

import json
import sqlite3
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
from urllib.parse import parse_qs, unquote, urlparse

import pytest
from test_erpnext_bounded_trial import (
    KEY_REF,
    NOW,
    SECRET_REF,
    FakeResponse,
    _private_json,
    completed_trial,
    openers,
)

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.discovery.erpnext_learning_comparison import (
    ComparisonInputs,
    LearningComparisonError,
    _ComparisonLedger,
    _ComparisonMetrics,
    _historical_rows,
    _paths,
    _state,
    inspect_learning_comparison_readiness,
    main,
    prepare_comparison_manifest,
    run_learning_comparison,
)
from orion.discovery.erpnext_six_hour_continuation import _continuation_inputs, _ContinuationLedger
from orion.history.evidence import HistoricalEvidenceBatch, HistoricalEvidenceError
from orion.stores.sqlite_checkpoint import SQLiteStudyCheckpointStore
from orion.stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore


def baseline(tmp_path, *, include_trial_identity=False):
    environment, candidate, digest, ledger, scopes, trial_report, trial_digest = completed_trial(tmp_path)
    prior = _ContinuationLedger(_continuation_inputs(
        environment, candidate, digest, trial_report, trial_digest
    ))
    prior.claim()
    for _ in range(7):
        prior.reserve_metadata()
    for _ in range(99):
        prior.reserve_study()
    prior.finish("complete", "cycle_limit")
    report_path = tmp_path / "reports" / "shadow-soak-report-20260906T140002.000000+0000.json"
    payload = json.loads(trial_report.read_bytes())
    payload.update(
        session_started_at=(NOW + timedelta(seconds=1)).isoformat(),
        session_ended_at=(NOW + timedelta(seconds=2)).isoformat(),
        study_get_budget=99, study_gets=99, max_live_gets=106, total_live_gets=106,
        cycles_attempted=99, cycles_completed=99, observations_persisted=303,
        evidence_batches_appended=99, supported_proposal_count=99, distinct_entities_studied=3,
    )
    report_digest = _private_json(report_path, payload)
    inputs = ComparisonInputs(
        environment, candidate, digest, report_path, report_digest,
        "f" * 40, "synthetic-comparison-new-allowance",
    )
    evidence_path, checkpoint_path = _paths(inputs)
    store = SQLiteHistoricalEvidenceStore(evidence_path)
    resources = store.list_resources(tenant_id=inputs.live().tenant_id)
    primary = resources[0]
    original = store.load_all(tenant_id=inputs.live().tenant_id, resource=primary)[0]
    original_fields = set(original.observations[0].evidence.payload["record"]) - {"name", "company"}
    fields = [next(iter(original_fields))]
    fields.append(next(field for field in scopes[primary] if field not in fields))
    others = [entity for entity in scopes if entity != primary][:2]
    for entity, batch_count, row_count in ((primary, 95, 3), (others[0], 2, 5), (others[1], 2, 4)):
        chosen = fields if entity == primary else scopes[entity][:2]
        offset = 1 if entity == primary else 0
        for index in range(batch_count):
            observations = tuple(Observation(Evidence(
                kind=EvidenceKind.API,
                source="synthetic-comparison-baseline",
                tenant_id=inputs.live().tenant_id,
                observed_at=NOW + timedelta(seconds=1),
                payload={"resource": entity, "record": {
                    "name": (
                        original.observations[0].evidence.payload["record"]["name"]
                        if include_trial_identity and entity == primary and index == record == 0
                        else f"retained-identity-{record}"
                    ),
                    "company": "Synthetic Exact Company",
                    **{field: "retained-value" for field in chosen},
                }},
            )) for record in range(row_count))
            store.append(HistoricalEvidenceBatch(
                tenant_id=inputs.live().tenant_id, resource=entity,
                sequence=offset + index + 1, created_at=NOW + timedelta(seconds=1),
                observations=observations,
            ))
    checkpoints = SQLiteStudyCheckpointStore(checkpoint_path)
    latest = checkpoints.load_latest(tenant_id=inputs.live().tenant_id)
    checkpoints.append(replace(latest, sequence=2, created_at=NOW + timedelta(seconds=1)))
    manifest = tmp_path / "reports" / "comparison-baseline.json"
    manifest_digest = prepare_comparison_manifest(inputs, manifest)
    return inputs, manifest, manifest_digest, ledger, scopes


def launch(inputs, manifest, digest, **kwargs):
    return run_learning_comparison(
        inputs, manifest, digest,
        clock=lambda: NOW + timedelta(seconds=3), monotonic=lambda: 0.0, **kwargs,
    )


def test_offline_manifest_and_readiness_preserve_historical_state(tmp_path):
    inputs, manifest, digest, ledger, _ = baseline(tmp_path)
    evidence, checkpoint = _paths(inputs)
    before = {path: path.read_bytes() for path in (ledger, evidence, checkpoint, manifest)}
    offline = replace(inputs, environment={
        key: value for key, value in inputs.environment.items() if key not in {KEY_REF, SECRET_REF}
    })
    report = inspect_learning_comparison_readiness(offline, manifest, digest)
    assert report["status"] == "ready"
    assert report["credentials_available"] is False
    assert report["ready_for_authorized_launch"] is False
    assert report["execution_allowed"] is False
    assert report["new_combined_get_max"] == 27
    assert report["prior_combined_gets"] == 156
    assert report["combined_cumulative_max"] == 183
    retained = json.loads(manifest.read_bytes())["baseline"]
    assert retained["observations"] == 304
    assert retained["fields_covered"] == 6
    assert retained["distinct_identities"] == 13
    assert retained["continuation_repeated_identities"] == 291
    assert before == {path: path.read_bytes() for path in before}


def test_continuation_repeat_reconciliation_is_seeded_by_trial_identity(tmp_path):
    _, manifest, _, _, _ = baseline(tmp_path, include_trial_identity=True)
    retained = json.loads(manifest.read_bytes())["baseline"]
    assert retained["distinct_identities"] == 13
    assert retained["continuation_repeated_identities"] == 291


def test_old_authorization_and_changed_manifest_refuse_before_claim(tmp_path):
    inputs, manifest, digest, ledger, _ = baseline(tmp_path)
    old = replace(inputs, authorization_reference=inputs.environment["ORION_LIVE_AUTHORIZATION_REFERENCE"])
    with pytest.raises(LearningComparisonError, match="fresh comparison"):
        launch(old, manifest, digest)
    with pytest.raises(LearningComparisonError, match="manifest binding"):
        launch(inputs, manifest, "0" * 64)
    assert inspect_learning_comparison_readiness(inputs, manifest, digest)["status"] == "ready"
    with sqlite3.connect(ledger) as connection:
        assert connection.execute(
            "SELECT 1 FROM sqlite_master WHERE name='corrected_selector_comparison'"
        ).fetchone() is None


def test_comparison_claim_is_one_use_and_concurrent(tmp_path):
    inputs, manifest, digest, _, _ = baseline(tmp_path)
    barrier = threading.Barrier(2)

    def claim():
        barrier.wait()
        try:
            _ComparisonLedger(inputs, manifest, digest).claim()
            return "claimed"
        except LearningComparisonError as exc:
            return str(exc)

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = tuple(pool.map(lambda _: claim(), range(2)))
    assert outcomes.count("claimed") == 1
    assert "comparison was already claimed" in outcomes
    assert inspect_learning_comparison_readiness(inputs, manifest, digest)["status"] == "already_claimed"


def test_request_allowance_is_distinct_and_reservations_are_bounded(tmp_path):
    inputs, manifest, digest, ledger_path, _ = baseline(tmp_path)
    with sqlite3.connect(ledger_path) as connection:
        before = _historical_rows(connection)
    ledger = _ComparisonLedger(inputs, manifest, digest)
    ledger.claim()
    for _ in range(7):
        ledger.reserve("metadata")
    for _ in range(20):
        ledger.reserve("study")
    assert ledger.snapshot() == ("running", 7, 20, "none")
    with pytest.raises(LearningComparisonError, match="allowance exhausted"):
        ledger.reserve("study")
    with sqlite3.connect(ledger_path) as connection:
        assert _historical_rows(connection) == before


def test_comparison_retains_learning_and_reports_only_aggregate_new_evidence(tmp_path):
    inputs, manifest, digest, ledger_path, scopes = baseline(tmp_path)
    prior_rows, prior_checkpoints, _, _ = _state(inputs)
    metadata, _, metadata_requests, _ = openers(scopes, expected_limit=5)
    requests = []

    def record(request, *, timeout):
        requests.append(request)
        query = parse_qs(urlparse(request.full_url).query)
        entity = unquote(urlparse(request.full_url).path.rsplit("/", 1)[-1])
        fields = json.loads(query["fields"][0])
        assert entity in scopes
        assert json.loads(query["filters"][0]) == [["company", "=", "Synthetic Exact Company"]]
        assert int(query["limit_page_length"][0]) == 5
        with sqlite3.connect(ledger_path) as connection:
            assert connection.execute(
                "SELECT metadata_gets,study_gets FROM corrected_selector_comparison"
            ).fetchone() == (7, len(requests))
        return FakeResponse(request, {"data": [
            {**dict.fromkeys(fields, "new-private-value"), "name": f"new-private-id-{index}",
             "company": "Synthetic Exact Company"}
            for index in range(5)
        ]})

    result = launch(inputs, manifest, digest, metadata_opener=metadata, record_opener=record)
    assert result["status"] == "complete"
    assert result["new_metadata_gets"] == len(metadata_requests) == 7
    assert result["new_study_gets"] == len(requests) == 20
    assert result["cycles_completed"] == 20
    assert result["comparison"]["observations"] == 100
    assert result["combined_cumulative_gets"] == 183
    assert result["comparison"]["newly_covered_fields"] > 0
    assert len(result["comparison"]["per_study_get"]) == 20
    assert result["comparison"]["new_identities"] + result["comparison"]["repeated_identities"] == 100
    rows, checkpoints, _, _ = _state(inputs)
    keyed = {(row[1], row[2]): row for row in rows}
    assert all(keyed[(row[1], row[2])] == row for row in prior_rows)
    assert checkpoints[:2] == prior_checkpoints
    assert len(checkpoints) == 3
    rendered = json.dumps(result)
    for secret in ("new-private-id", "new-private-value", "Synthetic Exact Company", "Synthetic Entity"):
        assert secret not in rendered
    with pytest.raises(LearningComparisonError):
        launch(inputs, manifest, digest, metadata_opener=metadata, record_opener=record)
    assert len(requests) == 20


@pytest.mark.parametrize("committed", [False, True])
def test_reservation_failure_stops_immediately_without_transport(tmp_path, monkeypatch, committed):
    inputs, manifest, digest, _, scopes = baseline(tmp_path)
    metadata, record, _, requests = openers(scopes, expected_limit=5)
    original = _ComparisonLedger.reserve
    attempts = []

    def reserve(self, kind):
        if kind == "study":
            attempts.append(1)
            if committed:
                original(self, kind)
            raise sqlite3.OperationalError("synthetic reservation storage failure")
        original(self, kind)

    monkeypatch.setattr(_ComparisonLedger, "reserve", reserve)
    with pytest.raises(LearningComparisonError, match="accounting failed"):
        launch(inputs, manifest, digest, metadata_opener=metadata, record_opener=record)
    assert len(attempts) == 1
    assert requests == []
    assert _ComparisonLedger(inputs, manifest, digest).snapshot() == (
        "failed", 7, int(committed), "accounting_or_storage_failure"
    )


@pytest.mark.parametrize("failure", [False, True])
def test_empty_or_transport_failure_stops_after_five_attempts(tmp_path, failure):
    inputs, manifest, digest, _, scopes = baseline(tmp_path)
    metadata, record, _, requests = openers(
        scopes, expected_limit=5, empty_record=not failure, record_failure=failure
    )
    result = launch(inputs, manifest, digest, metadata_opener=metadata, record_opener=record)
    assert result["new_study_gets"] == len(requests) == 5
    assert result["stop_reason"] == ("erp_contract_failure" if failure else "non_progress_limit")
    assert result["comparison"]["observations"] == 0
    assert len(result["comparison"]["per_study_get"]) == 5


def test_charged_metadata_interruption_consumes_claim(tmp_path):
    inputs, manifest, digest, _, scopes = baseline(tmp_path)
    metadata, record, requests, records = openers(scopes, interrupt=True)
    result = launch(inputs, manifest, digest, metadata_opener=metadata, record_opener=record)
    assert result["status"] == "interrupted"
    assert result["new_metadata_gets"] == len(requests) == 1
    assert records == []
    assert inspect_learning_comparison_readiness(inputs, manifest, digest)["status"] == "already_claimed"


@pytest.mark.parametrize("operator_stop", [False, True])
def test_local_metrics_work_rechecks_stop_before_transport(tmp_path, monkeypatch, operator_stop):
    inputs, manifest, digest, _, scopes = baseline(tmp_path)
    metadata, record, _, requests = openers(scopes, expected_limit=5)
    original = _ComparisonMetrics.begin
    stopped = []

    def begin(self, request):
        original(self, request)
        stopped.append(1)

    monkeypatch.setattr(_ComparisonMetrics, "begin", begin)
    result = run_learning_comparison(
        inputs, manifest, digest, metadata_opener=metadata, record_opener=record,
        clock=lambda: NOW + timedelta(seconds=3),
        monotonic=lambda: 901.0 if stopped and not operator_stop else 0.0,
        termination_requested=lambda: bool(stopped) and operator_stop,
    )
    assert requests == []
    assert result["stop_reason"] == ("user_termination" if operator_stop else "duration_limit")
    assert result["new_study_gets"] == 1
    assert result["comparison"]["observations"] == 0


def test_changed_baseline_refuses_before_any_transport(tmp_path):
    inputs, manifest, digest, _, _ = baseline(tmp_path)
    evidence, _ = _paths(inputs)
    with sqlite3.connect(evidence) as connection:
        connection.execute("DELETE FROM orion_historical_evidence WHERE sequence=2")
    with pytest.raises((LearningComparisonError, HistoricalEvidenceError)):
        launch(
            inputs, manifest, digest,
            metadata_opener=lambda *args, **kwargs: pytest.fail("no metadata after changed baseline"),
            record_opener=lambda *args, **kwargs: pytest.fail("no study after changed baseline"),
        )


def test_metadata_reservation_rechecks_deadline_before_transport(tmp_path, monkeypatch):
    inputs, manifest, digest, _, scopes = baseline(tmp_path)
    metadata, record, requests, records = openers(scopes, expected_limit=5)
    original = _ComparisonLedger.reserve
    reserved = []

    def reserve(self, kind):
        original(self, kind)
        reserved.append(kind)

    monkeypatch.setattr(_ComparisonLedger, "reserve", reserve)
    result = run_learning_comparison(
        inputs, manifest, digest, metadata_opener=metadata, record_opener=record,
        clock=lambda: NOW + timedelta(seconds=3),
        monotonic=lambda: 901.0 if reserved else 0.0,
    )
    assert result["stop_reason"] == "duration_limit"
    assert result["new_metadata_gets"] == 1
    assert requests == records == []


def test_cli_defaults_to_offline_readiness(tmp_path, monkeypatch, capsys):
    inputs, manifest, digest, _, _ = baseline(tmp_path)
    for key, value in inputs.environment.items():
        if key not in {KEY_REF, SECRET_REF}:
            monkeypatch.setenv(key, value)
    monkeypatch.delenv(KEY_REF, raising=False)
    monkeypatch.delenv(SECRET_REF, raising=False)
    code = main([
        "--candidate", str(inputs.candidate_path), "--candidate-sha256", inputs.candidate_sha256,
        "--continuation-report", str(inputs.continuation_report_path),
        "--continuation-report-sha256", inputs.continuation_report_sha256,
        "--source-commit", inputs.source_commit,
        "--comparison-authorization-reference", inputs.authorization_reference,
        "--manifest", str(manifest), "--manifest-sha256", digest,
    ])
    assert code == 0
    report = json.loads(capsys.readouterr().out)
    assert report["status"] == "ready"
    assert report["live_requests_performed"] == 0
    assert report["credentials_available"] is False


def test_missing_values_are_captured_but_not_valid_coverage(tmp_path):
    inputs, manifest, digest, _, scopes = baseline(tmp_path)
    metadata, _, _, _ = openers(scopes, expected_limit=5)
    requests = []

    def record(request, *, timeout):
        requests.append(request)
        fields = json.loads(parse_qs(urlparse(request.full_url).query)["fields"][0])
        return FakeResponse(request, {"data": [{
            **dict.fromkeys(fields, None), "name": "missing-value-id", "company": "Synthetic Exact Company"
        }]})

    result = launch(
        inputs, manifest, digest, metadata_opener=metadata, record_opener=record,
        termination_requested=lambda: len(requests) == 1,
    )
    assert result["comparison"]["fields_covered"] == 7
    assert result["comparison"]["valid_fields_covered"] == 6
    assert result["comparison"]["newly_covered_fields"] == 1
    assert result["comparison"]["new_identities"] == 1
