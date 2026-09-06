import json
import stat
import threading
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request

import pytest

import orion.discovery.erpnext_metadata_preflight as preflight_module
from orion.discovery.erpnext_adapter import DEFAULT_MAX_RESPONSE_BYTES
from orion.discovery.erpnext_live_session import (
    CredentialEnvironmentReferences,
    LiveSessionError,
    LiveSessionStorageError,
)
from orion.discovery.erpnext_metadata_preflight import (
    ERPNextMetadataPreflightConfig,
    MetadataCatalogRetryReport,
    MetadataPreflightBudgetError,
    MetadataPreflightError,
    MetadataPreflightRunReport,
    _BudgetedOpener,
    _candidate_path,
    _contains_sensitive_embedded_value,
    _ledger_path,
    _permission_retry_report_path,
    _read_name_catalog,
    _retry_report_path,
    _RunLedger,
    inspect_metadata_preflight_readiness,
    main,
    metadata_preflight_config_from_environment,
    run_erpnext_doctype_catalog_permission_retry_once,
    run_erpnext_doctype_catalog_retry_once,
    run_erpnext_metadata_preflight,
)

NOW = datetime(2026, 9, 6, 12, tzinfo=UTC)
TENANT = "synthetic-tenant"
KEY_REF = "SYNTHETIC_PREFLIGHT_KEY"
SECRET_REF = "SYNTHETIC_PREFLIGHT_SECRET"
SECRET_KEY = "synthetic-key-value-never-persist"
SECRET_VALUE = "synthetic-secret-value-never-persist"
SECRET_ENVIRONMENT = {KEY_REF: SECRET_KEY, SECRET_REF: SECRET_VALUE}


class FakeResponse:
    def __init__(self, request: Request, payload: object) -> None:
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


class RawResponse(FakeResponse):
    def __init__(self, request: Request, body: bytes) -> None:
        self._url = request.full_url
        self._body = body


def config(tmp_path: Path) -> ERPNextMetadataPreflightConfig:
    state = tmp_path / "state"
    reports = tmp_path / "reports"
    state.mkdir(mode=0o700)
    reports.mkdir(mode=0o700)
    return ERPNextMetadataPreflightConfig(
        base_url="https://synthetic.invalid",
        tenant_id=TENANT,
        authorization_reference="synthetic-metadata-only-ledger",
        credential_references=CredentialEnvironmentReferences(KEY_REF, SECRET_REF),
        state_directory=state,
        report_directory=reports,
    )


def metadata_payload(
    doctype: str,
    *,
    raw_label: str = "RAW LABEL MUST NOT PERSIST",
    default: str | None = None,
) -> dict[str, object]:
    metric: dict[str, object] = {
        "fieldname": "metric",
        "fieldtype": "Currency",
        "label": raw_label,
    }
    secret_field: dict[str, object] = {
        "fieldname": "api_secret",
        "fieldtype": "Password",
        "label": "RAW SECRET LABEL MUST NOT PERSIST",
    }
    if default is not None:
        secret_field["default"] = default
    return {
        "message": {
            "docs": [
                {
                    "name": doctype,
                    "module": "Synthetic Raw Module",
                    "fields": [
                        {
                            "fieldname": "company",
                            "fieldtype": "Link",
                            "options": "Company",
                        },
                        metric,
                        secret_field,
                    ],
                }
            ]
        }
    }


def catalog_payload(*names: str) -> dict[str, object]:
    return {"data": [{"name": name} for name in names]}


def run_valid_preflight(
    plan: ERPNextMetadataPreflightConfig,
    *,
    doctypes: tuple[str, ...] = ("Safe Invoice",),
):
    requests = []

    def opener(request, *, timeout):
        requests.append(request)
        path = urlparse(request.full_url).path
        if path == "/api/resource/Company":
            return FakeResponse(request, catalog_payload("Company A"))
        if path == "/api/resource/DocType":
            return FakeResponse(request, catalog_payload(*doctypes))
        assert path == "/api/method/frappe.desk.form.load.getdoctype"
        doctype = parse_qs(urlparse(request.full_url).query)["doctype"][0]
        assert doctype in doctypes
        return FakeResponse(request, metadata_payload(doctype))

    report = run_erpnext_metadata_preflight(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
    )
    return report, requests


def failed_doctype_catalog_ledger(plan: ERPNextMetadataPreflightConfig) -> _RunLedger:
    ledger = _RunLedger.create(_ledger_path(plan))
    assert ledger.reserve_attempt() == 1
    assert ledger.reserve_attempt() == 2
    ledger.update(
        status="failed",
        company_count=1,
        company_catalog_complete=1,
    )
    return ledger


def failed_permission_retry(
    plan: ERPNextMetadataPreflightConfig,
) -> tuple[_RunLedger, Path, bytes]:
    ledger = failed_doctype_catalog_ledger(plan)

    def permission_denied(request, *, timeout):
        raise HTTPError(request.full_url, 403, "synthetic denial", {}, None)

    report = run_erpnext_doctype_catalog_retry_once(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=permission_denied,
        clock=lambda: NOW,
    )
    assert report.failure_category == "http_permission"
    path = _retry_report_path(plan)
    return ledger, path, path.read_bytes()


def test_company_catalog_request_is_exactly_name_only_and_single_page(tmp_path):
    plan = config(tmp_path)
    captured = {}

    def opener(request, *, timeout):
        captured["request"] = request
        captured["timeout"] = timeout
        return FakeResponse(request, catalog_payload("Company A", "Company B"))

    names, complete = _read_name_catalog(
        plan,
        resource="Company",
        requested=500,
        opener=opener,
    )

    request = captured["request"]
    parsed = urlparse(request.full_url)
    query = parse_qs(parsed.query)
    assert names == ("Company A", "Company B")
    assert complete is True
    assert request.method == "GET"
    assert parsed.path == "/api/resource/Company"
    assert query == {
        "fields": ['["name"]'],
        "limit_start": ["0"],
        "limit_page_length": ["500"],
        "order_by": ["name asc"],
    }
    assert "filters" not in query
    assert captured["timeout"] == 20


def test_catalog_over_return_fails_closed(tmp_path):
    plan = config(tmp_path)

    def opener(request, *, timeout):
        return FakeResponse(request, catalog_payload("A", "B", "C"))

    with pytest.raises(MetadataPreflightError, match="operation failed") as caught:
        _read_name_catalog(
            plan,
            resource="Company",
            requested=2,
            opener=opener,
        )
    assert getattr(caught.value, "category", None) == "response_validation"


@pytest.mark.parametrize(
    ("failure", "expected"),
    (
        (HTTPError("https://synthetic.invalid", 401, "private", None, None), "http_permission"),
        (HTTPError("https://synthetic.invalid", 403, "private", None, None), "http_permission"),
        (HTTPError("https://synthetic.invalid", 404, "private", None, None), "endpoint_contract"),
        (HTTPError("https://synthetic.invalid", 429, "private", None, None), "http_status"),
        (URLError("private transport detail"), "transport_failure"),
        (TimeoutError("private timeout detail"), "transport_failure"),
    ),
)
def test_doctype_catalog_transport_failures_emit_only_sanitized_categories(
    tmp_path,
    failure,
    expected,
):
    plan = config(tmp_path)

    def opener(request, *, timeout):
        if urlparse(request.full_url).path == "/api/resource/Company":
            return FakeResponse(request, catalog_payload("Company A"))
        raise failure

    report = run_erpnext_metadata_preflight(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
    )

    assert report.status == "failed"
    assert report.attempted_gets == 2
    assert report.failure_stage == "doctype_catalog"
    assert report.failure_category == expected
    rendered, = plan.report_directory.glob("metadata-preflight-report-*.json")
    report_text = rendered.read_text()
    assert "private" not in report_text
    assert "synthetic.invalid" not in report_text


@pytest.mark.parametrize(
    ("response", "expected"),
    (
        ({"data": [], "unexpected": "private value"}, "response_validation"),
        ({"data": [{"name": "Safe", "extra": "private value"}]}, "response_validation"),
    ),
)
def test_doctype_catalog_response_failures_are_sanitized(tmp_path, response, expected):
    plan = config(tmp_path)

    def opener(request, *, timeout):
        if urlparse(request.full_url).path == "/api/resource/Company":
            return FakeResponse(request, catalog_payload("Company A"))
        return FakeResponse(request, response)

    report = run_erpnext_metadata_preflight(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
    )

    assert report.status == "failed"
    assert report.failure_stage == "doctype_catalog"
    assert report.failure_category == expected


@pytest.mark.parametrize(
    "body",
    (
        b"not-json private response",
        b"x" * (DEFAULT_MAX_RESPONSE_BYTES + 1),
    ),
    ids=("invalid-json", "oversized"),
)
def test_doctype_catalog_invalid_or_oversized_response_is_sanitized(tmp_path, body):
    plan = config(tmp_path)

    def opener(request, *, timeout):
        if urlparse(request.full_url).path == "/api/resource/Company":
            return FakeResponse(request, catalog_payload("Company A"))
        return RawResponse(request, body)

    report = run_erpnext_metadata_preflight(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
    )

    assert report.status == "failed"
    assert report.failure_stage == "doctype_catalog"
    assert report.failure_category == "response_validation"
    report_path, = plan.report_directory.glob("metadata-preflight-report-*.json")
    assert "private response" not in report_path.read_text()


def test_doctype_catalog_redirect_is_sanitized(tmp_path):
    plan = config(tmp_path)

    def opener(request, *, timeout):
        response = FakeResponse(request, catalog_payload("Company A"))
        if urlparse(request.full_url).path == "/api/resource/DocType":
            response._url = "https://redirect.invalid/private"
        return response

    report = run_erpnext_metadata_preflight(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
    )

    assert report.status == "failed"
    assert report.failure_stage == "doctype_catalog"
    assert report.failure_category == "redirect_rejected"


@pytest.mark.parametrize("company", ("Wildcard*", "X" * 257))
def test_catalog_names_that_are_not_exact_live_companies_fail_before_next_get(
    tmp_path,
    company,
):
    plan = config(tmp_path)
    requests = []

    def opener(request, *, timeout):
        requests.append(request)
        return FakeResponse(request, catalog_payload(company))

    report = run_erpnext_metadata_preflight(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
    )

    assert report.status == "failed"
    assert report.failure_stage == "company_catalog"
    assert report.failure_category == "scope_validation"
    assert report.attempted_gets == 1
    assert len(requests) == 1
    assert urlparse(requests[0].full_url).path == "/api/resource/Company"
    readiness = inspect_metadata_preflight_readiness(
        plan,
        environment=SECRET_ENVIRONMENT,
    )
    assert readiness.offline_candidate_ready is False
    assert readiness.ready_for_metadata_preflight is False


def test_candidate_storage_failure_emits_only_safe_category(tmp_path):
    plan = config(tmp_path)

    def opener(request, *, timeout):
        path = urlparse(request.full_url).path
        if path == "/api/resource/Company":
            return FakeResponse(request, catalog_payload("Company A"))
        if path == "/api/resource/DocType":
            _candidate_path(plan).write_text("preexisting private collision")
            return FakeResponse(request, catalog_payload())
        pytest.fail("metadata target request was not expected")

    report = run_erpnext_metadata_preflight(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
    )

    assert report.status == "failed"
    assert report.failure_stage == "candidate_persistence"
    assert report.failure_category == "storage_failure"
    report_path, = plan.report_directory.glob("metadata-preflight-report-*.json")
    assert "preexisting private collision" not in report_path.read_text()


def test_shared_budget_charges_failures_and_blocks_attempt_101(tmp_path):
    ledger_path = tmp_path / "attempts.sqlite3"
    ledger = _RunLedger.create(ledger_path)
    transport_calls = 0

    def failing_opener(request, *, timeout):
        nonlocal transport_calls
        transport_calls += 1
        raise TimeoutError("synthetic charged failure")

    opener = _BudgetedOpener(ledger, failing_opener)
    request = Request("https://synthetic.invalid/metadata", method="GET")
    for _ in range(100):
        with pytest.raises(TimeoutError, match="charged failure"):
            opener(request, timeout=20)

    assert ledger.snapshot()["attempted_gets"] == 100
    assert transport_calls == 100
    with pytest.raises(MetadataPreflightBudgetError, match="budget exhausted"):
        opener(request, timeout=20)
    assert ledger.snapshot()["attempted_gets"] == 100
    assert transport_calls == 100


def test_success_persists_only_sanitized_candidate_and_aggregate_report(tmp_path):
    plan = config(tmp_path)
    requests = []

    def opener(request, *, timeout):
        requests.append(request)
        parsed = urlparse(request.full_url)
        if parsed.path == "/api/resource/Company":
            return FakeResponse(request, catalog_payload("Company A", "Company B"))
        if parsed.path == "/api/resource/DocType":
            return FakeResponse(
                request,
                catalog_payload("Embedded Default", "Safe Invoice"),
            )
        assert parsed.path == "/api/method/frappe.desk.form.load.getdoctype"
        doctype = parse_qs(parsed.query)["doctype"][0]
        if doctype == "Embedded Default":
            return FakeResponse(
                request,
                metadata_payload(doctype, default="RAW DEFAULT MUST NOT PERSIST"),
            )
        return FakeResponse(request, metadata_payload(doctype))

    report = run_erpnext_metadata_preflight(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
    )

    assert report.status == "complete"
    assert report.attempted_gets == 4
    assert report.company_count == 2
    assert report.doctype_catalog_count == 2
    assert report.metadata_succeeded == 2
    assert report.metadata_failed == 0
    assert report.sensitive_metadata_excluded == 1
    assert report.candidate_entity_count == 1
    assert report.record_samples == 0
    assert report.erp_writes == 0
    assert report.soak_started is False

    candidate_path, = plan.state_directory.glob("metadata-candidate-*.json")
    report_path, = plan.report_directory.glob("metadata-preflight-report-*.json")
    candidate_text = candidate_path.read_text()
    report_text = report_path.read_text()
    candidate = json.loads(candidate_text)
    aggregate = json.loads(report_text)
    assert candidate == {
        "candidate_scopes": {"Safe Invoice": ["metric"]},
        "companies": ["Company A", "Company B"],
        "company_catalog_complete": True,
        "doctype_catalog_complete": True,
        "review_required": True,
        "schema_version": 1,
    }
    assert aggregate["company_count"] == 2
    assert aggregate["candidate_entity_count"] == 1
    assert "companies" not in aggregate
    assert "candidate_scopes" not in aggregate
    for forbidden in (
        SECRET_KEY,
        SECRET_VALUE,
        "RAW LABEL MUST NOT PERSIST",
        "RAW SECRET LABEL MUST NOT PERSIST",
        "RAW DEFAULT MUST NOT PERSIST",
        "Synthetic Raw Module",
    ):
        assert forbidden not in candidate_text
        assert forbidden not in report_text
    assert stat.S_IMODE(candidate_path.stat().st_mode) == 0o600
    assert stat.S_IMODE(report_path.stat().st_mode) == 0o600
    assert all(request.method == "GET" for request in requests)
    assert {
        urlparse(request.full_url).path for request in requests
    } <= {
        "/api/resource/Company",
        "/api/resource/DocType",
        "/api/method/frappe.desk.form.load.getdoctype",
    }


def test_sensitive_doctype_is_skipped_before_metadata_get(tmp_path):
    plan = config(tmp_path)
    requests = []

    def opener(request, *, timeout):
        requests.append(request)
        path = urlparse(request.full_url).path
        if path == "/api/resource/Company":
            return FakeResponse(request, catalog_payload("Company A"))
        if path == "/api/resource/DocType":
            return FakeResponse(
                request,
                catalog_payload("API Token Log", "Safe Invoice"),
            )
        doctype = parse_qs(urlparse(request.full_url).query)["doctype"][0]
        assert doctype == "Safe Invoice"
        return FakeResponse(request, metadata_payload(doctype))

    report = run_erpnext_metadata_preflight(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
    )

    assert report.status == "complete"
    assert report.attempted_gets == 3
    assert report.doctype_catalog_count == 2
    assert report.metadata_succeeded == 1
    assert report.metadata_failed == 0
    assert report.sensitive_metadata_excluded == 1
    metadata_targets = [
        parse_qs(urlparse(request.full_url).query)["doctype"][0]
        for request in requests
        if urlparse(request.full_url).path
        == "/api/method/frappe.desk.form.load.getdoctype"
    ]
    assert metadata_targets == ["Safe Invoice"]


@pytest.mark.parametrize(
    "metadata",
    (
        {"apiKey": {"value": "nested-secret"}},
        {"items": [{"authToken": "nested-token"}]},
        {"nested": [{"password": ["list-secret"]}]},
        {
            "fields": [
                {
                    "fieldname": "privateKey",
                    "fieldtype": "Data",
                    "default": {"value": "embedded-secret"},
                }
            ]
        },
    ),
)
def test_sensitive_embedded_mapping_list_and_camel_case_values_are_excluded(metadata):
    assert _contains_sensitive_embedded_value(metadata) is True


def test_prior_durable_state_prevents_rerun_without_new_transport(tmp_path):
    plan = config(tmp_path)
    transport_calls = 0

    def opener(request, *, timeout):
        nonlocal transport_calls
        transport_calls += 1
        path = urlparse(request.full_url).path
        if path == "/api/resource/Company":
            return FakeResponse(request, catalog_payload("Company A"))
        if path == "/api/resource/DocType":
            return FakeResponse(request, catalog_payload("Safe Invoice"))
        assert path == "/api/method/frappe.desk.form.load.getdoctype"
        return FakeResponse(request, metadata_payload("Safe Invoice"))

    first = run_erpnext_metadata_preflight(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
    )
    assert first.status == "complete"
    calls_after_first_run = transport_calls

    readiness = inspect_metadata_preflight_readiness(
        plan,
        environment=SECRET_ENVIRONMENT,
    )
    assert readiness.prior_run_present is True
    assert readiness.prior_run_status == "complete"
    assert readiness.ready_for_metadata_preflight is False
    assert readiness.offline_candidate_ready is True
    with pytest.raises(MetadataPreflightError, match="durable state"):
        run_erpnext_metadata_preflight(
            plan,
            environment=SECRET_ENVIRONMENT,
            opener=opener,
            clock=lambda: NOW,
        )
    assert transport_calls == calls_after_first_run


def test_interruption_is_durable_and_charged_attempt_is_not_reused(tmp_path):
    plan = config(tmp_path)
    termination_checks = iter((False, True))
    transport_calls = 0

    def opener(request, *, timeout):
        nonlocal transport_calls
        transport_calls += 1
        return FakeResponse(request, catalog_payload("Company A"))

    report = run_erpnext_metadata_preflight(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
        termination_requested=lambda: next(termination_checks),
    )

    assert report.status == "interrupted"
    assert report.attempted_gets == 1
    assert transport_calls == 1
    readiness = inspect_metadata_preflight_readiness(
        plan,
        environment=SECRET_ENVIRONMENT,
    )
    assert readiness.prior_run_present is True
    assert readiness.prior_run_status == "interrupted"
    assert readiness.attempted_gets == 1
    assert readiness.ready_for_metadata_preflight is False
    with pytest.raises(MetadataPreflightError, match="durable state"):
        run_erpnext_metadata_preflight(
            plan,
            environment=SECRET_ENVIRONMENT,
            opener=opener,
            clock=lambda: NOW,
        )
    assert transport_calls == 1


def test_empty_company_catalog_fails_without_offline_candidate(tmp_path):
    plan = config(tmp_path)
    requests = []

    def opener(request, *, timeout):
        requests.append(request)
        return FakeResponse(request, catalog_payload())

    report = run_erpnext_metadata_preflight(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
    )

    assert report.status == "failed"
    assert report.attempted_gets == 1
    assert report.company_count == 0
    assert len(requests) == 1
    readiness = inspect_metadata_preflight_readiness(
        plan,
        environment=SECRET_ENVIRONMENT,
    )
    assert readiness.prior_run_status == "failed"
    assert readiness.offline_candidate_ready is False
    assert readiness.ready_for_metadata_preflight is False


def test_incomplete_company_catalog_never_yields_offline_candidate(tmp_path):
    plan = config(tmp_path)
    company_names = tuple(f"Company {index:03d}" for index in range(500))

    def opener(request, *, timeout):
        path = urlparse(request.full_url).path
        if path == "/api/resource/Company":
            return FakeResponse(request, catalog_payload(*company_names))
        if path == "/api/resource/DocType":
            return FakeResponse(request, catalog_payload("Safe Invoice"))
        return FakeResponse(request, metadata_payload("Safe Invoice"))

    report = run_erpnext_metadata_preflight(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
    )

    assert report.status == "complete"
    assert report.company_count == 499
    assert report.company_catalog_complete is False
    assert report.doctype_catalog_complete is True
    readiness = inspect_metadata_preflight_readiness(
        plan,
        environment=SECRET_ENVIRONMENT,
    )
    assert readiness.offline_candidate_ready is False
    assert readiness.candidate_review_required is True


def test_multiple_candidate_scopes_validate_against_ledger_counts(tmp_path):
    plan = config(tmp_path)
    report, _ = run_valid_preflight(
        plan,
        doctypes=("Safe Invoice", "Safe Order"),
    )
    assert report.candidate_entity_count == 2
    assert report.candidate_field_count == 2

    readiness = inspect_metadata_preflight_readiness(
        plan,
        environment=SECRET_ENVIRONMENT,
    )
    assert readiness.prior_run_status == "complete"
    assert readiness.offline_candidate_ready is True
    assert readiness.candidate_review_required is True


def test_corrupt_candidate_is_uncertain_and_cannot_enable_or_repeat_run(tmp_path):
    plan = config(tmp_path)
    report, _ = run_valid_preflight(plan)
    assert report.status == "complete"
    candidate_path, = plan.state_directory.glob("metadata-candidate-*.json")
    candidate = json.loads(candidate_path.read_text())
    candidate["unexpected"] = "forged"
    candidate_path.write_text(json.dumps(candidate))
    candidate_path.chmod(0o600)

    readiness = inspect_metadata_preflight_readiness(
        plan,
        environment=SECRET_ENVIRONMENT,
    )
    assert readiness.prior_run_present is True
    assert readiness.prior_run_status == "uncertain"
    assert readiness.offline_candidate_ready is False
    assert readiness.ready_for_metadata_preflight is False

    transport_calls = 0

    def opener(request, *, timeout):
        nonlocal transport_calls
        transport_calls += 1
        pytest.fail("corrupt prior state must block transport")

    with pytest.raises(MetadataPreflightError, match="durable state"):
        run_erpnext_metadata_preflight(
            plan,
            environment=SECRET_ENVIRONMENT,
            opener=opener,
            clock=lambda: NOW,
        )
    assert transport_calls == 0


def test_orphan_candidate_blocks_direct_rerun_before_transport(tmp_path):
    plan = config(tmp_path)
    candidate_path = _candidate_path(plan)
    candidate_path.write_text(
        json.dumps(
            {
                "candidate_scopes": {"Safe Invoice": ["metric"]},
                "companies": ["Company A"],
                "company_catalog_complete": True,
                "doctype_catalog_complete": True,
                "review_required": True,
                "schema_version": 1,
            }
        )
    )
    candidate_path.chmod(0o600)
    readiness = inspect_metadata_preflight_readiness(
        plan,
        environment=SECRET_ENVIRONMENT,
    )
    assert readiness.prior_run_present is True
    assert readiness.prior_run_status == "uncertain"
    assert readiness.ready_for_metadata_preflight is False

    transport_calls = 0

    def opener(request, *, timeout):
        nonlocal transport_calls
        transport_calls += 1
        return FakeResponse(request, catalog_payload())

    with pytest.raises(MetadataPreflightError, match="prior|candidate|state"):
        run_erpnext_metadata_preflight(
            plan,
            environment=SECRET_ENVIRONMENT,
            opener=opener,
            clock=lambda: NOW,
        )
    assert transport_calls == 0


def test_broken_candidate_symlink_is_uncertain_and_blocks_transport(tmp_path):
    plan = config(tmp_path)
    candidate_path = _candidate_path(plan)
    candidate_path.symlink_to(tmp_path / "missing-candidate-target")

    readiness = inspect_metadata_preflight_readiness(
        plan,
        environment=SECRET_ENVIRONMENT,
    )
    assert readiness.prior_run_present is True
    assert readiness.prior_run_status == "uncertain"
    assert readiness.offline_candidate_ready is False
    assert readiness.ready_for_metadata_preflight is False

    transport_calls = 0

    def opener(request, *, timeout):
        nonlocal transport_calls
        transport_calls += 1
        pytest.fail("broken prior symlink must block transport")

    with pytest.raises(MetadataPreflightError, match="prior durable state"):
        run_erpnext_metadata_preflight(
            plan,
            environment=SECRET_ENVIRONMENT,
            opener=opener,
            clock=lambda: NOW,
        )
    assert transport_calls == 0


def test_default_cli_is_offline_and_generic_execute_flag_is_unavailable(
    tmp_path,
    monkeypatch,
    capsys,
):
    plan = config(tmp_path)
    environment = {
        "ORION_LIVE_BASE_URL": plan.base_url,
        "ORION_LIVE_TENANT_ID": TENANT,
        "ORION_LIVE_AUTHORIZATION_REFERENCE": "synthetic-metadata-only-ledger",
        "ORION_LIVE_API_KEY_REF": KEY_REF,
        "ORION_LIVE_API_SECRET_REF": SECRET_REF,
        "ORION_LIVE_STATE_DIR": str(plan.state_directory),
        "ORION_LIVE_REPORT_DIR": str(plan.report_directory),
        **SECRET_ENVIRONMENT,
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)

    def forbidden_network(*args, **kwargs):
        pytest.fail("default readiness must not perform network I/O")

    monkeypatch.setattr(preflight_module, "_default_opener", forbidden_network)
    assert main([]) == 0
    output = json.loads(capsys.readouterr().out)
    assert output["execution_allowed"] is False
    assert output["ready_for_metadata_preflight"] is True
    assert output["record_sampling_allowed"] is False
    assert output["soak_allowed"] is False
    assert output["erp_writes_allowed"] is False
    assert not list(plan.state_directory.iterdir())
    assert not list(plan.report_directory.iterdir())

    with pytest.raises(SystemExit) as error:
        main(["--execute"])
    assert error.value.code == 2
    assert not list(plan.state_directory.iterdir())
    assert not list(plan.report_directory.iterdir())


def test_full_single_pages_report_truncation_and_never_exceed_global_budget(tmp_path):
    plan = config(tmp_path)
    company_names = tuple(f"Company {index:03d}" for index in range(500))
    doctype_names = tuple(f"Synthetic Type {index:03d}" for index in range(99))
    requests = []

    def opener(request, *, timeout):
        requests.append(request)
        path = urlparse(request.full_url).path
        if path == "/api/resource/Company":
            return FakeResponse(request, catalog_payload(*company_names))
        if path == "/api/resource/DocType":
            return FakeResponse(request, catalog_payload(*doctype_names))
        assert path == "/api/method/frappe.desk.form.load.getdoctype"
        doctype = unquote(parse_qs(urlparse(request.full_url).query)["doctype"][0])
        return FakeResponse(request, metadata_payload(doctype, raw_label="discarded"))

    report = run_erpnext_metadata_preflight(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
    )

    assert report.status == "complete"
    assert report.attempted_gets == 100
    assert len(requests) == 100
    assert report.company_count == 499
    assert report.company_catalog_complete is False
    assert report.doctype_catalog_count == 98
    assert report.doctype_catalog_complete is False
    assert report.metadata_succeeded == 98
    assert report.metadata_failed == 0
    candidate_path, = plan.state_directory.glob("metadata-candidate-*.json")
    candidate = json.loads(candidate_path.read_text())
    assert len(candidate["companies"]) == 499
    assert len(candidate["candidate_scopes"]) == 98
    assert candidate["company_catalog_complete"] is False
    assert candidate["doctype_catalog_complete"] is False
    readiness = inspect_metadata_preflight_readiness(
        plan,
        environment=SECRET_ENVIRONMENT,
    )
    assert readiness.offline_candidate_ready is False
    assert readiness.candidate_review_required is True


def test_nested_state_and_report_paths_fail_during_direct_and_environment_config(tmp_path):
    state = tmp_path / "state"
    reports = state / "reports"
    state.mkdir(mode=0o700)
    reports.mkdir(mode=0o700)
    kwargs = {
        "base_url": "https://synthetic.invalid",
        "tenant_id": TENANT,
        "authorization_reference": "synthetic-metadata-only-ledger",
        "credential_references": CredentialEnvironmentReferences(KEY_REF, SECRET_REF),
        "state_directory": state,
        "report_directory": reports,
    }
    with pytest.raises(LiveSessionStorageError, match="isolated"):
        ERPNextMetadataPreflightConfig(**kwargs)

    environment = {
        "ORION_LIVE_BASE_URL": "https://synthetic.invalid",
        "ORION_LIVE_TENANT_ID": TENANT,
        "ORION_LIVE_AUTHORIZATION_REFERENCE": "synthetic-metadata-only-ledger",
        "ORION_LIVE_API_KEY_REF": KEY_REF,
        "ORION_LIVE_API_SECRET_REF": SECRET_REF,
        "ORION_LIVE_STATE_DIR": str(state),
        "ORION_LIVE_REPORT_DIR": str(reports),
    }
    with pytest.raises(LiveSessionStorageError, match="isolated"):
        metadata_preflight_config_from_environment(environment)


@pytest.mark.parametrize(
    "forged",
    (
        {"execution_allowed": True},
        {"record_samples": 1},
        {"erp_writes": 1},
        {"soak_started": True},
        {"attempted_gets": 2, "metadata_succeeded": 2, "metadata_failed": 1},
        {"candidate_entity_count": 2},
        {"company_count": 500},
        {"doctype_catalog_count": 99},
        {"failure_stage": "private-stage"},
        {"failure_category": "private exception text"},
        {"status": "failed", "failure_stage": "none", "failure_category": "none"},
    ),
    ids=(
        "execution-authority",
        "record-samples",
        "erp-writes",
        "soak-started",
        "metadata-over-attempts",
        "candidate-over-successes",
        "company-over-bound",
        "doctype-over-bound",
        "unallowlisted-stage",
        "unallowlisted-category",
        "failed-without-category",
    ),
)
def test_run_report_rejects_forged_authority_and_inconsistent_counts(forged):
    valid = MetadataPreflightRunReport(
        execution_allowed=False,
        status="complete",
        failure_stage="none",
        failure_category="none",
        started_at=NOW,
        ended_at=NOW,
        attempted_gets=3,
        max_total_attempted_gets=100,
        company_count=1,
        company_catalog_complete=True,
        doctype_catalog_count=1,
        doctype_catalog_complete=True,
        metadata_succeeded=1,
        metadata_failed=0,
        sensitive_metadata_excluded=0,
        candidate_entity_count=1,
        candidate_field_count=1,
        candidate_review_required=True,
    )

    with pytest.raises((MetadataPreflightError, TypeError)):
        replace(valid, **forged)


def test_doctype_catalog_retry_uses_only_attempt_three_and_preserves_failed_state(tmp_path):
    plan = config(tmp_path)
    ledger = failed_doctype_catalog_ledger(plan)
    prior_report = plan.report_directory / "metadata-preflight-report-original.json"
    prior_report.write_text('{"status":"failed"}\n')
    prior_report.chmod(0o600)
    prior_bytes = prior_report.read_bytes()
    requests = []

    def opener(request, *, timeout):
        requests.append(request)
        snapshot = ledger.snapshot()
        assert snapshot["status"] == "failed"
        assert snapshot["attempted_gets"] == 3
        return FakeResponse(request, catalog_payload("Alpha Type", "Zulu Type"))

    report = run_erpnext_doctype_catalog_retry_once(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
    )

    assert report == MetadataCatalogRetryReport(
        execution_allowed=False,
        status="succeeded",
        failure_stage="none",
        failure_category="none",
        started_at=NOW,
        ended_at=NOW,
        attempted_gets=3,
        request_attempts=1,
        max_total_attempted_gets=100,
        doctype_catalog_count=2,
        doctype_catalog_complete=True,
    )
    assert prior_report.read_bytes() == prior_bytes
    assert ledger.snapshot() == {
        "status": "failed",
        "attempted_gets": 3,
        "company_count": 1,
        "doctype_catalog_count": 0,
        "metadata_succeeded": 0,
        "metadata_failed": 0,
        "candidate_entity_count": 0,
        "candidate_field_count": 0,
        "company_catalog_complete": 1,
        "doctype_catalog_complete": 0,
    }
    assert len(requests) == 1
    request = requests[0]
    parsed = urlparse(request.full_url)
    assert request.method == "GET"
    assert parsed.path == "/api/resource/DocType"
    assert parse_qs(parsed.query) == {
        "fields": ['["name"]'],
        "limit_start": ["0"],
        "limit_page_length": ["99"],
        "order_by": ["name asc"],
    }
    assert request.get_header("Authorization") == f"token {SECRET_KEY}:{SECRET_VALUE}"
    retry_path = _retry_report_path(plan)
    rendered = retry_path.read_text()
    assert stat.S_IMODE(retry_path.stat().st_mode) == 0o600
    assert json.loads(rendered)["doctype_catalog_count"] == 2
    for forbidden in (SECRET_KEY, SECRET_VALUE, "Alpha Type", "Zulu Type", plan.base_url):
        assert forbidden not in rendered
    assert not list(plan.state_directory.glob("metadata-candidate-*.json"))


def test_doctype_catalog_retry_reports_sanitized_http_permission_once(tmp_path):
    plan = config(tmp_path)
    ledger = failed_doctype_catalog_ledger(plan)
    calls = 0

    def opener(request, *, timeout):
        nonlocal calls
        calls += 1
        raise HTTPError(request.full_url, 403, "RAW SERVER DENIAL", {}, None)

    report = run_erpnext_doctype_catalog_retry_once(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
    )

    assert report.status == "failed"
    assert report.failure_stage == "doctype_catalog"
    assert report.failure_category == "http_permission"
    assert report.attempted_gets == 3
    assert report.request_attempts == 1
    assert calls == 1
    assert ledger.snapshot()["status"] == "failed"
    assert ledger.snapshot()["attempted_gets"] == 3
    rendered = _retry_report_path(plan).read_text()
    for forbidden in ("RAW SERVER DENIAL", SECRET_KEY, SECRET_VALUE, plan.base_url, "403"):
        assert forbidden not in rendered


def test_doctype_catalog_retry_requires_credentials_before_reservation(tmp_path):
    plan = config(tmp_path)
    ledger = failed_doctype_catalog_ledger(plan)

    with pytest.raises(LiveSessionError, match="credential"):
        run_erpnext_doctype_catalog_retry_once(
            plan,
            environment={},
            opener=lambda *args, **kwargs: pytest.fail("credentials must block transport"),
            clock=lambda: NOW,
        )

    assert ledger.snapshot()["attempted_gets"] == 2
    assert not _retry_report_path(plan).exists()


def test_doctype_catalog_retry_cli_exposes_only_aggregate_result(tmp_path, monkeypatch, capsys):
    plan = config(tmp_path)
    failed_doctype_catalog_ledger(plan)
    environment = {
        "ORION_LIVE_BASE_URL": plan.base_url,
        "ORION_LIVE_TENANT_ID": TENANT,
        "ORION_LIVE_AUTHORIZATION_REFERENCE": "synthetic-metadata-only-ledger",
        "ORION_LIVE_API_KEY_REF": KEY_REF,
        "ORION_LIVE_API_SECRET_REF": SECRET_REF,
        "ORION_LIVE_STATE_DIR": str(plan.state_directory),
        "ORION_LIVE_REPORT_DIR": str(plan.report_directory),
        **SECRET_ENVIRONMENT,
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    requests = []

    def opener(request, *, timeout):
        requests.append(request)
        return FakeResponse(request, catalog_payload("Do Not Print This Name"))

    monkeypatch.setattr(preflight_module, "_default_opener", opener)
    assert main(["--retry-doctype-catalog-once"]) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "succeeded"
    assert payload["attempted_gets"] == 3
    assert payload["request_attempts"] == 1
    assert payload["doctype_catalog_count"] == 1
    assert "Do Not Print This Name" not in output
    assert SECRET_KEY not in output
    assert SECRET_VALUE not in output
    assert len(requests) == 1


@pytest.mark.parametrize(
    ("status", "attempts"),
    (("running", 2), ("failed", 1), ("failed", 3), ("interrupted", 2)),
)
def test_doctype_catalog_retry_refuses_unexpected_ledger_before_transport(
    tmp_path,
    status,
    attempts,
):
    plan = config(tmp_path)
    ledger = _RunLedger.create(_ledger_path(plan))
    for _ in range(attempts):
        ledger.reserve_attempt()
    ledger.update(
        status=status,
        company_count=1,
        company_catalog_complete=1,
    )
    calls = 0

    def opener(request, *, timeout):
        nonlocal calls
        calls += 1
        pytest.fail("invalid recovery state must block transport")

    with pytest.raises(MetadataPreflightError, match="retry"):
        run_erpnext_doctype_catalog_retry_once(
            plan,
            environment=SECRET_ENVIRONMENT,
            opener=opener,
            clock=lambda: NOW,
        )
    assert calls == 0
    assert ledger.snapshot()["attempted_gets"] == attempts


def test_doctype_catalog_retry_refuses_candidate_or_existing_result_before_transport(tmp_path):
    plan = config(tmp_path)
    ledger = failed_doctype_catalog_ledger(plan)
    candidate = _candidate_path(plan)
    candidate.write_text("{}")
    candidate.chmod(0o600)
    calls = 0

    def opener(request, *, timeout):
        nonlocal calls
        calls += 1
        pytest.fail("conflicting artifact must block transport")

    with pytest.raises(MetadataPreflightError, match="candidate"):
        run_erpnext_doctype_catalog_retry_once(
            plan,
            environment=SECRET_ENVIRONMENT,
            opener=opener,
            clock=lambda: NOW,
        )
    assert calls == 0
    assert ledger.snapshot()["attempted_gets"] == 2
    candidate.unlink()
    retry_path = _retry_report_path(plan)
    retry_path.write_text("{}")
    retry_path.chmod(0o600)
    with pytest.raises(MetadataPreflightError, match="result"):
        run_erpnext_doctype_catalog_retry_once(
            plan,
            environment=SECRET_ENVIRONMENT,
            opener=opener,
            clock=lambda: NOW,
        )
    assert calls == 0
    assert ledger.snapshot()["attempted_gets"] == 2


def test_doctype_catalog_retry_reservation_blocks_concurrent_transport(tmp_path):
    plan = config(tmp_path)
    ledger = failed_doctype_catalog_ledger(plan)
    entered = threading.Event()
    release = threading.Event()
    results = []
    calls = 0

    def first_opener(request, *, timeout):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=5)
        return FakeResponse(request, catalog_payload("Safe Type"))

    def first_run():
        results.append(
            run_erpnext_doctype_catalog_retry_once(
                plan,
                environment=SECRET_ENVIRONMENT,
                opener=first_opener,
                clock=lambda: NOW,
            )
        )

    worker = threading.Thread(target=first_run)
    worker.start()
    assert entered.wait(timeout=5)
    with pytest.raises(MetadataPreflightError, match="retry"):
        run_erpnext_doctype_catalog_retry_once(
            plan,
            environment=SECRET_ENVIRONMENT,
            opener=lambda *args, **kwargs: pytest.fail("no second transport"),
            clock=lambda: NOW,
        )
    release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert calls == 1
    assert len(results) == 1
    assert results[0].status == "succeeded"
    assert ledger.snapshot()["attempted_gets"] == 3


def test_doctype_catalog_retry_interruption_spends_attempt_and_cannot_repeat(tmp_path):
    plan = config(tmp_path)
    ledger = failed_doctype_catalog_ledger(plan)
    calls = 0

    def opener(request, *, timeout):
        nonlocal calls
        calls += 1
        raise KeyboardInterrupt

    report = run_erpnext_doctype_catalog_retry_once(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
    )
    assert report.status == "interrupted"
    assert report.failure_category == "interrupted"
    assert ledger.snapshot()["attempted_gets"] == 3
    with pytest.raises(MetadataPreflightError, match="retry"):
        run_erpnext_doctype_catalog_retry_once(
            plan,
            environment=SECRET_ENVIRONMENT,
            opener=opener,
            clock=lambda: NOW,
        )
    assert calls == 1


def test_doctype_catalog_retry_latched_interruption_spends_without_transport(tmp_path):
    plan = config(tmp_path)
    ledger = failed_doctype_catalog_ledger(plan)
    report = run_erpnext_doctype_catalog_retry_once(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=lambda *args, **kwargs: pytest.fail("latched stop must block transport"),
        clock=lambda: NOW,
        termination_requested=lambda: True,
    )

    assert report.status == "interrupted"
    assert report.failure_category == "interrupted"
    assert ledger.snapshot()["attempted_gets"] == 3


def test_permission_retry_uses_only_attempt_four_and_preserves_prior_report(tmp_path):
    plan = config(tmp_path)
    ledger, prior_path, prior_bytes = failed_permission_retry(plan)
    requests = []

    def opener(request, *, timeout):
        requests.append(request)
        assert ledger.snapshot()["attempted_gets"] == 4
        assert ledger.snapshot()["status"] == "failed"
        return FakeResponse(request, catalog_payload("Alpha Type", "Zulu Type"))

    report = run_erpnext_doctype_catalog_permission_retry_once(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
    )

    assert report == MetadataCatalogRetryReport(
        execution_allowed=False,
        status="succeeded",
        failure_stage="none",
        failure_category="none",
        started_at=NOW,
        ended_at=NOW,
        attempted_gets=4,
        request_attempts=1,
        max_total_attempted_gets=100,
        doctype_catalog_count=2,
        doctype_catalog_complete=True,
    )
    assert prior_path.read_bytes() == prior_bytes
    assert ledger.snapshot()["attempted_gets"] == 4
    assert ledger.snapshot()["status"] == "failed"
    assert len(requests) == 1
    parsed = urlparse(requests[0].full_url)
    assert requests[0].method == "GET"
    assert parsed.path == "/api/resource/DocType"
    assert parse_qs(parsed.query) == {
        "fields": ['["name"]'],
        "limit_start": ["0"],
        "limit_page_length": ["99"],
        "order_by": ["name asc"],
    }
    result_path = _permission_retry_report_path(plan)
    rendered = result_path.read_text()
    assert stat.S_IMODE(result_path.stat().st_mode) == 0o600
    for forbidden in (SECRET_KEY, SECRET_VALUE, "Alpha Type", "Zulu Type", plan.base_url):
        assert forbidden not in rendered


def test_permission_retry_reports_sanitized_endpoint_contract_once(tmp_path):
    plan = config(tmp_path)
    ledger, prior_path, prior_bytes = failed_permission_retry(plan)
    calls = 0

    def opener(request, *, timeout):
        nonlocal calls
        calls += 1
        raise HTTPError(request.full_url, 404, "RAW NOT FOUND", {}, None)

    report = run_erpnext_doctype_catalog_permission_retry_once(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: NOW,
    )

    assert report.status == "failed"
    assert report.failure_category == "endpoint_contract"
    assert report.attempted_gets == 4
    assert report.request_attempts == 1
    assert calls == 1
    assert ledger.snapshot()["attempted_gets"] == 4
    assert prior_path.read_bytes() == prior_bytes
    rendered = _permission_retry_report_path(plan).read_text()
    for forbidden in ("RAW NOT FOUND", SECRET_KEY, SECRET_VALUE, plan.base_url, "404"):
        assert forbidden not in rendered


@pytest.mark.parametrize("mutation", ("missing", "wrong-category", "permissive"))
def test_permission_retry_refuses_invalid_prior_report_before_transport(tmp_path, mutation):
    plan = config(tmp_path)
    ledger, prior_path, _ = failed_permission_retry(plan)
    if mutation == "missing":
        prior_path.unlink()
    elif mutation == "wrong-category":
        payload = json.loads(prior_path.read_text())
        payload["failure_category"] = "response_validation"
        prior_path.write_text(json.dumps(payload))
        prior_path.chmod(0o600)
    else:
        prior_path.chmod(0o644)
    calls = 0

    def opener(request, *, timeout):
        nonlocal calls
        calls += 1
        pytest.fail("invalid prior result must block transport")

    with pytest.raises(MetadataPreflightError, match="prior"):
        run_erpnext_doctype_catalog_permission_retry_once(
            plan,
            environment=SECRET_ENVIRONMENT,
            opener=opener,
            clock=lambda: NOW,
        )
    assert calls == 0
    assert ledger.snapshot()["attempted_gets"] == 3
    assert not _permission_retry_report_path(plan).exists()


def test_permission_retry_reservation_blocks_concurrent_transport(tmp_path):
    plan = config(tmp_path)
    ledger, _, _ = failed_permission_retry(plan)
    entered = threading.Event()
    release = threading.Event()
    reports = []
    calls = 0

    def first_opener(request, *, timeout):
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(timeout=5)
        return FakeResponse(request, catalog_payload("Safe Type"))

    def first_run():
        reports.append(
            run_erpnext_doctype_catalog_permission_retry_once(
                plan,
                environment=SECRET_ENVIRONMENT,
                opener=first_opener,
                clock=lambda: NOW,
            )
        )

    worker = threading.Thread(target=first_run)
    worker.start()
    assert entered.wait(timeout=5)
    with pytest.raises(MetadataPreflightError, match="retry"):
        run_erpnext_doctype_catalog_permission_retry_once(
            plan,
            environment=SECRET_ENVIRONMENT,
            opener=lambda *args, **kwargs: pytest.fail("no second transport"),
            clock=lambda: NOW,
        )
    release.set()
    worker.join(timeout=5)
    assert not worker.is_alive()
    assert calls == 1
    assert len(reports) == 1
    assert reports[0].status == "succeeded"
    assert ledger.snapshot()["attempted_gets"] == 4


def test_permission_retry_requires_credentials_before_attempt_four(tmp_path):
    plan = config(tmp_path)
    ledger, prior_path, prior_bytes = failed_permission_retry(plan)
    with pytest.raises(LiveSessionError, match="credential"):
        run_erpnext_doctype_catalog_permission_retry_once(
            plan,
            environment={},
            opener=lambda *args, **kwargs: pytest.fail("credentials must block transport"),
            clock=lambda: NOW,
        )
    assert ledger.snapshot()["attempted_gets"] == 3
    assert prior_path.read_bytes() == prior_bytes
    assert not _permission_retry_report_path(plan).exists()


def test_permission_retry_cli_exposes_only_aggregate_result(tmp_path, monkeypatch, capsys):
    plan = config(tmp_path)
    failed_permission_retry(plan)
    environment = {
        "ORION_LIVE_BASE_URL": plan.base_url,
        "ORION_LIVE_TENANT_ID": TENANT,
        "ORION_LIVE_AUTHORIZATION_REFERENCE": "synthetic-metadata-only-ledger",
        "ORION_LIVE_API_KEY_REF": KEY_REF,
        "ORION_LIVE_API_SECRET_REF": SECRET_REF,
        "ORION_LIVE_STATE_DIR": str(plan.state_directory),
        "ORION_LIVE_REPORT_DIR": str(plan.report_directory),
        **SECRET_ENVIRONMENT,
    }
    for name, value in environment.items():
        monkeypatch.setenv(name, value)
    requests = []

    def opener(request, *, timeout):
        requests.append(request)
        return FakeResponse(request, catalog_payload("Do Not Print This Name"))

    monkeypatch.setattr(preflight_module, "_default_opener", opener)
    assert main(["--retry-doctype-catalog-after-permission-once"]) == 0
    output = capsys.readouterr().out
    payload = json.loads(output)
    assert payload["status"] == "succeeded"
    assert payload["attempted_gets"] == 4
    assert payload["request_attempts"] == 1
    assert payload["doctype_catalog_count"] == 1
    assert "Do Not Print This Name" not in output
    assert SECRET_KEY not in output
    assert SECRET_VALUE not in output
    assert len(requests) == 1


def test_permission_retry_latched_interruption_spends_without_transport(tmp_path):
    plan = config(tmp_path)
    ledger, prior_path, prior_bytes = failed_permission_retry(plan)
    report = run_erpnext_doctype_catalog_permission_retry_once(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=lambda *args, **kwargs: pytest.fail("latched stop must block transport"),
        clock=lambda: NOW,
        termination_requested=lambda: True,
    )
    assert report.status == "interrupted"
    assert report.failure_category == "interrupted"
    assert ledger.snapshot()["attempted_gets"] == 4
    assert prior_path.read_bytes() == prior_bytes


def reviewed_catalog(plan, doctypes=("Safe Invoice",)):
    return preflight_module.ReviewedAdministratorCatalog(
        base_url=plan.base_url,
        tenant_id=plan.tenant_id,
        authorization_reference=plan.authorization_reference,
        doctypes=doctypes,
        review_reference="synthetic-administrator-review",
    )


def failed_attempt_four(plan):
    ledger, prior_path, prior_bytes = failed_permission_retry(plan)
    result = run_erpnext_doctype_catalog_permission_retry_once(
        plan,
        environment=SECRET_ENVIRONMENT,
        opener=lambda request, **kwargs: FakeResponse(request, catalog_payload("Safe Invoice")),
        clock=lambda: NOW,
    )
    assert result.status == "succeeded"
    return ledger, {prior_path: prior_bytes, _permission_retry_report_path(plan): _permission_retry_report_path(plan).read_bytes()}


def test_reviewed_catalog_continues_shared_pipeline_and_preserves_history(tmp_path):
    plan = config(tmp_path)
    ledger, prior = failed_attempt_four(plan)
    requests = []

    def opener(request, *, timeout):
        requests.append(request)
        assert ledger.snapshot()["attempted_gets"] == 4 + len(requests)
        assert request.get_method() == "GET"
        if urlparse(request.full_url).path == "/api/resource/Company":
            return FakeResponse(request, catalog_payload("Company A"))
        assert "/api/resource/DocType" not in request.full_url
        return FakeResponse(request, metadata_payload("Safe Invoice"))

    report = run_erpnext_metadata_preflight(
        plan, reviewed_catalog=reviewed_catalog(plan), environment=SECRET_ENVIRONMENT,
        opener=opener, clock=lambda: NOW,
    )
    assert report.status == "complete"
    assert report.attempted_gets == 6
    assert report.prior_attempted_gets == 4
    assert report.catalog_source == "reviewed_selection"
    assert report.doctype_catalog_complete is False
    assert report.candidate_entity_count == 1
    assert report.candidate_field_count == 1
    assert report.record_samples == report.erp_writes == 0
    assert report.soak_started is False
    assert all(path.read_bytes() == body for path, body in prior.items())
    candidate = json.loads(_candidate_path(plan).read_text())
    assert candidate["candidate_scopes"] == {"Safe Invoice": ["metric"]}
    assert candidate["companies"] == ["Company A"]
    assert candidate["review_required"] is True
    assert candidate["doctype_catalog_complete"] is False
    readiness = inspect_metadata_preflight_readiness(plan, environment={})
    assert readiness.offline_candidate_ready is True
    assert readiness.execution_allowed is False
    with pytest.raises(MetadataPreflightError):
        run_erpnext_metadata_preflight(
            plan, reviewed_catalog=reviewed_catalog(plan), environment=SECRET_ENVIRONMENT,
            opener=opener,
        )
    assert len(requests) == 2


@pytest.mark.parametrize("field,value", [
    ("base_url", "https://other.invalid"),
    ("tenant_id", "another-tenant"),
    ("authorization_reference", "another-authorization"),
])
def test_reviewed_catalog_binding_rejected_before_transport(tmp_path, field, value):
    plan = config(tmp_path)
    ledger, _ = failed_attempt_four(plan)
    catalog = replace(reviewed_catalog(plan), **{field: value})
    with pytest.raises(MetadataPreflightError, match="authorization"):
        run_erpnext_metadata_preflight(
            plan, reviewed_catalog=catalog, environment=SECRET_ENVIRONMENT,
            opener=lambda *args, **kwargs: pytest.fail("must not contact transport"),
        )
    assert ledger.snapshot()["attempted_gets"] == 4


@pytest.mark.parametrize("names", [(), ["Invoice"], ("Invoice", "Invoice"), ("Z", "A"), (12,), tuple(f"Type {i:03}" for i in range(96))])
def test_reviewed_catalog_invalid_selection_is_rejected(names):
    with pytest.raises((MetadataPreflightError, ValueError, TypeError)):
        preflight_module.ReviewedAdministratorCatalog(
            "https://synthetic.invalid", "tenant", "authorization", names, "review"
        )


def test_reviewed_catalog_is_immutable_and_never_full_site():
    from dataclasses import FrozenInstanceError

    catalog = preflight_module.ReviewedAdministratorCatalog(
        "https://synthetic.invalid", "tenant", "authorization", ("Invoice",), "review"
    )
    with pytest.raises(FrozenInstanceError):
        catalog.doctypes = ("Other",)
    with pytest.raises(MetadataPreflightError):
        replace(catalog, scope_kind="full_site")


def test_reviewed_catalog_failure_and_replay_share_original_allowance(tmp_path):
    plan = config(tmp_path)
    ledger, prior = failed_attempt_four(plan)

    def denied(request, *, timeout):
        raise HTTPError(request.full_url, 403, "private", {}, None)

    report = run_erpnext_metadata_preflight(
        plan, reviewed_catalog=reviewed_catalog(plan), environment=SECRET_ENVIRONMENT,
        opener=denied, clock=lambda: NOW,
    )
    assert report.status == "failed"
    assert report.attempted_gets == 5
    assert report.failure_category == "http_permission"
    assert ledger.snapshot()["status"] == "failed"
    with pytest.raises(MetadataPreflightError):
        run_erpnext_metadata_preflight(
            plan, reviewed_catalog=reviewed_catalog(plan), environment=SECRET_ENVIRONMENT,
            opener=lambda *args, **kwargs: pytest.fail("no replay"),
        )
    assert all(path.read_bytes() == body for path, body in prior.items())


def test_reviewed_catalog_concurrent_claim_blocks_second_transport(tmp_path):
    plan = config(tmp_path)
    ledger, _ = failed_attempt_four(plan)
    entered = threading.Event()
    release = threading.Event()
    outcomes = []

    def opener(request, *, timeout):
        if urlparse(request.full_url).path == "/api/resource/Company":
            entered.set()
            assert release.wait(5)
            return FakeResponse(request, catalog_payload("Company A"))
        return FakeResponse(request, metadata_payload("Safe Invoice"))

    def run():
        outcomes.append(run_erpnext_metadata_preflight(
            plan, reviewed_catalog=reviewed_catalog(plan), environment=SECRET_ENVIRONMENT,
            opener=opener, clock=lambda: NOW,
        ))

    thread = threading.Thread(target=run)
    thread.start()
    try:
        assert entered.wait(5)
        with pytest.raises(MetadataPreflightError):
            run_erpnext_metadata_preflight(
                plan, reviewed_catalog=reviewed_catalog(plan), environment=SECRET_ENVIRONMENT,
                opener=lambda *args, **kwargs: pytest.fail("second transport forbidden"),
            )
    finally:
        release.set()
        thread.join(5)
    assert not thread.is_alive()
    assert outcomes[0].status == "complete"
    assert ledger.snapshot()["attempted_gets"] == 6


def test_reviewed_catalog_maximum_budget_and_sensitive_exclusion(tmp_path):
    plan = config(tmp_path)
    ledger, _ = failed_attempt_four(plan)
    names = tuple(f"Safe Invoice {index:03}" for index in range(95))

    def opener(request, *, timeout):
        if urlparse(request.full_url).path == "/api/resource/Company":
            return FakeResponse(request, catalog_payload("Company A"))
        name = parse_qs(urlparse(request.full_url).query)["doctype"][0]
        return FakeResponse(request, metadata_payload(name))

    report = run_erpnext_metadata_preflight(
        plan, reviewed_catalog=reviewed_catalog(plan, names), environment=SECRET_ENVIRONMENT,
        opener=opener, clock=lambda: NOW,
    )
    assert report.status == "complete"
    assert report.attempted_gets == ledger.snapshot()["attempted_gets"] == 100
    assert report.metadata_succeeded == 95


def test_reviewed_catalog_skips_sensitive_type_without_get(tmp_path):
    plan = config(tmp_path)
    failed_attempt_four(plan)
    calls = []

    def opener(request, *, timeout):
        calls.append(request.full_url)
        if urlparse(request.full_url).path == "/api/resource/Company":
            return FakeResponse(request, catalog_payload("Company A"))
        return FakeResponse(request, metadata_payload("Safe Invoice"))

    report = run_erpnext_metadata_preflight(
        plan, reviewed_catalog=reviewed_catalog(plan, ("API Secret", "Safe Invoice")),
        environment=SECRET_ENVIRONMENT, opener=opener, clock=lambda: NOW,
    )
    assert report.status == "complete"
    assert report.attempted_gets == 6
    assert report.sensitive_metadata_excluded == 1
    assert len(calls) == 2


def test_reviewed_catalog_loader_pins_private_exact_bytes(tmp_path):
    import hashlib
    from dataclasses import asdict

    plan = config(tmp_path)
    catalog = reviewed_catalog(plan)
    body = json.dumps(asdict(catalog)).encode()
    path = tmp_path / "catalog.json"
    path.write_bytes(body)
    path.chmod(0o600)
    digest = hashlib.sha256(body).hexdigest()
    assert preflight_module.load_reviewed_administrator_catalog(path, digest) == catalog
    path.write_bytes(body + b" ")
    with pytest.raises(MetadataPreflightError, match="reviewed bytes"):
        preflight_module.load_reviewed_administrator_catalog(path, digest)


def test_reviewed_catalog_cli_without_execute_remains_offline(tmp_path, monkeypatch, capsys):
    import hashlib
    from dataclasses import asdict

    plan = config(tmp_path)
    ledger, _ = failed_attempt_four(plan)
    catalog = reviewed_catalog(plan)
    path = tmp_path / "catalog.json"
    body = json.dumps(asdict(catalog)).encode()
    path.write_bytes(body)
    path.chmod(0o600)
    monkeypatch.setattr(preflight_module, "metadata_preflight_config_from_environment", lambda env: plan)
    monkeypatch.setattr(preflight_module, "_default_opener", lambda *args, **kwargs: pytest.fail("offline CLI must never contact ERP"))
    args = ["--reviewed-admin-catalog", str(path), "--reviewed-admin-catalog-sha256", hashlib.sha256(body).hexdigest()]
    assert main(args) == 2
    assert json.loads(capsys.readouterr().out)["execution_allowed"] is False
    assert ledger.snapshot()["attempted_gets"] == 4
    assert ledger.snapshot()["status"] == "failed"


def test_reviewed_catalog_candidate_cannot_move_to_another_origin(tmp_path):
    plan = config(tmp_path)
    failed_attempt_four(plan)

    def opener(request, *, timeout):
        if urlparse(request.full_url).path == "/api/resource/Company":
            return FakeResponse(request, catalog_payload("Company A"))
        return FakeResponse(request, metadata_payload("Safe Invoice"))

    run_erpnext_metadata_preflight(
        plan, reviewed_catalog=reviewed_catalog(plan), environment=SECRET_ENVIRONMENT,
        opener=opener, clock=lambda: NOW,
    )
    changed = replace(plan, base_url="https://other.invalid")
    readiness = inspect_metadata_preflight_readiness(changed, environment={})
    assert readiness.offline_candidate_ready is False
    assert readiness.prior_run_status == "uncertain"
