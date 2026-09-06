import json
import stat
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
    LiveSessionStorageError,
)
from orion.discovery.erpnext_metadata_preflight import (
    ERPNextMetadataPreflightConfig,
    MetadataPreflightBudgetError,
    MetadataPreflightError,
    MetadataPreflightRunReport,
    _BudgetedOpener,
    _candidate_path,
    _contains_sensitive_embedded_value,
    _read_name_catalog,
    _RunLedger,
    inspect_metadata_preflight_readiness,
    main,
    metadata_preflight_config_from_environment,
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
