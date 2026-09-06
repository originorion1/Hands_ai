import hashlib
import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from urllib.parse import parse_qs, unquote, urlparse

import pytest

import orion.discovery.erpnext_live_session as live_session_module
from orion.contracts import Evidence, EvidenceKind, Observation
from orion.discovery.erpnext_historical_capture import default_historical_evidence_path
from orion.discovery.erpnext_live_session import (
    CredentialEnvironmentReferences,
    ERPNextCompanyAuthorization,
    ERPNextLiveSessionConfig,
    LiveSessionError,
    LiveSessionLimits,
    LiveSessionStorageError,
    ReviewedMetadataScope,
    inspect_live_session_readiness,
    live_session_config_from_environment,
    live_session_report_json,
    run_erpnext_live_session,
)
from orion.history.evidence import HistoricalEvidenceBatch
from orion.learning.autonomous_loop import LearningObjective
from orion.stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)
TENANT = "synthetic-tenant-private"
COMPANIES = ("synthetic-company-a", "synthetic-company-b")
KEY_REF = "SYNTHETIC_ERP_KEY"
SECRET_REF = "SYNTHETIC_ERP_SECRET"
SECRET_ENVIRONMENT = {
    KEY_REF: "synthetic-key-value-not-for-output",
    SECRET_REF: "synthetic-secret-value-not-for-output",
}


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


def metadata_doc(entity, *field_names, submitted=False, hidden=()):
    fields = [
        {
            "fieldname": name,
            "fieldtype": "Data",
            "reqd": 1,
            "hidden": 1 if name in hidden else 0,
        }
        for name in field_names
    ]
    fields.append({"fieldname": "company", "fieldtype": "Link", "options": "Company"})
    return {
        "name": entity,
        "module": "Synthetic",
        "is_submittable": 1 if submitted else 0,
        "fields": fields,
    }


def config(tmp_path, *, limits=None, companies=COMPANIES, scopes=None):
    state = tmp_path / "state"
    reports = tmp_path / "reports"
    state.mkdir(mode=0o700)
    reports.mkdir(mode=0o700)
    reviewed = scopes or (
        ReviewedMetadataScope("Synthetic Alpha", ("alpha_value", "alpha_note")),
        ReviewedMetadataScope("Synthetic Beta", ("beta_value",)),
    )
    return ERPNextLiveSessionConfig(
        base_url="https://synthetic.invalid",
        tenant_id=TENANT,
        authorization_reference="synthetic-ledger-reference",
        company_authorization=ERPNextCompanyAuthorization(TENANT, companies),
        reviewed_scopes=reviewed,
        credential_references=CredentialEnvironmentReferences(KEY_REF, SECRET_REF),
        state_directory=state,
        report_directory=reports,
        objective=LearningObjective("synthetic-live-objective", "synthetic autonomous study"),
        limits=limits
        or LiveSessionLimits(
            max_metadata_gets=len(reviewed),
            max_wall_clock_seconds=60,
            max_study_cycles=10,
            max_study_gets=3,
            max_observations_per_study=1,
            max_cumulative_observations=10,
            max_consecutive_non_progress=2,
        ),
    )


def openers(*, metadata_failure_at=None, record_rows=None):
    metadata_requests = []
    record_requests = []

    def metadata_opener(request, *, timeout):
        metadata_requests.append(request)
        if metadata_failure_at == len(metadata_requests):
            raise TimeoutError("synthetic metadata timeout")
        doctype = parse_qs(urlparse(request.full_url).query)["doctype"][0]
        docs = {
            "Synthetic Alpha": metadata_doc(
                "Synthetic Alpha", "alpha_value", "alpha_note"
            ),
            "Synthetic Beta": metadata_doc("Synthetic Beta", "beta_value", submitted=True),
        }
        return FakeResponse(request, {"message": {"docs": [docs[doctype]]}})

    def record_opener(request, *, timeout):
        query = parse_qs(urlparse(request.full_url).query)
        resource = unquote(urlparse(request.full_url).path.rsplit("/", 1)[-1])
        fields = json.loads(query["fields"][0])
        filters = json.loads(query["filters"][0])
        company = filters[0][2]
        requested = int(query["limit_page_length"][0])
        record_requests.append(
            {
                "resource": resource,
                "company": company,
                "requested": requested,
                "filters": filters,
                "fields": fields,
            }
        )
        if record_rows is not None:
            rows = record_rows(resource, company, fields, requested, len(record_requests))
        else:
            rows = []
            for index in range(requested):
                row = {
                    name: f"synthetic-value-{len(record_requests)}-{index}"
                    for name in fields
                }
                row["name"] = f"synthetic-record-{len(record_requests)}-{index}"
                row["company"] = company
                if "docstatus" in fields:
                    row["docstatus"] = 1
                rows.append(row)
        return FakeResponse(request, {"data": rows})

    return metadata_opener, record_opener, metadata_requests, record_requests


@pytest.mark.parametrize(
    "companies",
    [(), ("",), (" padded",), ("duplicate", "duplicate"), ("wild*",), ("bad\x7f",)],
)
def test_company_authorization_rejects_non_exact_scope(companies):
    with pytest.raises(LiveSessionError):
        ERPNextCompanyAuthorization(TENANT, companies)


def test_authorization_and_reviewed_scope_require_immutable_tuples(tmp_path):
    companies = list(COMPANIES)
    fields = ["alpha_value"]
    with pytest.raises(TypeError, match="immutable tuple"):
        ERPNextCompanyAuthorization(TENANT, companies)
    with pytest.raises(TypeError, match="immutable tuple"):
        ReviewedMetadataScope("Synthetic Alpha", fields)
    plan = config(tmp_path)
    with pytest.raises(TypeError, match="immutable tuple"):
        replace(plan, reviewed_scopes=list(plan.reviewed_scopes))


@pytest.mark.parametrize(
    "entity,fields",
    [
        ("User", ("full_name",)),
        ("Safe Entity", ("api_secret",)),
        ("Safe Entity", ("company",)),
        ("Safe Entity", ()),
        ("Safe Entity", ("value", "value")),
    ],
)
def test_reviewed_scope_rejects_sensitive_or_ambiguous_targets(entity, fields):
    with pytest.raises(LiveSessionError):
        ReviewedMetadataScope(entity, fields)


@pytest.mark.parametrize(
    "key_ref,secret_ref",
    [("lowercase", "SECRET"), ("KEY", "KEY"), ("KEY-VALUE", "SECRET")],
)
def test_credential_references_are_safe_and_distinct(key_ref, secret_ref):
    with pytest.raises(LiveSessionError):
        CredentialEnvironmentReferences(key_ref, secret_ref)


def test_credentials_resolve_without_entering_repr_or_reports(tmp_path):
    refs = CredentialEnvironmentReferences(KEY_REF, SECRET_REF)
    resolved = refs.resolve(SECRET_ENVIRONMENT)
    rendered = repr(resolved) + repr(config(tmp_path))
    assert SECRET_ENVIRONMENT[KEY_REF] not in rendered
    assert SECRET_ENVIRONMENT[SECRET_REF] not in rendered


def test_readiness_is_non_mutating_and_reports_only_aggregate_facts(tmp_path):
    plan = config(tmp_path)
    before = tuple(tmp_path.rglob("*"))
    report = inspect_live_session_readiness(plan, environment=SECRET_ENVIRONMENT)
    after = tuple(tmp_path.rglob("*"))

    assert report.ready
    assert report.company_scope_count == 2
    assert report.metadata_get_budget == 2
    assert report.study_get_budget == 3
    assert report.max_live_gets == 5
    assert report.live_gets_performed == report.erp_writes == 0
    assert before == after
    rendered = live_session_report_json(report)
    for forbidden in (*COMPANIES, TENANT, KEY_REF, SECRET_REF, *SECRET_ENVIRONMENT.values()):
        assert forbidden not in rendered
    assert '"execution_allowed":false' in rendered


@pytest.mark.parametrize(
    "change",
    [
        {"execution_allowed": True},
        {"execution_allowed": 0},
        {"recommendation_allowed": True},
        {"promotion_allowed": True},
        {"erp_writes": 1},
        {"erp_writes": False},
        {"live_gets_performed": 1},
    ],
)
def test_readiness_report_cannot_be_replaced_with_authority(tmp_path, change):
    report = inspect_live_session_readiness(
        config(tmp_path),
        environment=SECRET_ENVIRONMENT,
    )
    with pytest.raises(LiveSessionError, match="cannot (grant|claim)"):
        replace(report, **change)


def test_report_destination_must_be_owner_only(tmp_path):
    plan = config(tmp_path)
    plan.report_directory.chmod(0o770)
    readiness = inspect_live_session_readiness(plan, environment=SECRET_ENVIRONMENT)
    assert not readiness.report_destination_ready
    assert not readiness.ready

    metadata, records, _, _ = openers()
    report = run_erpnext_live_session(
        plan,
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata,
        record_opener=records,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )
    assert report.stop_reason == "read_limit"
    assert plan.report_directory.stat().st_mode & 0o777 == 0o700


def test_readiness_detects_missing_credentials_without_printing_references(tmp_path):
    report = inspect_live_session_readiness(config(tmp_path), environment={})
    assert not report.ready
    assert not report.credentials_available
    rendered = live_session_report_json(report)
    assert KEY_REF not in rendered and SECRET_REF not in rendered


def test_execute_with_missing_credentials_does_not_create_storage(tmp_path):
    plan = config(tmp_path)
    plan.state_directory.rmdir()
    plan.report_directory.rmdir()

    with pytest.raises(LiveSessionError, match="credential is unavailable"):
        run_erpnext_live_session(
            plan,
            environment={},
            clock=lambda: NOW,
            monotonic=lambda: 0.0,
        )

    assert not plan.state_directory.exists()
    assert not plan.report_directory.exists()


def test_credential_resolver_sanitizes_mapping_errors():
    class FailingEnvironment(dict):
        def get(self, key, default=None):
            raise RuntimeError("synthetic-secret-value-not-for-output")

    with pytest.raises(LiveSessionError) as caught:
        CredentialEnvironmentReferences(KEY_REF, SECRET_REF).resolve(
            FailingEnvironment()
        )
    assert "synthetic-secret-value-not-for-output" not in str(caught.value)


def test_readiness_reports_credentials_independently_of_missing_storage(tmp_path):
    plan = config(tmp_path)
    blocked_parent = tmp_path / "blocked-parent"
    blocked_parent.write_text("not a directory")
    unavailable = replace(plan, state_directory=blocked_parent / "state")

    report = inspect_live_session_readiness(unavailable, environment=SECRET_ENVIRONMENT)

    assert report.credentials_available
    assert not report.state_destination_ready
    assert not report.ready


def test_metadata_preflight_and_study_budgets_are_separate_and_global(tmp_path):
    plan = config(tmp_path)
    metadata, records, metadata_requests, record_requests = openers()
    report = run_erpnext_live_session(
        plan,
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata,
        record_opener=records,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    assert len(metadata_requests) == report.metadata_gets == report.metadata_get_budget == 2
    assert len(record_requests) == report.study_gets == report.study_get_budget == 3
    assert report.total_live_gets == report.max_live_gets == 5
    assert [item["company"] for item in record_requests] == [
        COMPANIES[0],
        COMPANIES[1],
        COMPANIES[0],
    ]
    assert report.distinct_companies_attempted == 2
    assert all(item["filters"][0] == ["company", "=", item["company"]] for item in record_requests)
    assert report.cycles_completed == report.observations_persisted == 3
    assert report.stop_reason == "read_limit"
    assert report.erp_writes == 0
    assert not report.recommendation_allowed
    assert not report.promotion_allowed
    assert not report.execution_allowed


def test_autonomous_target_selection_uses_all_reviewed_safe_metadata(tmp_path):
    metadata, records, _, record_requests = openers()
    report = run_erpnext_live_session(
        config(tmp_path),
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata,
        record_opener=records,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    selected = {(item["resource"], item["fields"][0]) for item in record_requests}
    assert len(selected) >= 2
    assert selected.issubset(
        {
            ("Synthetic Alpha", "alpha_value"),
            ("Synthetic Alpha", "alpha_note"),
            ("Synthetic Beta", "beta_value"),
        }
    )
    assert report.distinct_entities_studied >= 1


def test_metadata_can_narrow_but_cannot_widen_reviewed_targets(tmp_path):
    record_requests = []

    def metadata_opener(request, *, timeout):
        return FakeResponse(
            request,
            {
                "message": {
                    "docs": [
                        metadata_doc(
                            "Synthetic Alpha",
                            "alpha_value",
                            "unreviewed_business_value",
                        ),
                        metadata_doc("Unexpected Business Entity", "unexpected_value"),
                    ]
                }
            },
        )

    _, records, _, captured = openers()
    report = run_erpnext_live_session(
        config(
            tmp_path,
            scopes=(ReviewedMetadataScope("Synthetic Alpha", ("alpha_value",)),),
            limits=LiveSessionLimits(
                max_metadata_gets=1,
                max_study_cycles=2,
                max_study_gets=2,
                max_observations_per_study=1,
                max_cumulative_observations=2,
                max_consecutive_non_progress=1,
            ),
        ),
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata_opener,
        record_opener=records,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )
    record_requests.extend(captured)

    assert report.study_gets == 2
    assert {item["resource"] for item in record_requests} == {"Synthetic Alpha"}
    assert all("alpha_value" in item["fields"] for item in record_requests)
    assert all(
        "unreviewed_business_value" not in item["fields"]
        and "unexpected_value" not in item["fields"]
        for item in record_requests
    )


def test_cumulative_observation_budget_narrows_last_company_read(tmp_path):
    limits = LiveSessionLimits(
        max_metadata_gets=2,
        max_wall_clock_seconds=60,
        max_study_cycles=10,
        max_study_gets=10,
        max_observations_per_study=2,
        max_cumulative_observations=5,
        max_consecutive_non_progress=2,
    )
    metadata, records, _, record_requests = openers()
    report = run_erpnext_live_session(
        config(tmp_path, limits=limits),
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata,
        record_opener=records,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    assert [item["requested"] for item in record_requests] == [2, 2, 1]
    assert [item["company"] for item in record_requests] == [
        COMPANIES[0],
        COMPANIES[1],
        COMPANIES[0],
    ]
    assert report.study_gets == 3
    assert report.observations_persisted == 5
    assert report.stop_reason == "observation_limit"


def test_cross_company_response_stops_before_append(tmp_path):
    def wrong_company(resource, company, fields, requested, call_number):
        row = {name: "synthetic" for name in fields}
        row["name"] = "synthetic-record"
        row["company"] = "not-authorized-for-this-request"
        if "docstatus" in fields:
            row["docstatus"] = 1
        return [row]

    metadata, records, _, record_requests = openers(record_rows=wrong_company)
    report = run_erpnext_live_session(
        config(tmp_path),
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata,
        record_opener=records,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    assert len(record_requests) == 2
    assert report.study_gets == 2
    assert report.distinct_companies_attempted == 2
    assert report.observations_persisted == 0
    assert report.stop_reason == "erp_contract_failure"


def test_metadata_failure_consumes_preflight_budget_and_performs_no_study(tmp_path):
    metadata, records, metadata_requests, record_requests = openers(metadata_failure_at=2)
    report = run_erpnext_live_session(
        config(tmp_path),
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata,
        record_opener=records,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    assert len(metadata_requests) == report.metadata_gets == 2
    assert record_requests == []
    assert report.study_gets == 0
    assert report.stop_reason == "metadata_preflight_failure"


def test_hidden_reviewed_metadata_fails_before_record_read(tmp_path):
    record_requests = []

    def metadata_opener(request, *, timeout):
        doctype = parse_qs(urlparse(request.full_url).query)["doctype"][0]
        return FakeResponse(
            request,
            {
                "message": {
                    "docs": [metadata_doc(doctype, "alpha_value", hidden={"alpha_value"})]
                }
            },
        )

    report = run_erpnext_live_session(
        config(
            tmp_path,
            scopes=(ReviewedMetadataScope("Synthetic Alpha", ("alpha_value",)),),
            limits=LiveSessionLimits(max_metadata_gets=1, max_study_gets=1),
        ),
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata_opener,
        record_opener=lambda *args, **kwargs: record_requests.append(args),
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )
    assert record_requests == []
    assert report.metadata_gets == 1
    assert report.stop_reason == "metadata_preflight_failure"


def test_interruption_before_preflight_performs_no_get(tmp_path):
    metadata, records, metadata_requests, record_requests = openers()
    report = run_erpnext_live_session(
        config(tmp_path),
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata,
        record_opener=records,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        termination_requested=lambda: True,
    )
    assert metadata_requests == record_requests == []
    assert report.total_live_gets == 0
    assert report.distinct_companies_attempted == 0
    assert report.stop_reason == "user_termination"


def test_interruption_between_preflight_and_soak_performs_no_record_get(tmp_path):
    metadata, records, metadata_requests, record_requests = openers()
    decisions = iter((False, False, False, True))

    report = run_erpnext_live_session(
        config(tmp_path),
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata,
        record_opener=records,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
        termination_requested=lambda: next(decisions),
    )
    assert len(metadata_requests) == 2
    assert record_requests == []
    assert report.metadata_preflight_completed
    assert report.stop_reason == "user_termination"


def test_keyboard_interrupt_during_metadata_is_counted_without_record_read(tmp_path):
    metadata_requests = []
    record_requests = []

    def interrupted_metadata(request, *, timeout):
        metadata_requests.append(request)
        raise KeyboardInterrupt

    report = run_erpnext_live_session(
        config(tmp_path),
        environment=SECRET_ENVIRONMENT,
        metadata_opener=interrupted_metadata,
        record_opener=lambda *args, **kwargs: record_requests.append(args),
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    assert len(metadata_requests) == report.metadata_gets == 1
    assert record_requests == []
    assert report.stop_reason == "user_termination"


def test_keyboard_interrupt_during_soak_counts_one_shared_read(tmp_path):
    metadata, _, _, _ = openers()
    record_requests = []

    def interrupted_record(request, *, timeout):
        record_requests.append(request)
        raise KeyboardInterrupt

    report = run_erpnext_live_session(
        config(tmp_path),
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata,
        record_opener=interrupted_record,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    assert len(record_requests) == report.study_gets == 1
    assert report.distinct_companies_attempted == 1
    assert report.observations_persisted == 0
    assert report.stop_reason == "user_termination"


def test_existing_unauthorized_company_evidence_blocks_before_metadata(tmp_path):
    plan = config(tmp_path)
    evidence_path = default_historical_evidence_path(
        TENANT,
        resource="live-shadow-soak",
        state_root=plan.state_directory,
    )
    observation = Observation(
        Evidence(
            kind=EvidenceKind.API,
            source="synthetic-existing",
            tenant_id=TENANT,
            observed_at=NOW,
            payload={
                "resource": "Synthetic Alpha",
                "record": {
                    "name": "synthetic-existing-record",
                    "company": "removed-company",
                    "alpha_value": "synthetic",
                },
            },
        )
    )
    SQLiteHistoricalEvidenceStore(evidence_path).append(
        HistoricalEvidenceBatch(TENANT, "Synthetic Alpha", 1, NOW, (observation,))
    )
    metadata, records, metadata_requests, record_requests = openers()
    report = run_erpnext_live_session(
        plan,
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata,
        record_opener=records,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )
    assert metadata_requests == record_requests == []
    assert report.total_live_gets == 0
    assert report.stop_reason == "persistence_failure"


def test_aggregate_report_is_written_once_without_sensitive_values(tmp_path):
    metadata, records, _, _ = openers()
    plan = config(tmp_path)
    report = run_erpnext_live_session(
        plan,
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata,
        record_opener=records,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )
    files = tuple(plan.report_directory.iterdir())
    assert len(files) == 1
    assert files[0].is_file()
    rendered = files[0].read_text()
    assert json.loads(rendered) == json.loads(live_session_report_json(report))
    for forbidden in (
        *COMPANIES,
        TENANT,
        KEY_REF,
        SECRET_REF,
        *SECRET_ENVIRONMENT.values(),
        str(plan.state_directory),
        str(plan.report_directory),
        "Synthetic Alpha",
        "alpha_value",
        "synthetic-record",
    ):
        assert forbidden not in rendered


def test_aggregate_report_rejects_unallowlisted_text(tmp_path):
    metadata, records, _, _ = openers()
    report = run_erpnext_live_session(
        config(tmp_path),
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata,
        record_opener=records,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )
    with pytest.raises(LiveSessionError, match="stop reason"):
        replace(report, stop_reason="synthetic-private-error-text")
    with pytest.raises(LiveSessionError, match="failure categories"):
        replace(report, failure_category_counts=(("synthetic-private-error-text", 1),))
    with pytest.raises(LiveSessionError, match="downstream authority"):
        replace(report, execution_allowed=0)
    with pytest.raises(LiveSessionError, match="ERP writes"):
        replace(report, erp_writes=False)


def test_metadata_elapsed_time_reduces_one_shared_soak_duration(
    tmp_path,
    monkeypatch,
):
    class SteppedMonotonic:
        def __init__(self):
            self.value = -1.0

        def __call__(self):
            self.value += 1.0
            return self.value

    original = live_session_module.run_autonomous_shadow_soak
    captured = []

    def capture_session(*args, **kwargs):
        captured.append(args[2].max_wall_clock_seconds)
        return original(*args, **kwargs)

    monkeypatch.setattr(
        live_session_module,
        "run_autonomous_shadow_soak",
        capture_session,
    )
    metadata, records, _, _ = openers()
    plan = config(tmp_path)
    run_erpnext_live_session(
        plan,
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata,
        record_opener=records,
        clock=lambda: NOW,
        monotonic=SteppedMonotonic(),
    )

    assert captured == [56.0]


@pytest.mark.parametrize(
    "stop_reason,expected",
    [
        ("metadata_preflight_failure", 2),
        ("persistence_failure", 2),
        ("erp_contract_failure", 2),
        ("tenant_scope_mismatch", 2),
        ("user_termination", 130),
        ("read_limit", 0),
    ],
)
def test_execute_cli_exit_status_matches_safe_stop_category(
    tmp_path,
    monkeypatch,
    capsys,
    stop_reason,
    expected,
):
    plan = config(tmp_path)
    metadata, records, _, _ = openers()
    successful = run_erpnext_live_session(
        plan,
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata,
        record_opener=records,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )
    readiness = inspect_live_session_readiness(plan, environment=SECRET_ENVIRONMENT)
    monkeypatch.setattr(
        live_session_module,
        "live_session_config_from_environment",
        lambda environment: plan,
    )
    monkeypatch.setattr(
        live_session_module,
        "inspect_live_session_readiness",
        lambda config, environment: readiness,
    )
    monkeypatch.setattr(
        live_session_module,
        "run_erpnext_live_session",
        lambda config, **kwargs: replace(successful, stop_reason=stop_reason),
    )

    assert live_session_module.main(["--execute"]) == expected
    output = capsys.readouterr().out
    assert '"execution_allowed":false' in output


def test_configuration_rejects_metadata_budget_not_equal_to_scope(tmp_path):
    with pytest.raises(LiveSessionError, match="exactly match"):
        config(
            tmp_path,
            limits=LiveSessionLimits(max_metadata_gets=1),
        )


def test_configuration_rejects_repository_or_symlink_destination(tmp_path):
    state = tmp_path / "state"
    reports = tmp_path / "reports"
    state.mkdir(mode=0o700)
    reports.mkdir(mode=0o700)
    symlink = tmp_path / "linked-state"
    symlink.symlink_to(state, target_is_directory=True)
    with pytest.raises(LiveSessionStorageError, match="symlink"):
        ERPNextLiveSessionConfig(
            base_url="https://synthetic.invalid",
            tenant_id=TENANT,
            authorization_reference="synthetic-ledger-reference",
            company_authorization=ERPNextCompanyAuthorization(TENANT, COMPANIES),
            reviewed_scopes=(ReviewedMetadataScope("Synthetic Alpha", ("value",)),),
            credential_references=CredentialEnvironmentReferences(KEY_REF, SECRET_REF),
            state_directory=symlink,
            report_directory=reports,
            objective=LearningObjective("objective", "synthetic"),
            limits=LiveSessionLimits(max_metadata_gets=1),
        )


def test_existing_state_files_cannot_share_one_inode(tmp_path):
    plan = config(tmp_path)
    evidence_path = default_historical_evidence_path(
        TENANT,
        resource="live-shadow-soak",
        state_root=plan.state_directory,
    )
    _ = SQLiteHistoricalEvidenceStore(evidence_path)
    checkpoint_digest = hashlib.sha256(
        f"{TENANT}\0live-metadata".encode()
    ).hexdigest()
    checkpoint_path = plan.state_directory / f"study-checkpoints-{checkpoint_digest}.sqlite3"
    os.link(evidence_path, checkpoint_path)
    metadata, records, metadata_requests, record_requests = openers()

    report = run_erpnext_live_session(
        plan,
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata,
        record_opener=records,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )
    assert metadata_requests == record_requests == []
    assert report.stop_reason == "persistence_failure"


def test_state_database_cannot_hardlink_unrelated_file(tmp_path):
    plan = config(tmp_path)
    external_path = tmp_path / "unrelated.sqlite3"
    _ = SQLiteHistoricalEvidenceStore(external_path)
    evidence_path = default_historical_evidence_path(
        TENANT,
        resource="live-shadow-soak",
        state_root=plan.state_directory,
    )
    os.link(external_path, evidence_path)
    before = external_path.read_bytes()
    metadata, records, metadata_requests, record_requests = openers()

    report = run_erpnext_live_session(
        plan,
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata,
        record_opener=records,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    assert metadata_requests == record_requests == []
    assert report.total_live_gets == 0
    assert report.stop_reason == "persistence_failure"
    assert external_path.read_bytes() == before


def test_corrupt_checkpoint_blocks_before_metadata(tmp_path):
    plan = config(tmp_path)
    checkpoint_digest = hashlib.sha256(
        f"{TENANT}\0live-metadata".encode()
    ).hexdigest()
    checkpoint_path = plan.state_directory / f"study-checkpoints-{checkpoint_digest}.sqlite3"
    checkpoint_path.write_bytes(b"not-a-sqlite-database")
    checkpoint_path.chmod(0o600)
    metadata, records, metadata_requests, record_requests = openers()

    report = run_erpnext_live_session(
        plan,
        environment=SECRET_ENVIRONMENT,
        metadata_opener=metadata,
        record_opener=records,
        clock=lambda: NOW,
        monotonic=lambda: 0.0,
    )

    assert metadata_requests == record_requests == []
    assert report.total_live_gets == 0
    assert report.stop_reason == "persistence_failure"


def test_environment_loader_accepts_references_but_not_secret_cli_values(tmp_path):
    environment = {
        "ORION_LIVE_BASE_URL": "https://synthetic.invalid",
        "ORION_LIVE_TENANT_ID": TENANT,
        "ORION_LIVE_AUTHORIZATION_REFERENCE": "synthetic-ledger-reference",
        "ORION_LIVE_COMPANIES_JSON": json.dumps(list(COMPANIES)),
        "ORION_LIVE_REVIEWED_SCOPES_JSON": json.dumps(
            {"Synthetic Alpha": ["alpha_value"], "Synthetic Beta": ["beta_value"]}
        ),
        "ORION_LIVE_API_KEY_REF": KEY_REF,
        "ORION_LIVE_API_SECRET_REF": SECRET_REF,
        "ORION_LIVE_STATE_DIR": str(tmp_path / "state"),
        "ORION_LIVE_REPORT_DIR": str(tmp_path / "reports"),
        "ORION_LIVE_OBJECTIVE_ID": "objective",
        "ORION_LIVE_OBJECTIVE_DESCRIPTION": "synthetic objective",
        **SECRET_ENVIRONMENT,
    }
    plan = live_session_config_from_environment(environment)
    assert plan.limits.max_metadata_gets == 2
    assert plan.limits.max_study_gets == 100
    assert plan.limits.max_live_gets == 102
    assert SECRET_ENVIRONMENT[KEY_REF] not in repr(plan)
    assert SECRET_ENVIRONMENT[SECRET_REF] not in repr(plan)


def test_missing_environment_configuration_uses_sanitized_error(tmp_path):
    with pytest.raises(LiveSessionError) as caught:
        live_session_config_from_environment({"UNRELATED": "private-value"})
    assert "private-value" not in str(caught.value)


def test_environment_loader_rejects_duplicate_scope_keys(tmp_path):
    environment = {
        "ORION_LIVE_BASE_URL": "https://synthetic.invalid",
        "ORION_LIVE_TENANT_ID": TENANT,
        "ORION_LIVE_AUTHORIZATION_REFERENCE": "synthetic-ledger-reference",
        "ORION_LIVE_COMPANIES_JSON": json.dumps(list(COMPANIES)),
        "ORION_LIVE_REVIEWED_SCOPES_JSON": (
            '{"Synthetic Alpha":["alpha_value"],'
            '"Synthetic Alpha":["alpha_note"]}'
        ),
        "ORION_LIVE_API_KEY_REF": KEY_REF,
        "ORION_LIVE_API_SECRET_REF": SECRET_REF,
        "ORION_LIVE_STATE_DIR": str(tmp_path / "state"),
        "ORION_LIVE_REPORT_DIR": str(tmp_path / "reports"),
        "ORION_LIVE_OBJECTIVE_ID": "objective",
        "ORION_LIVE_OBJECTIVE_DESCRIPTION": "synthetic objective",
    }
    with pytest.raises(LiveSessionError, match="JSON configuration"):
        live_session_config_from_environment(environment)
