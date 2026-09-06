"""One-use corrected-filter refresh for the completed reviewed metadata run.

The original preflight row and artifacts remain immutable.  A separate table
inside the same private SQLite ledger accounts for at most eighteen additional
metadata GETs, while a distinct candidate and aggregate report preserve the
refresh result for review.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import stat
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..contracts import utc_now
from .erpnext_adapter import DEFAULT_MAX_RESPONSE_BYTES, _default_opener
from .erpnext_live_session import (
    LiveSessionError,
    LiveSessionStorageError,
    ReviewedMetadataScope,
    _prepare_destinations,
    _validate_configured_paths,
    derive_metadata_scope_candidate,
)
from .erpnext_metadata_adapter import ERPNextMetadataAdapter
from .erpnext_metadata_preflight import (
    ERPNextMetadataPreflightConfig,
    MetadataPreflightBudgetError,
    MetadataPreflightError,
    ReviewedAdministratorCatalog,
    _admin_binding_digest,
    _candidate_path,
    _contains_sensitive_embedded_value,
    _existing_private_retry_ledger,
    _metadata_target_failure_bucket,
    _path_entry_exists,
    _RunLedger,
    _scope_digest,
    _unique_json_object,
    _validated_candidate,
    _write_private_json,
    load_reviewed_administrator_catalog,
    metadata_preflight_config_from_environment,
)

PRIOR_ATTEMPTED_GETS = 24
REFRESH_REQUEST_GETS = 18
REFRESH_CUMULATIVE_MAX_GETS = PRIOR_ATTEMPTED_GETS + REFRESH_REQUEST_GETS

_REFRESH_STATE_SCHEMA = """
CREATE TABLE metadata_filter_refresh (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    status TEXT NOT NULL,
    catalog_digest TEXT NOT NULL,
    binding TEXT NOT NULL,
    prior_report_digest TEXT NOT NULL,
    prior_candidate_digest TEXT NOT NULL,
    attempted_gets INTEGER NOT NULL CHECK (attempted_gets BETWEEN 0 AND 18),
    metadata_succeeded INTEGER NOT NULL DEFAULT 0,
    metadata_failed INTEGER NOT NULL DEFAULT 0,
    sensitive_excluded INTEGER NOT NULL DEFAULT 0,
    candidate_entity_count INTEGER NOT NULL DEFAULT 0,
    candidate_field_count INTEGER NOT NULL DEFAULT 0
)
"""
_REFRESH_TARGET_SCHEMA = """
CREATE TABLE metadata_filter_refresh_targets (
    target_index INTEGER PRIMARY KEY CHECK (target_index BETWEEN 1 AND 18),
    status TEXT NOT NULL,
    category TEXT NOT NULL
)
"""


@dataclass(frozen=True, slots=True)
class MetadataRefreshTargetResult:
    target_index: int
    status: str
    category: str

    def __post_init__(self) -> None:
        if type(self.target_index) is not int or not 1 <= self.target_index <= 18:
            raise MetadataPreflightError("metadata refresh target index is invalid")
        if self.status not in {"candidate", "excluded", "no_candidate", "failed"}:
            raise MetadataPreflightError("metadata refresh target status is invalid")
        allowed = {
            "candidate": {"none"},
            "excluded": {"sensitive_value"},
            "no_candidate": {"scope_incompatible"},
            "failed": {"http_authentication", "http_permission", "other", "interrupted"},
        }
        if self.category not in allowed[self.status]:
            raise MetadataPreflightError("metadata refresh target category is invalid")


@dataclass(frozen=True, slots=True)
class MetadataRefreshReadinessReport:
    execution_allowed: bool
    offline_inputs_ready: bool
    credentials_available: bool
    status: str
    prior_attempted_gets: int
    additional_gets: int
    cumulative_max_gets: int
    reviewed_type_count: int
    record_sampling_allowed: bool = False
    erp_writes_allowed: bool = False
    soak_allowed: bool = False

    def __post_init__(self) -> None:
        if self.status not in {"ready", "invalid", "already_claimed"}:
            raise MetadataPreflightError("metadata refresh readiness status is invalid")
        if (
            self.execution_allowed
            or self.record_sampling_allowed
            or self.erp_writes_allowed
            or self.soak_allowed
        ):
            raise MetadataPreflightError("metadata refresh readiness cannot grant authority")
        if self.offline_inputs_ready != (self.status == "ready"):
            raise MetadataPreflightError("metadata refresh readiness is inconsistent")
        if (
            self.prior_attempted_gets != PRIOR_ATTEMPTED_GETS
            or self.additional_gets != REFRESH_REQUEST_GETS
            or self.cumulative_max_gets != REFRESH_CUMULATIVE_MAX_GETS
            or self.reviewed_type_count != REFRESH_REQUEST_GETS
        ):
            raise MetadataPreflightError("metadata refresh budget is invalid")


@dataclass(frozen=True, slots=True)
class MetadataRefreshRunReport:
    execution_allowed: bool
    status: str
    failure_category: str
    started_at: datetime
    ended_at: datetime
    prior_attempted_gets: int
    additional_attempted_gets: int
    cumulative_attempted_gets: int
    max_total_attempted_gets: int
    reviewed_type_count: int
    metadata_succeeded: int
    metadata_failed: int
    sensitive_metadata_excluded: int
    candidate_entity_count: int
    candidate_field_count: int
    candidate_review_required: bool
    target_results: tuple[MetadataRefreshTargetResult, ...]
    record_samples: int = 0
    erp_writes: int = 0
    soak_started: bool = False

    def __post_init__(self) -> None:
        if self.status not in {"complete", "failed", "interrupted"}:
            raise MetadataPreflightError("metadata refresh status is invalid")
        if self.failure_category not in {"none", "storage_failure", "internal_failure", "interrupted"}:
            raise MetadataPreflightError("metadata refresh failure category is invalid")
        if self.status == "complete" and self.failure_category != "none":
            raise MetadataPreflightError("completed metadata refresh cannot report failure")
        if self.status == "interrupted" and self.failure_category != "interrupted":
            raise MetadataPreflightError("interrupted metadata refresh category is invalid")
        if self.status == "failed" and self.failure_category in {"none", "interrupted"}:
            raise MetadataPreflightError("failed metadata refresh requires a safe category")
        for value in (self.started_at, self.ended_at):
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise MetadataPreflightError("metadata refresh time must be timezone-aware")
        if self.ended_at < self.started_at:
            raise MetadataPreflightError("metadata refresh time range is invalid")
        counts = (
            self.prior_attempted_gets,
            self.additional_attempted_gets,
            self.cumulative_attempted_gets,
            self.max_total_attempted_gets,
            self.reviewed_type_count,
            self.metadata_succeeded,
            self.metadata_failed,
            self.sensitive_metadata_excluded,
            self.candidate_entity_count,
            self.candidate_field_count,
            self.record_samples,
            self.erp_writes,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise MetadataPreflightError("metadata refresh counts are invalid")
        if (
            self.prior_attempted_gets != PRIOR_ATTEMPTED_GETS
            or self.max_total_attempted_gets != 100
            or self.reviewed_type_count != REFRESH_REQUEST_GETS
            or self.additional_attempted_gets > REFRESH_REQUEST_GETS
            or self.cumulative_attempted_gets
            != self.prior_attempted_gets + self.additional_attempted_gets
            or self.metadata_succeeded + self.metadata_failed
            != self.additional_attempted_gets
            or len(self.target_results) != self.additional_attempted_gets
            or self.candidate_entity_count > self.metadata_succeeded
        ):
            raise MetadataPreflightError("metadata refresh accounting is invalid")
        if self.status == "complete" and self.additional_attempted_gets != REFRESH_REQUEST_GETS:
            raise MetadataPreflightError("completed metadata refresh is incomplete")
        if tuple(item.target_index for item in self.target_results) != tuple(
            range(1, len(self.target_results) + 1)
        ):
            raise MetadataPreflightError("metadata refresh target ordering is invalid")
        if self.candidate_review_required != (self.status == "complete"):
            raise MetadataPreflightError("metadata refresh review status is invalid")
        if self.execution_allowed or self.record_samples or self.erp_writes or self.soak_started:
            raise MetadataPreflightError("metadata refresh cannot grant live authority")


@dataclass(frozen=True, slots=True)
class _PriorState:
    ledger: _RunLedger
    companies: tuple[str, ...]
    report_digest: str
    candidate_digest: str


def _refresh_candidate_path(config: ERPNextMetadataPreflightConfig) -> Path:
    return config.state_directory / f"metadata-candidate-filter-refresh-{_scope_digest(config)}.json"


def _refresh_report_path(config: ERPNextMetadataPreflightConfig, ended_at: datetime) -> Path:
    stamp = ended_at.strftime("%Y%m%dT%H%M%S.%f%z")
    return config.report_directory / f"metadata-filter-refresh-report-{stamp}.json"


def _read_private_bytes(path: Path, label: str) -> bytes:
    flags = os.O_RDONLY
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags)
        with os.fdopen(descriptor, "rb") as stream:
            info = os.fstat(stream.fileno())
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise MetadataPreflightError(f"{label} is not private")
            body = stream.read(DEFAULT_MAX_RESPONSE_BYTES + 1)
    except OSError as exc:
        raise MetadataPreflightError(f"{label} is unavailable") from exc
    if len(body) > DEFAULT_MAX_RESPONSE_BYTES:
        raise MetadataPreflightError(f"{label} exceeds its size bound")
    return body


def _validated_completion_report(config: ERPNextMetadataPreflightConfig) -> str:
    expected_keys = {
        "attempted_gets",
        "candidate_entity_count",
        "candidate_field_count",
        "candidate_review_required",
        "catalog_source",
        "company_catalog_complete",
        "company_count",
        "doctype_catalog_complete",
        "doctype_catalog_count",
        "ended_at",
        "erp_writes",
        "execution_allowed",
        "failure_category",
        "failure_stage",
        "max_total_attempted_gets",
        "metadata_authentication_failures",
        "metadata_failed",
        "metadata_other_failures",
        "metadata_permission_failures",
        "metadata_succeeded",
        "prior_attempted_gets",
        "record_samples",
        "sensitive_metadata_excluded",
        "soak_started",
        "started_at",
        "status",
    }
    matches: list[str] = []
    for path in sorted(config.report_directory.glob("metadata-preflight-report-*.json")):
        body = _read_private_bytes(path, "prior metadata report")
        try:
            payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_json_object)
        except (UnicodeDecodeError, json.JSONDecodeError, MetadataPreflightError):
            continue
        if not isinstance(payload, Mapping) or not (
            payload.get("attempted_gets") == PRIOR_ATTEMPTED_GETS
            and payload.get("catalog_source") == "reviewed_selection"
        ):
            continue
        if set(payload) != expected_keys or not (
            payload.get("execution_allowed") is False
            and payload.get("status") == "complete"
            and payload.get("failure_stage") == "none"
            and payload.get("failure_category") == "none"
            and payload.get("max_total_attempted_gets") == 100
            and payload.get("prior_attempted_gets") == 5
            and payload.get("company_count") == 1
            and payload.get("company_catalog_complete") is True
            and payload.get("doctype_catalog_count") == REFRESH_REQUEST_GETS
            and payload.get("doctype_catalog_complete") is False
            and payload.get("metadata_succeeded") == REFRESH_REQUEST_GETS
            and payload.get("metadata_failed") == 0
            and payload.get("metadata_authentication_failures") == 0
            and payload.get("metadata_permission_failures") == 0
            and payload.get("metadata_other_failures") == 0
            and payload.get("sensitive_metadata_excluded") == REFRESH_REQUEST_GETS
            and payload.get("candidate_entity_count") == 0
            and payload.get("candidate_field_count") == 0
            and payload.get("candidate_review_required") is True
            and payload.get("record_samples") == 0
            and payload.get("erp_writes") == 0
            and payload.get("soak_started") is False
        ):
            raise MetadataPreflightError("prior metadata completion report is invalid")
        matches.append(hashlib.sha256(body).hexdigest())
    if len(matches) != 1:
        raise MetadataPreflightError("unique metadata completion report is required")
    return matches[0]


def _refresh_table_present(ledger: _RunLedger) -> bool:
    with ledger._connect() as connection:
        rows = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name IN ('metadata_filter_refresh', 'metadata_filter_refresh_targets')"
        ).fetchall()
    return bool(rows)


def _validated_prior_state(
    config: ERPNextMetadataPreflightConfig,
    catalog: ReviewedAdministratorCatalog,
) -> _PriorState:
    catalog.validate_binding(config)
    if len(catalog.doctypes) != REFRESH_REQUEST_GETS:
        raise MetadataPreflightError("metadata refresh requires the exact reviewed selection")
    ledger = _existing_private_retry_ledger(config)
    snapshot = ledger.snapshot()
    if ledger.accounting_base() != 6 or snapshot != {
        "status": "complete",
        "attempted_gets": PRIOR_ATTEMPTED_GETS,
        "company_count": 1,
        "doctype_catalog_count": REFRESH_REQUEST_GETS,
        "metadata_succeeded": REFRESH_REQUEST_GETS,
        "metadata_failed": 0,
        "candidate_entity_count": 0,
        "candidate_field_count": 0,
        "company_catalog_complete": 1,
        "doctype_catalog_complete": 0,
    }:
        raise MetadataPreflightError("metadata refresh requires exact completed attempt twenty-four")
    candidate = _validated_candidate(config, snapshot)
    if candidate["candidate_scopes"] != {} or len(candidate["companies"]) != 1:
        raise MetadataPreflightError("metadata refresh requires the empty completed candidate")
    candidate_body = _read_private_bytes(_candidate_path(config), "prior metadata candidate")
    report_digest = _validated_completion_report(config)
    with ledger._connect() as connection:
        claim = connection.execute("SELECT digest FROM admin_catalog_claim").fetchone()
    if claim != (catalog.digest(),):
        raise MetadataPreflightError("metadata refresh catalog claim does not match")
    if _refresh_table_present(ledger):
        raise MetadataPreflightError("metadata refresh was already claimed")
    if _path_entry_exists(_refresh_candidate_path(config)):
        raise MetadataPreflightError("metadata refresh candidate already exists")
    if tuple(config.report_directory.glob("metadata-filter-refresh-report-*.json")):
        raise MetadataPreflightError("metadata refresh report already exists")
    return _PriorState(
        ledger=ledger,
        companies=tuple(candidate["companies"]),
        report_digest=report_digest,
        candidate_digest=hashlib.sha256(candidate_body).hexdigest(),
    )


class _RefreshLedger:
    def __init__(self, prior: _PriorState) -> None:
        self._prior = prior

    def claim(self, catalog: ReviewedAdministratorCatalog, config: ERPNextMetadataPreflightConfig) -> None:
        if _validated_completion_report(config) != self._prior.report_digest:
            raise MetadataPreflightError("metadata refresh report changed before claim")
        candidate_body = _read_private_bytes(
            _candidate_path(config), "prior metadata candidate"
        )
        if hashlib.sha256(candidate_body).hexdigest() != self._prior.candidate_digest:
            raise MetadataPreflightError("metadata refresh candidate changed before claim")
        connection = self._prior.ledger._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, attempted_gets, company_count, doctype_catalog_count, "
                "metadata_succeeded, metadata_failed, candidate_entity_count, "
                "candidate_field_count, company_catalog_complete, doctype_catalog_complete "
                "FROM preflight_run WHERE singleton=1"
            ).fetchone()
            existing = connection.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name='metadata_filter_refresh'"
            ).fetchone()
            binding = _admin_binding_digest(config)
            admin_claim = connection.execute(
                "SELECT digest, binding FROM admin_catalog_claim"
            ).fetchall()
            resume_claim = connection.execute(
                "SELECT catalog_digest, binding FROM admin_catalog_resume_claim"
            ).fetchall()
            if existing or row != (
                "complete", 24, 1, 18, 18, 0, 0, 0, 1, 0
            ) or admin_claim != [(catalog.digest(), binding)] or resume_claim != [
                (catalog.digest(), binding)
            ]:
                raise MetadataPreflightError("metadata refresh claim no longer matches")
            connection.execute(_REFRESH_STATE_SCHEMA)
            connection.execute(_REFRESH_TARGET_SCHEMA)
            connection.execute(
                "INSERT INTO metadata_filter_refresh ("
                "singleton, status, catalog_digest, binding, prior_report_digest, "
                "prior_candidate_digest, attempted_gets) VALUES (1, 'running', ?, ?, ?, ?, 0)",
                (
                    catalog.digest(),
                    binding,
                    self._prior.report_digest,
                    self._prior.candidate_digest,
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reserve_attempt(self) -> int:
        connection = self._prior.ledger._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, attempted_gets FROM metadata_filter_refresh WHERE singleton=1"
            ).fetchone()
            if row is None or row[0] != "running":
                raise MetadataPreflightError("metadata refresh is not running")
            attempted = int(row[1])
            if attempted >= REFRESH_REQUEST_GETS:
                raise MetadataPreflightBudgetError("metadata refresh GET budget exhausted")
            attempted += 1
            connection.execute(
                "UPDATE metadata_filter_refresh SET attempted_gets=? WHERE singleton=1",
                (attempted,),
            )
            connection.commit()
            return attempted
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def record(self, result: MetadataRefreshTargetResult) -> None:
        connection = self._prior.ledger._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, attempted_gets, metadata_succeeded, metadata_failed, "
                "sensitive_excluded FROM metadata_filter_refresh WHERE singleton=1"
            ).fetchone()
            if row is None or row[0] != "running" or row[1] != result.target_index:
                raise MetadataPreflightError("metadata refresh result does not match reservation")
            succeeded = int(row[2]) + int(result.status != "failed")
            failed = int(row[3]) + int(result.status == "failed")
            excluded = int(row[4]) + int(result.status == "excluded")
            connection.execute(
                "INSERT INTO metadata_filter_refresh_targets VALUES (?, ?, ?)",
                (result.target_index, result.status, result.category),
            )
            connection.execute(
                "UPDATE metadata_filter_refresh SET metadata_succeeded=?, "
                "metadata_failed=?, sensitive_excluded=? WHERE singleton=1",
                (succeeded, failed, excluded),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def finish(self, status: str, entity_count: int = 0, field_count: int = 0) -> None:
        if status not in {"complete", "failed", "interrupted"}:
            raise MetadataPreflightError("metadata refresh final status is invalid")
        with self._prior.ledger._connect() as connection:
            cursor = connection.execute(
                "UPDATE metadata_filter_refresh SET status=?, candidate_entity_count=?, "
                "candidate_field_count=? WHERE singleton=1 AND status='running'",
                (status, entity_count, field_count),
            )
            if cursor.rowcount != 1:
                raise MetadataPreflightError("metadata refresh could not be finalized")

    def snapshot(self) -> tuple[Mapping[str, Any], tuple[MetadataRefreshTargetResult, ...]]:
        with self._prior.ledger._connect() as connection:
            row = connection.execute(
                "SELECT status, attempted_gets, metadata_succeeded, metadata_failed, "
                "sensitive_excluded, candidate_entity_count, candidate_field_count "
                "FROM metadata_filter_refresh WHERE singleton=1"
            ).fetchone()
            targets = connection.execute(
                "SELECT target_index, status, category FROM metadata_filter_refresh_targets "
                "ORDER BY target_index"
            ).fetchall()
        if row is None:
            raise MetadataPreflightError("metadata refresh state is invalid")
        names = (
            "status",
            "attempted_gets",
            "metadata_succeeded",
            "metadata_failed",
            "sensitive_excluded",
            "candidate_entity_count",
            "candidate_field_count",
        )
        return dict(zip(names, row, strict=True)), tuple(
            MetadataRefreshTargetResult(*target) for target in targets
        )


class _RefreshBudgetedOpener:
    def __init__(self, ledger: _RefreshLedger, opener: Callable[..., Any]) -> None:
        self._ledger = ledger
        self._opener = opener

    def __call__(self, request: Any, *, timeout: int) -> Any:
        self._ledger.reserve_attempt()
        return self._opener(request, timeout=timeout)


def inspect_metadata_refresh_readiness(
    config: ERPNextMetadataPreflightConfig,
    catalog: ReviewedAdministratorCatalog,
    *,
    environment: Mapping[str, str],
) -> MetadataRefreshReadinessReport:
    credentials_available = True
    try:
        config.credential_references.resolve(environment)
    except (LiveSessionError, TypeError):
        credentials_available = False
    status = "ready"
    try:
        _validated_prior_state(config, catalog)
    except MetadataPreflightError as exc:
        status = "already_claimed" if "already claimed" in str(exc) else "invalid"
    return MetadataRefreshReadinessReport(
        execution_allowed=False,
        offline_inputs_ready=status == "ready",
        credentials_available=credentials_available,
        status=status,
        prior_attempted_gets=PRIOR_ATTEMPTED_GETS,
        additional_gets=REFRESH_REQUEST_GETS,
        cumulative_max_gets=REFRESH_CUMULATIVE_MAX_GETS,
        reviewed_type_count=len(catalog.doctypes),
    )


def _report_json(report: MetadataRefreshReadinessReport | MetadataRefreshRunReport) -> str:
    payload = asdict(report)
    for name in ("started_at", "ended_at"):
        if name in payload:
            payload[name] = payload[name].isoformat()
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def run_metadata_refresh(
    config: ERPNextMetadataPreflightConfig,
    catalog: ReviewedAdministratorCatalog,
    *,
    environment: Mapping[str, str],
    opener: Callable[..., Any] | None = None,
    clock: Callable[[], datetime] = utc_now,
    termination_requested: Callable[[], bool] | None = None,
) -> MetadataRefreshRunReport:
    if not isinstance(config, ERPNextMetadataPreflightConfig):
        raise TypeError("config must be ERPNextMetadataPreflightConfig")
    if not isinstance(catalog, ReviewedAdministratorCatalog):
        raise TypeError("catalog must be ReviewedAdministratorCatalog")
    termination = termination_requested or (lambda: False)
    if not callable(termination):
        raise TypeError("termination_requested must be callable")
    started_at = clock()
    if not isinstance(started_at, datetime) or started_at.utcoffset() is None:
        raise MetadataPreflightError("clock must return a timezone-aware datetime")
    _validate_configured_paths(config)  # type: ignore[arg-type]
    _prepare_destinations(config)  # type: ignore[arg-type]
    prior = _validated_prior_state(config, catalog)
    credentials = config.credential_references.resolve(environment)
    transport = opener or _default_opener
    if not callable(transport):
        raise TypeError("opener must be callable")
    ledger = _RefreshLedger(prior)
    ledger.claim(catalog, config)
    budgeted = _RefreshBudgetedOpener(ledger, transport)
    candidates: list[ReviewedMetadataScope] = []
    failure_category = "none"
    status = "complete"
    try:
        for index, doctype in enumerate(catalog.doctypes, start=1):
            if termination():
                raise _RefreshInterrupted
            try:
                observations = ERPNextMetadataAdapter(
                    base_url=config.base_url,
                    tenant_id=config.tenant_id,
                    api_key=credentials.api_key,
                    api_secret=credentials.api_secret,
                    doctypes=(doctype,),
                    opener=budgeted,
                ).discover()
                raw_metadata = observations[0].evidence.payload["metadata"]
                if _contains_sensitive_embedded_value(raw_metadata):
                    result = MetadataRefreshTargetResult(index, "excluded", "sensitive_value")
                else:
                    candidate = derive_metadata_scope_candidate(
                        config.tenant_id, doctype, observations
                    )
                    if candidate is None:
                        result = MetadataRefreshTargetResult(
                            index, "no_candidate", "scope_incompatible"
                        )
                    else:
                        candidates.append(candidate)
                        result = MetadataRefreshTargetResult(index, "candidate", "none")
            except (KeyboardInterrupt, SystemExit, _RefreshInterrupted):
                raise
            except Exception as exc:  # noqa: BLE001 - each charged target is categorized
                bucket = _metadata_target_failure_bucket(exc)
                category = {
                    "authentication": "http_authentication",
                    "permission": "http_permission",
                }.get(bucket, "other")
                result = MetadataRefreshTargetResult(index, "failed", category)
            ledger.record(result)
        field_count = sum(len(scope.fields) for scope in candidates)
        _write_private_json(
            _refresh_candidate_path(config),
            {
                "candidate_scopes": {
                    scope.entity: list(scope.fields) for scope in candidates
                },
                "companies": list(prior.companies),
                "company_catalog_complete": True,
                "doctype_catalog_complete": False,
                "review_required": True,
                "schema_version": 1,
            },
        )
        ledger.finish("complete", len(candidates), field_count)
    except (KeyboardInterrupt, SystemExit, _RefreshInterrupted):
        status = "interrupted"
        failure_category = "interrupted"
        state, target_results = ledger.snapshot()
        if state["attempted_gets"] == len(target_results) + 1:
            # A charged GET can be interrupted before its outcome is recorded.
            ledger.record(
                MetadataRefreshTargetResult(state["attempted_gets"], "failed", "interrupted")
            )
        ledger.finish(status)
    except LiveSessionStorageError:
        status = "failed"
        failure_category = "storage_failure"
        ledger.finish(status)
    except Exception:  # noqa: BLE001 - aggregate-only failure boundary
        status = "failed"
        failure_category = "internal_failure"
        ledger.finish(status)
    ended_at = clock()
    state, target_results = ledger.snapshot()
    report = MetadataRefreshRunReport(
        execution_allowed=False,
        status=status,
        failure_category=failure_category,
        started_at=started_at,
        ended_at=ended_at,
        prior_attempted_gets=PRIOR_ATTEMPTED_GETS,
        additional_attempted_gets=int(state["attempted_gets"]),
        cumulative_attempted_gets=PRIOR_ATTEMPTED_GETS + int(state["attempted_gets"]),
        max_total_attempted_gets=100,
        reviewed_type_count=REFRESH_REQUEST_GETS,
        metadata_succeeded=int(state["metadata_succeeded"]),
        metadata_failed=int(state["metadata_failed"]),
        sensitive_metadata_excluded=int(state["sensitive_excluded"]),
        candidate_entity_count=int(state["candidate_entity_count"]),
        candidate_field_count=int(state["candidate_field_count"]),
        candidate_review_required=status == "complete",
        target_results=target_results,
    )
    _write_private_json(
        _refresh_report_path(config, ended_at),
        json.loads(_report_json(report)),
    )
    return report


class _RefreshInterrupted(BaseException):
    pass


class _TerminationLatch:
    def __init__(self) -> None:
        self.requested = False

    def __call__(self) -> bool:
        return self.requested

    def handle(self, signum: int, frame: Any) -> None:
        del signum, frame
        if self.requested:
            raise KeyboardInterrupt
        self.requested = True


@contextmanager
def _termination_signals() -> Iterator[_TerminationLatch]:
    latch = _TerminationLatch()
    previous = {
        signum: signal.getsignal(signum) for signum in (signal.SIGINT, signal.SIGTERM)
    }
    try:
        for signum in previous:
            signal.signal(signum, latch.handle)
        yield latch
    finally:
        for signum, handler in previous.items():
            signal.signal(signum, handler)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Prepare or run the bounded corrected-filter metadata refresh",
        allow_abbrev=False,
    )
    parser.add_argument("--execute-metadata-refresh", action="store_true")
    parser.add_argument("--reviewed-admin-catalog", required=True, type=Path)
    parser.add_argument("--reviewed-admin-catalog-sha256", required=True)
    args = parser.parse_args(argv)
    try:
        config = metadata_preflight_config_from_environment(os.environ)
        catalog = load_reviewed_administrator_catalog(
            args.reviewed_admin_catalog, args.reviewed_admin_catalog_sha256
        )
        readiness = inspect_metadata_refresh_readiness(
            config, catalog, environment=os.environ
        )
    except Exception:  # noqa: BLE001 - CLI emits fixed safe state only
        print('{"execution_allowed":false,"status":"configuration_invalid"}')
        return 2
    if not args.execute_metadata_refresh:
        print(_report_json(readiness))
        return 0 if readiness.offline_inputs_ready else 2
    if not readiness.offline_inputs_ready:
        print(_report_json(readiness))
        return 2
    try:
        with _termination_signals() as termination:
            report = run_metadata_refresh(
                config,
                catalog,
                environment=os.environ,
                termination_requested=termination,
            )
    except Exception:  # noqa: BLE001 - CLI emits fixed safe state only
        print('{"execution_allowed":false,"status":"refresh_refused"}')
        return 2
    print(_report_json(report))
    if report.status == "interrupted":
        return 130
    return 0 if report.status == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "PRIOR_ATTEMPTED_GETS",
    "REFRESH_CUMULATIVE_MAX_GETS",
    "REFRESH_REQUEST_GETS",
    "MetadataRefreshReadinessReport",
    "MetadataRefreshRunReport",
    "MetadataRefreshTargetResult",
    "inspect_metadata_refresh_readiness",
    "main",
    "run_metadata_refresh",
]
