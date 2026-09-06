from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse

import pytest

from orion.discovery.erpnext_bounded_trial import (
    _trial_inputs,
    _TrialLedger,
    bounded_trial_report_json,
    inspect_bounded_trial_readiness,
    main,
    run_bounded_trial,
)
from orion.discovery.erpnext_historical_capture import (
    default_historical_evidence_path,
)
from orion.discovery.erpnext_metadata_preflight import (
    _LEDGER_SCHEMA,
    _admin_binding_digest,
    _ledger_path,
    metadata_preflight_config_from_environment,
)
from orion.discovery.erpnext_metadata_refresh import (
    _REFRESH_STATE_SCHEMA,
    _REFRESH_TARGET_SCHEMA,
    _refresh_candidate_path,
)
from orion.discovery.erpnext_six_hour_continuation import (
    CONTINUATION_COMBINED_REQUESTS,
    CONTINUATION_CYCLES,
    CONTINUATION_METADATA_REQUESTS,
    CONTINUATION_OBSERVATIONS,
    CONTINUATION_SECONDS,
    CONTINUATION_STUDY_REQUESTS,
    CUMULATIVE_COMBINED_REQUESTS,
    EFFECTIVE_ADDITIONAL_OBSERVATIONS,
    METADATA_CUMULATIVE_MAX,
    STUDY_CUMULATIVE_MAX,
    SixHourContinuationError,
    _continuation_inputs,
    _ContinuationLedger,
    inspect_six_hour_continuation_readiness,
    run_six_hour_continuation,
)
from orion.discovery.erpnext_six_hour_continuation import (
    main as continuation_main,
)
from orion.stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore

NOW = datetime(2026, 9, 6, 14, tzinfo=UTC)
KEY_REF = "SYNTHETIC_BOUNDED_TRIAL_KEY"
SECRET_REF = "SYNTHETIC_BOUNDED_TRIAL_SECRET"
FIELD_COUNTS = (12, 5, 8, 23, 17, 9, 59)


class FakeResponse:
    def __init__(self, request, payload):
        self._url = request.full_url
        self._body = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self._url

    def read(self, size=-1):
        return self._body if size < 0 else self._body[:size]


def _private_json(path: Path, payload: object) -> str:
    body = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
    path.write_bytes(body)
    path.chmod(0o600)
    return hashlib.sha256(body).hexdigest()


def prepared(tmp_path: Path):
    state = tmp_path / "state"
    reports = tmp_path / "reports"
    state.mkdir(mode=0o700)
    reports.mkdir(mode=0o700)
    environment = {
        "ORION_LIVE_BASE_URL": "https://synthetic.invalid",
        "ORION_LIVE_TENANT_ID": "synthetic-bounded-tenant",
        "ORION_LIVE_AUTHORIZATION_REFERENCE": "synthetic-bounded-ledger",
        "ORION_LIVE_API_KEY_REF": KEY_REF,
        "ORION_LIVE_API_SECRET_REF": SECRET_REF,
        "ORION_LIVE_STATE_DIR": str(state),
        "ORION_LIVE_REPORT_DIR": str(reports),
        "ORION_LIVE_OBJECTIVE_ID": "synthetic-bounded-objective",
        "ORION_LIVE_OBJECTIVE_DESCRIPTION": "one bounded synthetic observation",
    }
    config = metadata_preflight_config_from_environment(environment)
    binding = _admin_binding_digest(config)
    catalog_digest = "a" * 64
    ledger_path = _ledger_path(config)
    connection = sqlite3.connect(ledger_path)
    try:
        connection.execute(_LEDGER_SCHEMA)
        connection.execute(
            "INSERT INTO preflight_run VALUES "
            "(1, 'complete', 24, 1, 18, 18, 0, 0, 0, 1, 0)"
        )
        connection.execute(
            "CREATE TABLE admin_catalog_claim (digest TEXT NOT NULL, binding TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO admin_catalog_claim VALUES (?, ?)",
            (catalog_digest, binding),
        )
        connection.execute(
            "CREATE TABLE admin_catalog_resume_claim ("
            "catalog_digest TEXT NOT NULL, binding TEXT NOT NULL, "
            "prior_report_digest TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO admin_catalog_resume_claim VALUES (?, ?, ?)",
            (catalog_digest, binding, "b" * 64),
        )
        connection.execute(_REFRESH_STATE_SCHEMA)
        connection.execute(_REFRESH_TARGET_SCHEMA)
        connection.execute(
            "INSERT INTO metadata_filter_refresh VALUES "
            "(1, 'complete', ?, ?, ?, ?, 18, 18, 0, 0, 7, 133)",
            (catalog_digest, binding, "c" * 64, "d" * 64),
        )
        connection.executemany(
            "INSERT INTO metadata_filter_refresh_targets VALUES (?, ?, ?)",
            [
                *((index, "candidate", "none") for index in range(1, 8)),
                *((index, "no_candidate", "scope_incompatible") for index in range(8, 19)),
            ],
        )
        connection.commit()
    finally:
        connection.close()
    ledger_path.chmod(0o600)
    scopes = {
        f"Synthetic Entity {index}": [
            f"safe_field_{index}_{field}" for field in range(count)
        ]
        for index, count in enumerate(FIELD_COUNTS, start=1)
    }
    candidate_path = _refresh_candidate_path(config)
    candidate_digest = _private_json(
        candidate_path,
        {
            "candidate_scopes": scopes,
            "companies": ["Synthetic Exact Company"],
            "company_catalog_complete": True,
            "doctype_catalog_complete": False,
            "review_required": True,
            "schema_version": 1,
        },
    )
    return environment, candidate_path, candidate_digest, ledger_path, scopes


def rows(ledger_path: Path):
    with sqlite3.connect(ledger_path) as connection:
        preflight = connection.execute("SELECT * FROM preflight_run").fetchall()
        refresh = connection.execute("SELECT * FROM metadata_filter_refresh").fetchall()
        targets = connection.execute(
            "SELECT * FROM metadata_filter_refresh_targets ORDER BY target_index"
        ).fetchall()
    return preflight, refresh, targets


def openers(
    scopes,
    *,
    metadata_failure=False,
    record_failure=False,
    empty_record=False,
    interrupt=False,
    expected_limit=1,
):
    metadata_requests = []
    record_requests = []

    def metadata_opener(request, *, timeout):
        metadata_requests.append(request)
        if interrupt:
            raise KeyboardInterrupt
        if metadata_failure:
            raise TimeoutError("synthetic metadata failure")
        doctype = parse_qs(urlparse(request.full_url).query)["doctype"][0]
        fields = [
            {"fieldname": name, "fieldtype": "Data"}
            for name in scopes[doctype]
        ]
        fields.append(
            {"fieldname": "company", "fieldtype": "Link", "options": "Company"}
        )
        return FakeResponse(
            request,
            {
                "message": {
                    "docs": [
                        {
                            "name": doctype,
                            "module": "Synthetic",
                            "is_submittable": 0,
                            "fields": fields,
                        }
                    ]
                }
            },
        )

    def record_opener(request, *, timeout):
        record_requests.append(request)
        if record_failure:
            raise TimeoutError("synthetic record failure")
        query = parse_qs(urlparse(request.full_url).query)
        fields = json.loads(query["fields"][0])
        filters = json.loads(query["filters"][0])
        assert request.get_method() == "GET"
        assert filters == [["company", "=", "Synthetic Exact Company"]]
        assert int(query["limit_page_length"][0]) == expected_limit
        resource = unquote(urlparse(request.full_url).path.rsplit("/", 1)[-1])
        assert resource in scopes
        selected = [name for name in fields if name not in {"name", "company"}]
        assert len(selected) == 1 and selected[0] in scopes[resource]
        row = {name: "synthetic" for name in fields}
        row["name"] = "synthetic-record"
        row["company"] = "Synthetic Exact Company"
        return FakeResponse(request, {"data": [] if empty_record else [row]})

    return metadata_opener, record_opener, metadata_requests, record_requests


def test_readiness_validates_exact_42_request_state_without_mutation(tmp_path):
    environment, candidate, digest, ledger, _ = prepared(tmp_path)
    before = ledger.read_bytes(), candidate.read_bytes(), rows(ledger)

    report = inspect_bounded_trial_readiness(environment, candidate, digest)

    assert report.offline_inputs_ready
    assert not report.credentials_available
    assert not report.ready_for_authorized_launch
    assert report.prior_metadata_requests == 42
    assert report.metadata_request_budget == 7
    assert report.metadata_cumulative_max == 49
    assert report.study_request_budget == 1
    assert report.combined_request_max == 8
    assert report.max_wall_clock_seconds == 300
    assert report.max_study_cycles == report.max_observations == 1
    assert report.live_requests_performed == report.erp_writes == 0
    assert before == (ledger.read_bytes(), candidate.read_bytes(), rows(ledger))


def test_one_cycle_trial_charges_separate_budgets_and_cannot_replay(tmp_path):
    environment, candidate, digest, ledger, scopes = prepared(tmp_path)
    environment |= {KEY_REF: "synthetic-key", SECRET_REF: "synthetic-secret"}
    metadata, record, metadata_requests, record_requests = openers(scopes)
    original_rows = rows(ledger)

    report = run_bounded_trial(
        environment,
        candidate,
        digest,
        metadata_opener=metadata,
        record_opener=record,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    assert report.status == "complete"
    assert report.stop_reason == "cycle_limit"
    assert report.metadata_requests == len(metadata_requests) == 7
    assert report.metadata_cumulative_requests == 49
    assert report.study_requests == len(record_requests) == 1
    assert report.combined_requests == report.combined_request_max == 8
    assert report.cycles_attempted == report.cycles_completed == 1
    assert report.observations_persisted == 1
    assert report.erp_writes == 0
    assert rows(ledger) == original_rows
    with sqlite3.connect(ledger) as connection:
        accounting = connection.execute(
            "SELECT status, prior_metadata_requests, metadata_requests, "
            "study_requests, stop_reason FROM bounded_readonly_trial"
        ).fetchone()
    assert accounting == ("complete", 42, 7, 1, "cycle_limit")
    with pytest.raises(Exception, match="already claimed"):
        run_bounded_trial(
            environment,
            candidate,
            digest,
            metadata_opener=lambda *args, **kwargs: pytest.fail("unexpected metadata"),
            record_opener=lambda *args, **kwargs: pytest.fail("unexpected record"),
        )


def test_metadata_failure_is_charged_and_stops_before_study(tmp_path):
    environment, candidate, digest, ledger, scopes = prepared(tmp_path)
    environment |= {KEY_REF: "synthetic-key", SECRET_REF: "synthetic-secret"}
    metadata, record, metadata_requests, record_requests = openers(
        scopes, metadata_failure=True
    )

    report = run_bounded_trial(
        environment,
        candidate,
        digest,
        metadata_opener=metadata,
        record_opener=record,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    assert report.status == "failed"
    assert report.stop_reason == "metadata_preflight_failure"
    assert report.metadata_requests == len(metadata_requests) == 1
    assert report.metadata_cumulative_requests == 43
    assert report.study_requests == report.combined_requests - 1 == 0
    assert record_requests == []
    with sqlite3.connect(ledger) as connection:
        assert connection.execute(
            "SELECT status, metadata_requests, study_requests "
            "FROM bounded_readonly_trial"
        ).fetchone() == ("failed", 1, 0)


def test_first_study_failure_is_charged_and_stops(tmp_path):
    environment, candidate, digest, ledger, scopes = prepared(tmp_path)
    environment |= {KEY_REF: "synthetic-key", SECRET_REF: "synthetic-secret"}
    metadata, record, metadata_requests, record_requests = openers(
        scopes, record_failure=True
    )

    report = run_bounded_trial(
        environment,
        candidate,
        digest,
        metadata_opener=metadata,
        record_opener=record,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    assert report.status == "failed"
    assert report.stop_reason == "erp_contract_failure"
    assert report.metadata_requests == len(metadata_requests) == 7
    assert report.study_requests == len(record_requests) == 1
    assert report.combined_requests == 8
    assert report.cycles_attempted == 1
    assert report.cycles_completed == report.observations_persisted == 0
    with sqlite3.connect(ledger) as connection:
        assert connection.execute(
            "SELECT status, metadata_requests, study_requests "
            "FROM bounded_readonly_trial"
        ).fetchone() == ("failed", 7, 1)


def test_first_non_progress_outcome_stops(tmp_path):
    environment, candidate, digest, _, scopes = prepared(tmp_path)
    environment |= {KEY_REF: "synthetic-key", SECRET_REF: "synthetic-secret"}
    metadata, record, _, record_requests = openers(scopes, empty_record=True)

    report = run_bounded_trial(
        environment,
        candidate,
        digest,
        metadata_opener=metadata,
        record_opener=record,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    assert report.status == "failed"
    assert report.stop_reason == "non_progress_limit"
    assert report.study_requests == len(record_requests) == 1
    assert report.cycles_attempted == 1
    assert report.cycles_completed == report.observations_persisted == 0


def test_interrupted_attempt_is_charged_and_not_replayable(tmp_path):
    environment, candidate, digest, _, scopes = prepared(tmp_path)
    environment |= {KEY_REF: "synthetic-key", SECRET_REF: "synthetic-secret"}
    metadata, record, metadata_requests, record_requests = openers(scopes, interrupt=True)

    report = run_bounded_trial(
        environment,
        candidate,
        digest,
        metadata_opener=metadata,
        record_opener=record,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    assert report.status == "interrupted"
    assert report.stop_reason == "user_termination"
    assert report.metadata_requests == len(metadata_requests) == 1
    assert report.study_requests == 0
    assert record_requests == []
    readiness = inspect_bounded_trial_readiness(environment, candidate, digest)
    assert readiness.status == "already_claimed"
    assert not readiness.offline_inputs_ready


def test_wrong_candidate_digest_refuses_without_ledger_change(tmp_path):
    environment, candidate, _, ledger, _ = prepared(tmp_path)
    before = ledger.read_bytes()

    report = inspect_bounded_trial_readiness(environment, candidate, "0" * 64)

    assert report.status == "invalid"
    assert not report.offline_inputs_ready
    assert ledger.read_bytes() == before


def test_concurrent_claim_has_exactly_one_winner(tmp_path):
    environment, candidate, digest, _, _ = prepared(tmp_path)
    barrier = threading.Barrier(2)

    def claim():
        trial_ledger = _TrialLedger(_trial_inputs(environment, candidate, digest))
        barrier.wait()
        try:
            trial_ledger.claim()
        except Exception as exc:  # noqa: BLE001 - outcome is asserted below
            return type(exc).__name__, str(exc)
        return "claimed", ""

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(lambda _: claim(), range(2)))

    assert sum(kind == "claimed" for kind, _ in outcomes) == 1
    refused = [message for kind, message in outcomes if kind != "claimed"]
    assert len(refused) == 1 and "already claimed" in refused[0]


def test_cli_defaults_to_offline_readiness(tmp_path, monkeypatch, capsys):
    environment, candidate, digest, ledger, _ = prepared(tmp_path)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    assert main(["--candidate", str(candidate), "--candidate-sha256", digest]) == 0

    report = json.loads(capsys.readouterr().out)
    assert report["offline_inputs_ready"] is True
    assert report["ready_for_authorized_launch"] is False
    assert report["execution_allowed"] is False
    assert report["combined_request_max"] == 8
    with sqlite3.connect(ledger) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='bounded_readonly_trial'"
        ).fetchone() is None
    rendered = bounded_trial_report_json(
        inspect_bounded_trial_readiness(environment, candidate, digest)
    )
    assert "Synthetic" not in rendered
    assert stat.S_IMODE(candidate.stat().st_mode) == 0o600


def completed_trial(tmp_path: Path):
    environment, candidate, digest, ledger, scopes = prepared(tmp_path)
    credentialed = environment | {
        KEY_REF: "synthetic-key",
        SECRET_REF: "synthetic-secret",
    }
    metadata, record, _, _ = openers(scopes)
    result = run_bounded_trial(
        credentialed,
        candidate,
        digest,
        metadata_opener=metadata,
        record_opener=record,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )
    assert result.status == "complete"
    reports = tuple((tmp_path / "reports").glob("shadow-soak-report-*.json"))
    assert len(reports) == 1
    report = reports[0]
    report_digest = hashlib.sha256(report.read_bytes()).hexdigest()
    return credentialed, candidate, digest, ledger, scopes, report, report_digest


def test_six_hour_readiness_uses_only_residual_budgets_without_mutation(tmp_path):
    environment, candidate, digest, ledger, _, report, report_digest = completed_trial(
        tmp_path
    )
    environment.pop(KEY_REF)
    environment.pop(SECRET_REF)
    before = ledger.read_bytes(), candidate.read_bytes(), report.read_bytes()

    readiness = inspect_six_hour_continuation_readiness(
        environment, candidate, digest, report, report_digest
    )
    inputs = _continuation_inputs(
        environment, candidate, digest, report, report_digest
    )

    assert readiness.offline_inputs_ready
    assert not readiness.credentials_available
    assert not readiness.ready_for_authorized_launch
    assert readiness.prior_metadata_requests == 49
    assert readiness.additional_metadata_get_budget == CONTINUATION_METADATA_REQUESTS == 7
    assert readiness.metadata_cumulative_max == METADATA_CUMULATIVE_MAX == 56
    assert readiness.prior_study_requests == 1
    assert readiness.additional_study_get_budget == CONTINUATION_STUDY_REQUESTS == 99
    assert readiness.study_cumulative_max == STUDY_CUMULATIVE_MAX == 100
    assert readiness.additional_combined_get_max == CONTINUATION_COMBINED_REQUESTS == 106
    assert readiness.cumulative_combined_get_max == CUMULATIVE_COMBINED_REQUESTS == 156
    assert readiness.max_wall_clock_seconds == CONTINUATION_SECONDS == 21600
    assert readiness.prior_cycles == 1
    assert readiness.additional_cycle_budget == CONTINUATION_CYCLES == 99
    assert readiness.cycle_cumulative_max == 100
    assert readiness.prior_observations == 1
    assert readiness.additional_observation_budget == CONTINUATION_OBSERVATIONS == 499
    assert readiness.observation_cumulative_max == 500
    assert readiness.effective_additional_observation_max == 495
    assert readiness.effective_cumulative_observation_max == 496
    assert EFFECTIVE_ADDITIONAL_OBSERVATIONS == 495
    assert inputs.live.limits.max_observations_per_study == 5
    assert inputs.live.limits.max_consecutive_non_progress == 5
    assert readiness.live_requests_performed == readiness.erp_writes == 0
    assert before == (ledger.read_bytes(), candidate.read_bytes(), report.read_bytes())
    with sqlite3.connect(ledger) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='six_hour_continuation'"
        ).fetchone() is None


def test_continuation_ledger_refuses_request_100_and_replay(tmp_path):
    environment, candidate, digest, _, _, report, report_digest = completed_trial(tmp_path)
    inputs = _continuation_inputs(
        environment, candidate, digest, report, report_digest
    )
    ledger = _ContinuationLedger(inputs)
    ledger.claim()
    for _ in range(7):
        ledger.reserve_metadata()
    for _ in range(99):
        ledger.reserve_study()

    assert ledger.snapshot() == ("running", 7, 99, "none")
    with pytest.raises(Exception, match="study budget"):
        ledger.reserve_study()
    with pytest.raises(Exception, match="already claimed"):
        _ContinuationLedger(inputs).claim()


def test_six_hour_continuation_concurrent_claim_has_one_winner(tmp_path):
    environment, candidate, digest, _, _, report, report_digest = completed_trial(
        tmp_path
    )
    inputs = _continuation_inputs(
        environment, candidate, digest, report, report_digest
    )
    barrier = threading.Barrier(2)

    def claim():
        barrier.wait()
        try:
            _ContinuationLedger(inputs).claim()
        except Exception as exc:  # noqa: BLE001 - asserted fixed outcome
            return type(exc).__name__, str(exc)
        return "claimed", ""

    with ThreadPoolExecutor(max_workers=2) as executor:
        outcomes = tuple(executor.map(lambda _: claim(), range(2)))

    assert sum(kind == "claimed" for kind, _ in outcomes) == 1
    refused = [message for kind, message in outcomes if kind != "claimed"]
    assert len(refused) == 1 and "already claimed" in refused[0]


def test_continuation_metadata_failure_is_charged_and_consumes_claim(tmp_path):
    environment, candidate, digest, ledger, scopes, report, report_digest = (
        completed_trial(tmp_path)
    )
    metadata, record, metadata_requests, record_requests = openers(
        scopes, metadata_failure=True, expected_limit=5
    )

    result = run_six_hour_continuation(
        environment,
        candidate,
        digest,
        report,
        report_digest,
        metadata_opener=metadata,
        record_opener=record,
        clock=lambda: NOW + timedelta(seconds=1),
        monotonic=lambda: 0.0,
    )

    assert result.status == "failed"
    assert result.stop_reason == "metadata_preflight_failure"
    assert result.additional_metadata_gets == len(metadata_requests) == 1
    assert result.metadata_cumulative_requests == 50
    assert result.additional_study_gets == 0
    assert record_requests == []
    with sqlite3.connect(ledger) as connection:
        assert connection.execute(
            "SELECT status, metadata_requests, study_requests "
            "FROM six_hour_continuation"
        ).fetchone() == ("failed", 1, 0)
    assert inspect_six_hour_continuation_readiness(
        environment, candidate, digest, report, report_digest
    ).status == "already_claimed"


@pytest.mark.parametrize("reservation_committed", [False, True])
def test_continuation_reservation_failure_stops_before_any_transport(
    tmp_path, monkeypatch, reservation_committed
):
    environment, candidate, digest, ledger, scopes, report, report_digest = (
        completed_trial(tmp_path)
    )
    metadata, record, _, record_requests = openers(
        scopes, empty_record=True, expected_limit=5
    )
    original = _ContinuationLedger.reserve_study
    reservation_attempts = []

    def fail_once(self):
        reservation_attempts.append(1)
        if len(reservation_attempts) == 1:
            if reservation_committed:
                original(self)
            raise sqlite3.OperationalError("synthetic accounting storage failure")
        return original(self)

    monkeypatch.setattr(_ContinuationLedger, "reserve_study", fail_once)

    def run():
        return run_six_hour_continuation(
            environment,
            candidate,
            digest,
            report,
            report_digest,
            metadata_opener=metadata,
            record_opener=record,
            clock=lambda: NOW + timedelta(seconds=1),
            monotonic=lambda: 0.0,
        )

    if reservation_committed:
        result = run()
        assert result.status == "failed"
        assert result.stop_reason == "persistence_failure"
        assert result.additional_study_gets == 1
        assert result.cycles_attempted == 1
        assert result.cycles_completed == result.observations_persisted == 0
        stop_reason = "persistence_failure"
    else:
        with pytest.raises(SixHourContinuationError, match="accounting mismatch"):
            run()
        stop_reason = "accounting_mismatch"

    assert len(reservation_attempts) == 1
    assert record_requests == []
    with sqlite3.connect(ledger) as connection:
        assert connection.execute(
            "SELECT status, metadata_requests, study_requests, stop_reason "
            "FROM six_hour_continuation"
        ).fetchone() == ("failed", 7, int(reservation_committed), stop_reason)


def test_continuation_allows_five_genuine_transport_failures(tmp_path):
    environment, candidate, digest, _, scopes, report, report_digest = (
        completed_trial(tmp_path)
    )
    metadata, record, _, record_requests = openers(
        scopes, record_failure=True, expected_limit=5
    )

    result = run_six_hour_continuation(
        environment,
        candidate,
        digest,
        report,
        report_digest,
        metadata_opener=metadata,
        record_opener=record,
        clock=lambda: NOW + timedelta(seconds=1),
        monotonic=lambda: 0.0,
    )

    assert result.status == "failed"
    assert result.stop_reason == "erp_contract_failure"
    assert result.additional_study_gets == len(record_requests) == 5
    assert result.cycles_attempted == 5
    assert result.cycles_completed == result.observations_persisted == 0


def test_continuation_stops_after_five_non_progress_reads_and_preserves_evidence(
    tmp_path,
):
    environment, candidate, digest, ledger, scopes, report, report_digest = (
        completed_trial(tmp_path)
    )
    metadata, record, metadata_requests, record_requests = openers(
        scopes, empty_record=True, expected_limit=5
    )
    config = metadata_preflight_config_from_environment(environment)
    evidence_path = default_historical_evidence_path(
        config.tenant_id,
        resource="live-shadow-soak",
        state_root=config.state_directory,
    )
    before = evidence_path.read_bytes()

    result = run_six_hour_continuation(
        environment,
        candidate,
        digest,
        report,
        report_digest,
        metadata_opener=metadata,
        record_opener=record,
        clock=lambda: NOW + timedelta(seconds=1),
        monotonic=lambda: 0.0,
    )

    assert result.status == "complete"
    assert result.stop_reason == "non_progress_limit"
    assert result.additional_metadata_gets == len(metadata_requests) == 7
    assert result.metadata_cumulative_requests == 56
    assert result.additional_study_gets == len(record_requests) == 5
    assert result.study_cumulative_requests == 6
    assert result.additional_combined_gets == 12
    assert result.cumulative_combined_gets == 62
    assert result.cycles_attempted == 5
    assert result.cycles_completed == result.observations_persisted == 0
    assert result.erp_writes == 0
    assert evidence_path.read_bytes() == before
    with sqlite3.connect(ledger) as connection:
        assert connection.execute(
            "SELECT status, metadata_requests, study_requests, stop_reason "
            "FROM six_hour_continuation"
        ).fetchone() == ("complete", 7, 5, "non_progress_limit")


def test_continuation_appends_after_trial_and_interruption_stops_next_read(tmp_path):
    environment, candidate, digest, _, scopes, report, report_digest = completed_trial(
        tmp_path
    )
    metadata, record, _, record_requests = openers(scopes, expected_limit=5)
    config = metadata_preflight_config_from_environment(environment)
    evidence_path = default_historical_evidence_path(
        config.tenant_id,
        resource="live-shadow-soak",
        state_root=config.state_directory,
    )

    result = run_six_hour_continuation(
        environment,
        candidate,
        digest,
        report,
        report_digest,
        metadata_opener=metadata,
        record_opener=record,
        clock=lambda: NOW + timedelta(seconds=1),
        monotonic=lambda: 0.0,
        termination_requested=lambda: len(record_requests) == 1,
    )

    assert result.status == "interrupted"
    assert result.stop_reason == "user_termination"
    assert result.additional_study_gets == len(record_requests) == 1
    assert result.cycles_completed == result.observations_persisted == 1
    store = SQLiteHistoricalEvidenceStore(evidence_path)
    resources = store.list_resources(tenant_id=config.tenant_id)
    batches = tuple(
        batch
        for resource in resources
        for batch in store.load_all(tenant_id=config.tenant_id, resource=resource)
    )
    assert len(batches) == 2
    assert sum(len(batch.observations) for batch in batches) == 2


def test_six_hour_readiness_rejects_changed_report_and_candidate(tmp_path):
    environment, candidate, digest, ledger, _, report, report_digest = completed_trial(
        tmp_path
    )
    before = ledger.read_bytes()

    wrong_report = inspect_six_hour_continuation_readiness(
        environment, candidate, digest, report, "0" * 64
    )
    wrong_candidate = inspect_six_hour_continuation_readiness(
        environment, candidate, "0" * 64, report, report_digest
    )

    assert wrong_report.status == wrong_candidate.status == "invalid"
    assert ledger.read_bytes() == before


def test_six_hour_cli_defaults_to_offline_readiness(tmp_path, monkeypatch, capsys):
    environment, candidate, digest, ledger, _, report, report_digest = completed_trial(
        tmp_path
    )
    environment.pop(KEY_REF)
    environment.pop(SECRET_REF)
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    result = continuation_main(
        [
            "--candidate",
            str(candidate),
            "--candidate-sha256",
            digest,
            "--trial-report",
            str(report),
            "--trial-report-sha256",
            report_digest,
        ]
    )

    assert result == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["offline_inputs_ready"] is True
    assert payload["execution_allowed"] is False
    assert payload["additional_combined_get_max"] == 106
    with sqlite3.connect(ledger) as connection:
        assert connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='six_hour_continuation'"
        ).fetchone() is None
