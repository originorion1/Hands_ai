from __future__ import annotations

import hashlib
import json
import sqlite3
import stat
import threading
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
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
        assert int(query["limit_page_length"][0]) == 1
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
