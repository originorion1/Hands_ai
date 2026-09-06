import hashlib
import json
import stat
import threading
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlparse
from urllib.request import Request

import pytest

import orion.discovery.erpnext_metadata_refresh as refresh_module
from orion.discovery.erpnext_live_session import (
    CredentialEnvironmentReferences,
    LiveSessionError,
)
from orion.discovery.erpnext_metadata_preflight import (
    ERPNextMetadataPreflightConfig,
    MetadataPreflightError,
    MetadataPreflightRunReport,
    ReviewedAdministratorCatalog,
    _admin_binding_digest,
    _candidate_path,
    _ledger_path,
    _RunLedger,
    _write_private_json,
    _write_run_report,
)
from orion.discovery.erpnext_metadata_refresh import (
    PRIOR_ATTEMPTED_GETS,
    REFRESH_CUMULATIVE_MAX_GETS,
    REFRESH_REQUEST_GETS,
    _refresh_candidate_path,
    inspect_metadata_refresh_readiness,
    main,
    run_metadata_refresh,
)

NOW = datetime(2026, 9, 6, 13, tzinfo=UTC)
LATER = datetime(2026, 9, 6, 13, 1, tzinfo=UTC)
TENANT = "synthetic-tenant"
KEY_REF = "SYNTHETIC_REFRESH_KEY"
SECRET_REF = "SYNTHETIC_REFRESH_SECRET"
SECRET_KEY = "synthetic-key-never-persist"
SECRET_VALUE = "synthetic-secret-never-persist"
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
        authorization_reference="synthetic-refresh-authorization",
        credential_references=CredentialEnvironmentReferences(KEY_REF, SECRET_REF),
        state_directory=state,
        report_directory=reports,
    )


def catalog(plan: ERPNextMetadataPreflightConfig) -> ReviewedAdministratorCatalog:
    return ReviewedAdministratorCatalog(
        base_url=plan.base_url,
        tenant_id=plan.tenant_id,
        authorization_reference=plan.authorization_reference,
        doctypes=tuple(f"Reviewed Type {index:02}" for index in range(18)),
        review_reference="synthetic-reviewed-catalog",
    )


def metadata_payload(doctype: str, *, include_company: bool = True) -> dict[str, object]:
    fields: list[dict[str, object]] = [
        {
            "fieldname": "metric",
            "fieldtype": "Currency",
            "label": "RAW LABEL MUST NOT PERSIST",
        },
        {
            "fieldname": "api_secret",
            "fieldtype": "Password",
            "default": None,
        },
    ]
    if include_company:
        fields.append(
            {"fieldname": "company", "fieldtype": "Link", "options": "Company"}
        )
    return {
        "message": {
            "docs": [
                {
                    "name": doctype,
                    "module": "Synthetic Raw Module",
                    "fields": fields,
                }
            ]
        },
        "user_settings": {"list_view": True},
    }


def completed_attempt_twenty_four(
    plan: ERPNextMetadataPreflightConfig,
    reviewed: ReviewedAdministratorCatalog,
) -> tuple[_RunLedger, dict[Path, bytes]]:
    ledger = _RunLedger.create(_ledger_path(plan))
    binding = _admin_binding_digest(plan)
    with ledger._connect() as connection:
        connection.execute(
            "UPDATE preflight_run SET status='complete', attempted_gets=24, "
            "company_count=1, doctype_catalog_count=18, metadata_succeeded=18, "
            "metadata_failed=0, candidate_entity_count=0, candidate_field_count=0, "
            "company_catalog_complete=1, doctype_catalog_complete=0 WHERE singleton=1"
        )
        connection.execute(
            "CREATE TABLE admin_catalog_claim (digest TEXT NOT NULL, binding TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO admin_catalog_claim VALUES (?, ?)",
            (reviewed.digest(), binding),
        )
        connection.execute(
            "CREATE TABLE admin_catalog_resume_claim ("
            "catalog_digest TEXT NOT NULL, binding TEXT NOT NULL, "
            "prior_report_digest TEXT NOT NULL)"
        )
        connection.execute(
            "INSERT INTO admin_catalog_resume_claim VALUES (?, ?, ?)",
            (reviewed.digest(), binding, "a" * 64),
        )
    _write_private_json(
        _candidate_path(plan),
        {
            "candidate_scopes": {},
            "companies": ["Company A"],
            "company_catalog_complete": True,
            "doctype_catalog_complete": False,
            "review_required": True,
            "schema_version": 1,
        },
    )
    _write_run_report(
        plan,
        MetadataPreflightRunReport(
            execution_allowed=False,
            status="complete",
            failure_stage="none",
            failure_category="none",
            started_at=NOW,
            ended_at=NOW,
            attempted_gets=24,
            max_total_attempted_gets=100,
            company_count=1,
            company_catalog_complete=True,
            doctype_catalog_count=18,
            doctype_catalog_complete=False,
            metadata_succeeded=18,
            metadata_failed=0,
            sensitive_metadata_excluded=18,
            candidate_entity_count=0,
            candidate_field_count=0,
            candidate_review_required=True,
            catalog_source="reviewed_selection",
            prior_attempted_gets=5,
        ),
    )
    artifacts = {_candidate_path(plan): _candidate_path(plan).read_bytes()}
    for path in plan.report_directory.glob("metadata-preflight-report-*.json"):
        artifacts[path] = path.read_bytes()
    return ledger, artifacts


def private_catalog_file(tmp_path: Path, reviewed: ReviewedAdministratorCatalog):
    path = tmp_path / "catalog.json"
    body = json.dumps(asdict(reviewed)).encode()
    path.write_bytes(body)
    path.chmod(0o600)
    return path, hashlib.sha256(body).hexdigest()


def test_refresh_readiness_is_offline_and_preserves_attempt_twenty_four(tmp_path):
    plan = config(tmp_path)
    reviewed = catalog(plan)
    ledger, artifacts = completed_attempt_twenty_four(plan, reviewed)
    ledger_body = _ledger_path(plan).read_bytes()

    readiness = inspect_metadata_refresh_readiness(plan, reviewed, environment={})

    assert readiness.offline_inputs_ready is True
    assert readiness.credentials_available is False
    assert readiness.execution_allowed is False
    assert readiness.prior_attempted_gets == PRIOR_ATTEMPTED_GETS == 24
    assert readiness.additional_gets == REFRESH_REQUEST_GETS == 18
    assert readiness.cumulative_max_gets == REFRESH_CUMULATIVE_MAX_GETS == 42
    assert ledger.snapshot()["attempted_gets"] == 24
    assert _ledger_path(plan).read_bytes() == ledger_body
    assert all(path.read_bytes() == body for path, body in artifacts.items())


def test_refresh_uses_exact_eighteen_metadata_gets_and_preserves_prior_artifacts(tmp_path):
    plan = config(tmp_path)
    reviewed = catalog(plan)
    ledger, artifacts = completed_attempt_twenty_four(plan, reviewed)
    requests = []

    def opener(request, *, timeout):
        requests.append(request)
        assert request.get_method() == "GET"
        assert urlparse(request.full_url).path == "/api/method/frappe.desk.form.load.getdoctype"
        doctype = parse_qs(urlparse(request.full_url).query)["doctype"][0]
        assert doctype == reviewed.doctypes[len(requests) - 1]
        return FakeResponse(request, metadata_payload(doctype))

    report = run_metadata_refresh(
        plan,
        reviewed,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: LATER,
    )

    assert report.status == "complete"
    assert report.additional_attempted_gets == len(requests) == 18
    assert report.cumulative_attempted_gets == 42
    assert report.metadata_succeeded == 18
    assert report.metadata_failed == 0
    assert report.sensitive_metadata_excluded == 0
    assert report.candidate_entity_count == 18
    assert all(item.status == "candidate" for item in report.target_results)
    assert ledger.snapshot()["attempted_gets"] == 24
    assert all(path.read_bytes() == body for path, body in artifacts.items())
    refreshed = _refresh_candidate_path(plan)
    payload = json.loads(refreshed.read_text())
    assert len(payload["candidate_scopes"]) == 18
    assert len(payload["companies"]) == 1
    assert payload["review_required"] is True
    assert stat.S_IMODE(refreshed.stat().st_mode) == 0o600
    with ledger._connect() as connection:
        state = connection.execute(
            "SELECT status, attempted_gets FROM metadata_filter_refresh"
        ).fetchone()
    assert state == ("complete", 18)


def test_refresh_reports_sanitized_target_outcomes(tmp_path):
    plan = config(tmp_path)
    reviewed = catalog(plan)
    ledger, artifacts = completed_attempt_twenty_four(plan, reviewed)

    def opener(request, *, timeout):
        doctype = parse_qs(urlparse(request.full_url).query)["doctype"][0]
        index = reviewed.doctypes.index(doctype)
        if index == 0:
            raise HTTPError(request.full_url, 401, "RAW AUTH DETAIL", {}, None)
        if index == 1:
            raise HTTPError(request.full_url, 403, "RAW PERMISSION DETAIL", {}, None)
        if index == 2:
            return RawResponse(request, b"RAW INVALID JSON")
        if index == 3:
            payload = metadata_payload(doctype)
            payload["api_key"] = "RAW SECRET VALUE"
            return FakeResponse(request, payload)
        if index == 4:
            return FakeResponse(request, metadata_payload(doctype, include_company=False))
        return FakeResponse(request, metadata_payload(doctype))

    report = run_metadata_refresh(
        plan,
        reviewed,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: LATER,
    )

    assert report.status == "complete"
    assert report.additional_attempted_gets == 18
    assert report.cumulative_attempted_gets == 42
    assert report.metadata_succeeded == 15
    assert report.metadata_failed == 3
    assert report.sensitive_metadata_excluded == 1
    assert tuple((item.status, item.category) for item in report.target_results[:5]) == (
        ("failed", "http_authentication"),
        ("failed", "http_permission"),
        ("failed", "other"),
        ("excluded", "sensitive_value"),
        ("no_candidate", "scope_incompatible"),
    )
    assert ledger.snapshot()["attempted_gets"] == 24
    assert all(path.read_bytes() == body for path, body in artifacts.items())
    report_path, = plan.report_directory.glob("metadata-filter-refresh-report-*.json")
    rendered = report_path.read_text()
    for forbidden in (
        "RAW AUTH DETAIL",
        "RAW PERMISSION DETAIL",
        "RAW INVALID JSON",
        "RAW SECRET VALUE",
        SECRET_KEY,
        SECRET_VALUE,
        plan.base_url,
    ):
        assert forbidden not in rendered


def test_refresh_missing_credentials_does_not_claim_or_contact_transport(tmp_path):
    plan = config(tmp_path)
    reviewed = catalog(plan)
    ledger, artifacts = completed_attempt_twenty_four(plan, reviewed)

    with pytest.raises(LiveSessionError):
        run_metadata_refresh(
            plan,
            reviewed,
            environment={},
            opener=lambda *args, **kwargs: pytest.fail("missing credentials block transport"),
        )

    assert ledger.snapshot()["attempted_gets"] == 24
    assert all(path.read_bytes() == body for path, body in artifacts.items())
    with ledger._connect() as connection:
        present = connection.execute(
            "SELECT name FROM sqlite_master WHERE name='metadata_filter_refresh'"
        ).fetchone()
    assert present is None


def test_refresh_claim_is_one_use_even_after_interruption(tmp_path):
    plan = config(tmp_path)
    reviewed = catalog(plan)
    ledger, artifacts = completed_attempt_twenty_four(plan, reviewed)

    report = run_metadata_refresh(
        plan,
        reviewed,
        environment=SECRET_ENVIRONMENT,
        opener=lambda *args, **kwargs: pytest.fail("latched stop blocks transport"),
        clock=lambda: LATER,
        termination_requested=lambda: True,
    )

    assert report.status == "interrupted"
    assert report.additional_attempted_gets == 0
    assert report.cumulative_attempted_gets == 24
    assert ledger.snapshot()["attempted_gets"] == 24
    assert all(path.read_bytes() == body for path, body in artifacts.items())
    with pytest.raises(MetadataPreflightError, match="already claimed"):
        run_metadata_refresh(
            plan,
            reviewed,
            environment=SECRET_ENVIRONMENT,
            opener=lambda *args, **kwargs: pytest.fail("refresh cannot replay"),
        )


@pytest.mark.parametrize("interruption_stage", ("opener", "read"))
@pytest.mark.parametrize("completed_targets", (0, 1))
def test_refresh_charged_interruption_reports_and_cannot_replay(
    tmp_path, interruption_stage, completed_targets
):
    plan = config(tmp_path)
    reviewed = catalog(plan)
    ledger, artifacts = completed_attempt_twenty_four(plan, reviewed)
    requests = []

    class InterruptedResponse(FakeResponse):
        def read(self, size=-1):
            raise KeyboardInterrupt("RAW INTERRUPT DETAIL")

    def opener(request, *, timeout):
        requests.append(request)
        doctype = parse_qs(urlparse(request.full_url).query)["doctype"][0]
        if len(requests) <= completed_targets:
            return FakeResponse(request, metadata_payload(doctype))
        if interruption_stage == "opener":
            raise KeyboardInterrupt("RAW INTERRUPT DETAIL")
        return InterruptedResponse(request, metadata_payload(doctype))

    report = run_metadata_refresh(
        plan,
        reviewed,
        environment=SECRET_ENVIRONMENT,
        opener=opener,
        clock=lambda: LATER,
    )

    charged = completed_targets + 1
    assert report.status == report.failure_category == "interrupted"
    assert report.additional_attempted_gets == len(requests) == charged
    assert report.cumulative_attempted_gets == 24 + charged
    assert report.metadata_succeeded == completed_targets
    assert report.metadata_failed == 1
    assert report.target_results[-1].target_index == charged
    assert report.target_results[-1].status == "failed"
    assert report.target_results[-1].category == "interrupted"
    assert report.candidate_review_required is False
    assert not _refresh_candidate_path(plan).exists()
    assert ledger.snapshot()["attempted_gets"] == 24
    assert all(path.read_bytes() == body for path, body in artifacts.items())
    report_path, = plan.report_directory.glob("metadata-filter-refresh-report-*.json")
    rendered = report_path.read_text()
    persisted = json.loads(rendered)
    assert persisted["status"] == "interrupted"
    assert persisted["additional_attempted_gets"] == charged
    for forbidden in ("RAW INTERRUPT DETAIL", SECRET_KEY, SECRET_VALUE, plan.base_url):
        assert forbidden not in rendered
    with pytest.raises(MetadataPreflightError, match="already claimed"):
        run_metadata_refresh(
            plan,
            reviewed,
            environment=SECRET_ENVIRONMENT,
            opener=lambda *args, **kwargs: pytest.fail("interrupted refresh cannot replay"),
        )
    assert len(requests) == charged


def test_refresh_concurrent_claim_blocks_second_transport(tmp_path):
    plan = config(tmp_path)
    reviewed = catalog(plan)
    ledger, artifacts = completed_attempt_twenty_four(plan, reviewed)
    entered = threading.Event()
    release = threading.Event()
    outcomes = []

    def opener(request, *, timeout):
        if not entered.is_set():
            entered.set()
            assert release.wait(5)
        doctype = parse_qs(urlparse(request.full_url).query)["doctype"][0]
        return FakeResponse(request, metadata_payload(doctype))

    def run():
        outcomes.append(
            run_metadata_refresh(
                plan,
                reviewed,
                environment=SECRET_ENVIRONMENT,
                opener=opener,
                clock=lambda: LATER,
            )
        )

    worker = threading.Thread(target=run)
    worker.start()
    try:
        assert entered.wait(5)
        with pytest.raises(MetadataPreflightError, match="already claimed"):
            run_metadata_refresh(
                plan,
                reviewed,
                environment=SECRET_ENVIRONMENT,
                opener=lambda *args, **kwargs: pytest.fail(
                    "second claim cannot contact transport"
                ),
            )
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert outcomes[0].status == "complete"
    assert ledger.snapshot()["attempted_gets"] == 24
    assert all(path.read_bytes() == body for path, body in artifacts.items())


def test_refresh_cli_defaults_to_offline_readiness(tmp_path, monkeypatch, capsys):
    plan = config(tmp_path)
    reviewed = catalog(plan)
    ledger, artifacts = completed_attempt_twenty_four(plan, reviewed)
    path, digest = private_catalog_file(tmp_path, reviewed)
    monkeypatch.setattr(
        refresh_module, "metadata_preflight_config_from_environment", lambda env: plan
    )
    monkeypatch.setattr(
        refresh_module,
        "_default_opener",
        lambda *args, **kwargs: pytest.fail("offline readiness cannot contact ERP"),
    )

    result = main(
        [
            "--reviewed-admin-catalog",
            str(path),
            "--reviewed-admin-catalog-sha256",
            digest,
        ]
    )

    assert result == 0
    output = json.loads(capsys.readouterr().out)
    assert output["offline_inputs_ready"] is True
    assert output["execution_allowed"] is False
    assert output["additional_gets"] == 18
    assert output["cumulative_max_gets"] == 42
    assert ledger.snapshot()["attempted_gets"] == 24
    assert all(artifact.read_bytes() == body for artifact, body in artifacts.items())
