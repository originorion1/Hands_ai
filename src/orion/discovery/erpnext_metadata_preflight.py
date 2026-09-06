"""Bounded ERPNext metadata-only preflight with durable request accounting.

This entrypoint cannot sample records or invoke the shadow-soak runner.  Its
explicit execution mode reads two name-only catalogs and structural metadata,
then writes only a review-required candidate and an aggregate report.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import signal
import sqlite3
import stat
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urlencode
from urllib.request import Request

from ..contracts import utc_now
from .erpnext_adapter import (
    DEFAULT_MAX_RESPONSE_BYTES,
    DEFAULT_TIMEOUT_SECONDS,
    _default_opener,
    _normalize_base_url,
    _validate_resource,
)
from .erpnext_live_session import (
    CredentialEnvironmentReferences,
    ERPNextCompanyAuthorization,
    LiveSessionError,
    LiveSessionStorageError,
    ReviewedMetadataScope,
    _destination_ready,
    _enforce_private_file,
    _prepare_destinations,
    _reject_symlink_path,
    _validate_absolute_path,
    _validate_configured_paths,
    derive_metadata_scope_candidate,
    is_sensitive_metadata_name,
)
from .erpnext_metadata_adapter import ERPNextMetadataAdapter

MAX_TOTAL_ATTEMPTED_GETS = 100
MAX_COMPANY_NAMES = 499
MAX_METADATA_DOCTYPES = MAX_TOTAL_ATTEMPTED_GETS - 2
DOCTYPE_CATALOG_RETRY_PRIOR_ATTEMPTS = 2
DOCTYPE_CATALOG_RETRY_ATTEMPT = 3
_CATALOG_FIELDS = ("name",)
_SENSITIVE_VALUE_KEYS = frozenset(
    {
        "api_key",
        "api_secret",
        "authorization",
        "credential",
        "credentials",
        "password",
        "secret",
        "token",
    }
)
_FAILURE_STAGES = frozenset(
    {"none", "company_catalog", "doctype_catalog", "metadata_targets", "candidate_persistence"}
)
_FAILURE_CATEGORIES = frozenset(
    {
        "none",
        "http_permission",
        "endpoint_contract",
        "http_status",
        "transport_failure",
        "redirect_rejected",
        "response_validation",
        "scope_validation",
        "storage_failure",
        "internal_failure",
        "interrupted",
    }
)
_LEDGER_SCHEMA = """
CREATE TABLE preflight_run (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    status TEXT NOT NULL,
    attempted_gets INTEGER NOT NULL CHECK (attempted_gets BETWEEN 0 AND 100),
    company_count INTEGER NOT NULL DEFAULT 0,
    doctype_catalog_count INTEGER NOT NULL DEFAULT 0,
    metadata_succeeded INTEGER NOT NULL DEFAULT 0,
    metadata_failed INTEGER NOT NULL DEFAULT 0,
    candidate_entity_count INTEGER NOT NULL DEFAULT 0,
    candidate_field_count INTEGER NOT NULL DEFAULT 0,
    company_catalog_complete INTEGER NOT NULL DEFAULT 0,
    doctype_catalog_complete INTEGER NOT NULL DEFAULT 0
)
"""


def _validate_ledger_snapshot(snapshot: Mapping[str, Any]) -> None:
    expected = {
        "status",
        "attempted_gets",
        "company_count",
        "doctype_catalog_count",
        "metadata_succeeded",
        "metadata_failed",
        "candidate_entity_count",
        "candidate_field_count",
        "company_catalog_complete",
        "doctype_catalog_complete",
    }
    if set(snapshot) != expected or snapshot.get("status") not in {
        "running",
        "complete",
        "failed",
        "interrupted",
    }:
        raise MetadataPreflightError("metadata preflight state is invalid")
    counts = tuple(
        snapshot[name]
        for name in (
            "attempted_gets",
            "company_count",
            "doctype_catalog_count",
            "metadata_succeeded",
            "metadata_failed",
            "candidate_entity_count",
            "candidate_field_count",
        )
    )
    if any(type(value) is not int or value < 0 for value in counts):
        raise MetadataPreflightError("metadata preflight state counts are invalid")
    if not 0 <= snapshot["attempted_gets"] <= MAX_TOTAL_ATTEMPTED_GETS:
        raise MetadataPreflightError("metadata preflight state exceeded its budget")
    if snapshot["company_count"] > MAX_COMPANY_NAMES:
        raise MetadataPreflightError("metadata preflight company count is invalid")
    if snapshot["doctype_catalog_count"] > MAX_METADATA_DOCTYPES:
        raise MetadataPreflightError("metadata preflight DocType count is invalid")
    if (
        snapshot["metadata_succeeded"] + snapshot["metadata_failed"]
        > snapshot["doctype_catalog_count"]
    ):
        raise MetadataPreflightError("metadata preflight metadata counts are invalid")
    if snapshot["candidate_entity_count"] > snapshot["metadata_succeeded"]:
        raise MetadataPreflightError("metadata preflight candidate counts are invalid")
    if snapshot["status"] == "complete" and snapshot["attempted_gets"] != (
        2 + snapshot["metadata_succeeded"] + snapshot["metadata_failed"]
    ):
        raise MetadataPreflightError("completed metadata preflight accounting is invalid")
    for name in ("company_catalog_complete", "doctype_catalog_complete"):
        if snapshot[name] not in (0, 1) or type(snapshot[name]) is not int:
            raise MetadataPreflightError("metadata preflight completeness state is invalid")


class MetadataPreflightError(LiveSessionError):
    """Raised when the metadata-only authorization boundary cannot be upheld."""


class MetadataPreflightBudgetError(MetadataPreflightError):
    """Raised before transport when the cumulative allowance is exhausted."""


class _MetadataCatalogRetryRefused(MetadataPreflightError):
    """Raised before transport when the one-shot recovery contract does not match."""


class _CategorizedPreflightError(MetadataPreflightError):
    def __init__(self, category: str) -> None:
        if category not in _FAILURE_CATEGORIES - {"none", "interrupted"}:
            raise ValueError("invalid metadata preflight failure category")
        super().__init__("metadata preflight operation failed")
        self.category = category


class _PreflightInterrupted(BaseException):
    pass


@dataclass(frozen=True, slots=True)
class ERPNextMetadataPreflightConfig:
    """Non-secret inputs for exactly one metadata-only preflight."""

    base_url: str
    tenant_id: str = field(repr=False)
    authorization_reference: str = field(repr=False)
    credential_references: CredentialEnvironmentReferences = field(repr=False)
    state_directory: Path
    report_directory: Path
    max_total_attempted_gets: int = MAX_TOTAL_ATTEMPTED_GETS

    def __post_init__(self) -> None:
        normalized = _normalize_base_url(self.base_url)
        if normalized != self.base_url:
            raise MetadataPreflightError("base_url must be the normalized HTTPS origin")
        for value, label in (
            (self.tenant_id, "tenant_id"),
            (self.authorization_reference, "authorization_reference"),
        ):
            if not isinstance(value, str) or not value.strip() or value != value.strip():
                raise MetadataPreflightError(f"{label} must be normalized non-empty text")
        if not isinstance(self.credential_references, CredentialEnvironmentReferences):
            raise TypeError("credential_references must be credential references")
        if self.max_total_attempted_gets != MAX_TOTAL_ATTEMPTED_GETS:
            raise MetadataPreflightError("metadata preflight budget must remain exactly 100")
        _validate_absolute_path(self.state_directory, "state directory")
        _validate_absolute_path(self.report_directory, "report directory")
        if self.state_directory == self.report_directory:
            raise LiveSessionStorageError("state and report destinations must be distinct")
        _reject_symlink_path(self.state_directory)
        _reject_symlink_path(self.report_directory)
        _validate_configured_paths(self)  # type: ignore[arg-type]


@dataclass(frozen=True, slots=True)
class MetadataPreflightReadinessReport:
    execution_allowed: bool
    ready_for_metadata_preflight: bool
    offline_candidate_ready: bool
    credentials_available: bool
    state_destination_ready: bool
    report_destination_ready: bool
    prior_run_present: bool
    prior_run_status: str
    attempted_gets: int
    candidate_review_required: bool
    live_session_ready: bool = False
    max_total_attempted_gets: int = MAX_TOTAL_ATTEMPTED_GETS
    record_sampling_allowed: bool = False
    soak_allowed: bool = False
    erp_writes_allowed: bool = False

    def __post_init__(self) -> None:
        for name in (
            "execution_allowed",
            "ready_for_metadata_preflight",
            "offline_candidate_ready",
            "credentials_available",
            "state_destination_ready",
            "report_destination_ready",
            "prior_run_present",
            "candidate_review_required",
            "live_session_ready",
            "record_sampling_allowed",
            "soak_allowed",
            "erp_writes_allowed",
        ):
            if type(getattr(self, name)) is not bool:
                raise TypeError("metadata readiness authority values must be booleans")
        if (
            self.execution_allowed
            or self.live_session_ready
            or self.record_sampling_allowed
            or self.soak_allowed
            or self.erp_writes_allowed
        ):
            raise MetadataPreflightError("metadata readiness cannot grant live authority")
        if self.max_total_attempted_gets != MAX_TOTAL_ATTEMPTED_GETS:
            raise MetadataPreflightError("metadata readiness budget is invalid")
        if type(self.attempted_gets) is not int or not 0 <= self.attempted_gets <= 100:
            raise MetadataPreflightError("metadata readiness attempt count is invalid")
        if self.prior_run_status not in {
            "none",
            "running",
            "complete",
            "failed",
            "interrupted",
            "uncertain",
        }:
            raise MetadataPreflightError("metadata readiness prior status is invalid")
        if self.prior_run_present != (self.prior_run_status != "none"):
            raise MetadataPreflightError("metadata readiness prior state is inconsistent")
        if self.ready_for_metadata_preflight and (
            self.prior_run_present
            or not self.credentials_available
            or not self.state_destination_ready
            or not self.report_destination_ready
        ):
            raise MetadataPreflightError("metadata preflight readiness is inconsistent")
        if self.offline_candidate_ready and not self.candidate_review_required:
            raise MetadataPreflightError("metadata candidate cannot bypass review")
        if self.offline_candidate_ready and self.prior_run_status != "complete":
            raise MetadataPreflightError("metadata candidate readiness is inconsistent")


@dataclass(frozen=True, slots=True)
class MetadataPreflightRunReport:
    execution_allowed: bool
    status: str
    failure_stage: str
    failure_category: str
    started_at: datetime
    ended_at: datetime
    attempted_gets: int
    max_total_attempted_gets: int
    company_count: int
    company_catalog_complete: bool
    doctype_catalog_count: int
    doctype_catalog_complete: bool
    metadata_succeeded: int
    metadata_failed: int
    sensitive_metadata_excluded: int
    candidate_entity_count: int
    candidate_field_count: int
    candidate_review_required: bool
    record_samples: int = 0
    erp_writes: int = 0
    soak_started: bool = False

    def __post_init__(self) -> None:
        if self.status not in {"complete", "failed", "interrupted"}:
            raise MetadataPreflightError("metadata preflight status is invalid")
        if self.failure_stage not in _FAILURE_STAGES:
            raise MetadataPreflightError("metadata preflight failure stage is invalid")
        if self.failure_category not in _FAILURE_CATEGORIES:
            raise MetadataPreflightError("metadata preflight failure category is invalid")
        if self.status == "complete" and (
            self.failure_stage != "none" or self.failure_category != "none"
        ):
            raise MetadataPreflightError("completed metadata preflight cannot report failure")
        if self.status == "failed" and (
            self.failure_stage == "none" or self.failure_category in {"none", "interrupted"}
        ):
            raise MetadataPreflightError("failed metadata preflight requires a safe category")
        if self.status == "interrupted" and self.failure_category != "interrupted":
            raise MetadataPreflightError("interrupted metadata preflight category is invalid")
        for value in (self.started_at, self.ended_at):
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise MetadataPreflightError("metadata preflight time must be timezone-aware")
        if self.ended_at < self.started_at:
            raise MetadataPreflightError("metadata preflight time range is invalid")
        if self.max_total_attempted_gets != MAX_TOTAL_ATTEMPTED_GETS:
            raise MetadataPreflightError("metadata preflight budget is invalid")
        counts = (
            self.attempted_gets,
            self.company_count,
            self.doctype_catalog_count,
            self.metadata_succeeded,
            self.metadata_failed,
            self.sensitive_metadata_excluded,
            self.candidate_entity_count,
            self.candidate_field_count,
            self.record_samples,
            self.erp_writes,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise MetadataPreflightError("metadata preflight counts must be non-negative integers")
        if self.attempted_gets > MAX_TOTAL_ATTEMPTED_GETS:
            raise MetadataPreflightError("metadata preflight exceeded its GET budget")
        if self.metadata_succeeded + self.metadata_failed > self.attempted_gets:
            raise MetadataPreflightError("metadata preflight request counts are inconsistent")
        if self.company_count > MAX_COMPANY_NAMES:
            raise MetadataPreflightError("metadata preflight company count is invalid")
        if self.doctype_catalog_count > MAX_METADATA_DOCTYPES:
            raise MetadataPreflightError("metadata preflight DocType count is invalid")
        if self.metadata_succeeded + self.metadata_failed > self.doctype_catalog_count:
            raise MetadataPreflightError("metadata preflight metadata counts are inconsistent")
        if self.candidate_entity_count > self.metadata_succeeded:
            raise MetadataPreflightError("metadata preflight candidate counts are inconsistent")
        if self.status == "complete" and self.attempted_gets != (
            2 + self.metadata_succeeded + self.metadata_failed
        ):
            raise MetadataPreflightError("completed metadata preflight accounting is invalid")
        for value in (
            self.execution_allowed,
            self.company_catalog_complete,
            self.doctype_catalog_complete,
            self.candidate_review_required,
            self.soak_started,
        ):
            if type(value) is not bool:
                raise TypeError("metadata preflight authority values must be booleans")
        if self.execution_allowed or self.record_samples or self.erp_writes or self.soak_started:
            raise MetadataPreflightError("metadata preflight report cannot claim live authority")
        if self.candidate_review_required != (self.status == "complete"):
            raise MetadataPreflightError("metadata candidate review status is inconsistent")


@dataclass(frozen=True, slots=True)
class MetadataCatalogRetryReport:
    """Aggregate outcome of the single authorized DocType-catalog retry."""

    execution_allowed: bool
    status: str
    failure_stage: str
    failure_category: str
    started_at: datetime
    ended_at: datetime
    attempted_gets: int
    request_attempts: int
    max_total_attempted_gets: int
    doctype_catalog_count: int
    doctype_catalog_complete: bool
    candidate_review_required: bool = False
    metadata_target_requests: int = 0
    record_samples: int = 0
    erp_writes: int = 0
    soak_started: bool = False

    def __post_init__(self) -> None:
        if self.status not in {"succeeded", "failed", "interrupted"}:
            raise MetadataPreflightError("metadata catalog retry status is invalid")
        if self.failure_stage not in {"none", "doctype_catalog"}:
            raise MetadataPreflightError("metadata catalog retry stage is invalid")
        if self.failure_category not in _FAILURE_CATEGORIES:
            raise MetadataPreflightError("metadata catalog retry category is invalid")
        if self.status == "succeeded" and (
            self.failure_stage != "none" or self.failure_category != "none"
        ):
            raise MetadataPreflightError("successful metadata catalog retry cannot report failure")
        if self.status == "failed" and (
            self.failure_stage != "doctype_catalog"
            or self.failure_category in {"none", "interrupted"}
        ):
            raise MetadataPreflightError("failed metadata catalog retry requires a safe category")
        if self.status == "interrupted" and (
            self.failure_stage != "doctype_catalog"
            or self.failure_category != "interrupted"
        ):
            raise MetadataPreflightError("interrupted metadata catalog retry is invalid")
        for value in (self.started_at, self.ended_at):
            if not isinstance(value, datetime) or value.utcoffset() is None:
                raise MetadataPreflightError("metadata catalog retry time must be timezone-aware")
        if self.ended_at < self.started_at:
            raise MetadataPreflightError("metadata catalog retry time range is invalid")
        if (
            self.attempted_gets != DOCTYPE_CATALOG_RETRY_ATTEMPT
            or self.request_attempts != 1
            or self.max_total_attempted_gets != MAX_TOTAL_ATTEMPTED_GETS
        ):
            raise MetadataPreflightError("metadata catalog retry accounting is invalid")
        for value in (
            self.doctype_catalog_count,
            self.metadata_target_requests,
            self.record_samples,
            self.erp_writes,
        ):
            if type(value) is not int or value < 0:
                raise MetadataPreflightError("metadata catalog retry counts are invalid")
        if self.doctype_catalog_count > MAX_METADATA_DOCTYPES:
            raise MetadataPreflightError("metadata catalog retry count is invalid")
        for value in (
            self.execution_allowed,
            self.doctype_catalog_complete,
            self.candidate_review_required,
            self.soak_started,
        ):
            if type(value) is not bool:
                raise TypeError("metadata catalog retry authority values must be booleans")
        if (
            self.execution_allowed
            or self.candidate_review_required
            or self.metadata_target_requests
            or self.record_samples
            or self.erp_writes
            or self.soak_started
        ):
            raise MetadataPreflightError("metadata catalog retry cannot grant broader authority")
        if self.status != "succeeded" and (
            self.doctype_catalog_count or self.doctype_catalog_complete
        ):
            raise MetadataPreflightError("failed metadata catalog retry cannot report a catalog")


def _scope_digest(config: ERPNextMetadataPreflightConfig) -> str:
    return hashlib.sha256(
        f"{config.tenant_id}\0metadata-only-preflight-v1".encode()
    ).hexdigest()


def _ledger_path(config: ERPNextMetadataPreflightConfig) -> Path:
    return config.state_directory / f"metadata-preflight-{_scope_digest(config)}.sqlite3"


def _candidate_path(config: ERPNextMetadataPreflightConfig) -> Path:
    return config.state_directory / f"metadata-candidate-{_scope_digest(config)}.json"


def _retry_report_path(config: ERPNextMetadataPreflightConfig) -> Path:
    return config.report_directory / (
        f"metadata-preflight-doctype-retry-{_scope_digest(config)}.json"
    )


def _path_entry_exists(path: Path) -> bool:
    try:
        path.lstat()
    except FileNotFoundError:
        return False
    except OSError:
        return True
    return True


class _RunLedger:
    def __init__(self, path: Path) -> None:
        self.path = path

    @classmethod
    def create(cls, path: Path) -> _RunLedger:
        _reject_symlink_path(path)
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        try:
            descriptor = os.open(path, flags, 0o600)
        except OSError as exc:
            raise MetadataPreflightError("metadata preflight already has durable state") from exc
        os.close(descriptor)
        ledger = cls(path)
        try:
            with ledger._connect() as connection:
                connection.execute(_LEDGER_SCHEMA)
                connection.execute(
                    "INSERT INTO preflight_run (singleton, status, attempted_gets) "
                    "VALUES (1, 'running', 0)"
                )
            _enforce_private_file(path)
        except Exception:
            try:
                path.unlink(missing_ok=True)
            except OSError:
                pass
            raise
        return ledger

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def snapshot(self) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT status, attempted_gets, company_count, doctype_catalog_count, "
                "metadata_succeeded, metadata_failed, candidate_entity_count, "
                "candidate_field_count, company_catalog_complete, "
                "doctype_catalog_complete FROM preflight_run WHERE singleton = 1"
            ).fetchone()
        if row is None:
            raise MetadataPreflightError("metadata preflight state is invalid")
        names = (
            "status",
            "attempted_gets",
            "company_count",
            "doctype_catalog_count",
            "metadata_succeeded",
            "metadata_failed",
            "candidate_entity_count",
            "candidate_field_count",
            "company_catalog_complete",
            "doctype_catalog_complete",
        )
        snapshot = dict(zip(names, row, strict=True))
        _validate_ledger_snapshot(snapshot)
        return snapshot

    def reserve_attempt(self) -> int:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, attempted_gets FROM preflight_run WHERE singleton = 1"
            ).fetchone()
            if row is None or row[0] != "running":
                raise MetadataPreflightError("metadata preflight is not running")
            attempted = int(row[1])
            if attempted >= MAX_TOTAL_ATTEMPTED_GETS:
                raise MetadataPreflightBudgetError("metadata GET budget exhausted")
            attempted += 1
            connection.execute(
                "UPDATE preflight_run SET attempted_gets = ? WHERE singleton = 1",
                (attempted,),
            )
            connection.commit()
            return attempted
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reserve_failed_doctype_retry(self) -> int:
        """Atomically spend attempt three without changing the failed run state."""

        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, attempted_gets, company_count, doctype_catalog_count, "
                "metadata_succeeded, metadata_failed, candidate_entity_count, "
                "candidate_field_count, company_catalog_complete, "
                "doctype_catalog_complete FROM preflight_run WHERE singleton = 1"
            ).fetchone()
            if row is None:
                raise _MetadataCatalogRetryRefused("metadata catalog retry state is invalid")
            names = (
                "status",
                "attempted_gets",
                "company_count",
                "doctype_catalog_count",
                "metadata_succeeded",
                "metadata_failed",
                "candidate_entity_count",
                "candidate_field_count",
                "company_catalog_complete",
                "doctype_catalog_complete",
            )
            snapshot = dict(zip(names, row, strict=True))
            _validate_ledger_snapshot(snapshot)
            if not (
                snapshot["status"] == "failed"
                and snapshot["attempted_gets"] == DOCTYPE_CATALOG_RETRY_PRIOR_ATTEMPTS
                and 0 < snapshot["company_count"] <= MAX_COMPANY_NAMES
                and snapshot["doctype_catalog_count"] == 0
                and snapshot["metadata_succeeded"] == 0
                and snapshot["metadata_failed"] == 0
                and snapshot["candidate_entity_count"] == 0
                and snapshot["candidate_field_count"] == 0
                and snapshot["company_catalog_complete"] == 1
                and snapshot["doctype_catalog_complete"] == 0
            ):
                raise _MetadataCatalogRetryRefused(
                    "metadata catalog retry is not authorized for this state"
                )
            cursor = connection.execute(
                "UPDATE preflight_run SET attempted_gets = ? "
                "WHERE singleton = 1 AND status = 'failed' AND attempted_gets = ?",
                (DOCTYPE_CATALOG_RETRY_ATTEMPT, DOCTYPE_CATALOG_RETRY_PRIOR_ATTEMPTS),
            )
            if cursor.rowcount != 1:
                raise _MetadataCatalogRetryRefused(
                    "metadata catalog retry reservation failed"
                )
            connection.commit()
            return DOCTYPE_CATALOG_RETRY_ATTEMPT
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def update(self, **values: int | str) -> None:
        allowed = {
            "status",
            "company_count",
            "doctype_catalog_count",
            "metadata_succeeded",
            "metadata_failed",
            "candidate_entity_count",
            "candidate_field_count",
            "company_catalog_complete",
            "doctype_catalog_complete",
        }
        if not values or not set(values) <= allowed:
            raise MetadataPreflightError("invalid metadata preflight state update")
        assignments = ", ".join(f"{name} = ?" for name in values)
        with self._connect() as connection:
            connection.execute(
                f"UPDATE preflight_run SET {assignments} WHERE singleton = 1",
                tuple(values.values()),
            )


class _BudgetedOpener:
    def __init__(self, ledger: _RunLedger, opener: Callable[..., Any]) -> None:
        self._ledger = ledger
        self._opener = opener

    def __call__(self, request: Any, *, timeout: int) -> Any:
        self._ledger.reserve_attempt()
        return self._opener(request, timeout=timeout)


class _SingleRetryOpener:
    def __init__(
        self,
        ledger: _RunLedger,
        opener: Callable[..., Any],
        termination_requested: Callable[[], bool],
    ) -> None:
        self._ledger = ledger
        self._opener = opener
        self._termination_requested = termination_requested
        self._used = False

    def __call__(self, request: Any, *, timeout: int) -> Any:
        if self._used:
            raise _MetadataCatalogRetryRefused("metadata catalog retry already attempted")
        self._used = True
        self._ledger.reserve_failed_doctype_retry()
        if self._termination_requested():
            raise _PreflightInterrupted
        return self._opener(request, timeout=timeout)


def _read_name_catalog(
    config: ERPNextMetadataPreflightConfig,
    *,
    resource: str,
    requested: int,
    opener: Callable[..., Any],
) -> tuple[tuple[str, ...], bool]:
    _validate_resource(resource)
    query = urlencode(
        {
            "fields": json.dumps(_CATALOG_FIELDS, separators=(",", ":")),
            "limit_start": 0,
            "limit_page_length": requested,
            "order_by": "name asc",
        }
    )
    request = Request(
        f"{config.base_url}/api/resource/{quote(resource, safe='')}?{query}",
        headers={"Accept": "application/json"},
        method="GET",
    )
    try:
        with opener(request, timeout=DEFAULT_TIMEOUT_SECONDS) as response:
            final_url_getter = getattr(response, "geturl", None)
            final_url = final_url_getter() if callable(final_url_getter) else None
            if final_url and final_url != request.full_url:
                raise _CategorizedPreflightError("redirect_rejected")
            body = response.read(DEFAULT_MAX_RESPONSE_BYTES + 1)
    except _CategorizedPreflightError:
        raise
    except HTTPError as exc:
        if exc.code in {401, 403}:
            category = "http_permission"
        elif exc.code in {404, 405}:
            category = "endpoint_contract"
        elif 300 <= exc.code < 400:
            category = "redirect_rejected"
        else:
            category = "http_status"
        raise _CategorizedPreflightError(category) from None
    except (URLError, TimeoutError):
        raise _CategorizedPreflightError("transport_failure") from None
    if len(body) > DEFAULT_MAX_RESPONSE_BYTES:
        raise _CategorizedPreflightError("response_validation")
    try:
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, MetadataPreflightError):
        raise _CategorizedPreflightError("response_validation") from None
    if (
        not isinstance(payload, Mapping)
        or set(payload) != {"data"}
        or not isinstance(payload.get("data"), list)
    ):
        raise _CategorizedPreflightError("response_validation")
    if len(payload["data"]) > requested:
        raise _CategorizedPreflightError("response_validation")
    names: list[str] = []
    for row in payload["data"]:
        if not isinstance(row, Mapping) or set(row) != {"name"}:
            raise _CategorizedPreflightError("response_validation")
        name = row.get("name")
        if (
            not isinstance(name, str)
            or not name
            or name != name.strip()
            or any(not character.isprintable() for character in name)
        ):
            raise _CategorizedPreflightError("response_validation")
        try:
            _validate_resource(name)
        except ValueError:
            raise _CategorizedPreflightError("response_validation") from None
        names.append(name)
    if names != sorted(names) or len(names) != len(set(names)):
        raise _CategorizedPreflightError("response_validation")
    complete = len(names) < requested
    return tuple(names), complete


def _contains_sensitive_embedded_value(value: object) -> bool:
    if isinstance(value, Mapping):
        raw_fieldname = value.get("fieldname")
        raw_fieldtype = value.get("fieldtype")
        raw_default = value.get("default")
        default_nonempty = raw_default not in (None, "", False, 0, (), [], {})
        if default_nonempty and (
            raw_fieldtype == "Password"
            or (
                isinstance(raw_fieldname, str)
                and _name_contains_sensitive_token(raw_fieldname)
            )
        ):
            return True
        for raw_key, nested in value.items():
            key = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", str(raw_key))
            key = key.lower().replace("-", "_")
            nonempty = nested not in (None, "", False, 0, (), [], {})
            if (key in _SENSITIVE_VALUE_KEYS or is_sensitive_metadata_name(str(raw_key))) and nonempty:
                return True
            if _contains_sensitive_embedded_value(nested):
                return True
        return False
    if isinstance(value, (list, tuple)):
        return any(_contains_sensitive_embedded_value(item) for item in value)
    return False


def _name_contains_sensitive_token(value: str) -> bool:
    return is_sensitive_metadata_name(value)


def _write_private_json(path: Path, payload: Mapping[str, Any]) -> None:
    _reject_symlink_path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise LiveSessionStorageError("private metadata artifact could not be created") from exc
    try:
        data = (json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n").encode()
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        _enforce_private_file(path)
    except Exception:  # noqa: BLE001 - partial artifact is removed on any write failure
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise LiveSessionStorageError("private metadata artifact could not be written") from None


def _validated_candidate(
    config: ERPNextMetadataPreflightConfig,
    snapshot: Mapping[str, Any],
) -> Mapping[str, Any]:
    path = _candidate_path(config)
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
                raise MetadataPreflightError("metadata candidate is not private")
            body = stream.read(DEFAULT_MAX_RESPONSE_BYTES + 1)
    except (OSError, MetadataPreflightError) as exc:
        raise MetadataPreflightError("metadata candidate could not be validated") from exc
    if len(body) > DEFAULT_MAX_RESPONSE_BYTES:
        raise MetadataPreflightError("metadata candidate exceeds its size bound")
    try:
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, MetadataPreflightError) as exc:
        raise MetadataPreflightError("metadata candidate is invalid JSON") from exc
    expected = {
        "candidate_scopes",
        "companies",
        "company_catalog_complete",
        "doctype_catalog_complete",
        "review_required",
        "schema_version",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise MetadataPreflightError("metadata candidate schema is invalid")
    if payload["schema_version"] != 1 or type(payload["schema_version"]) is not int:
        raise MetadataPreflightError("metadata candidate schema version is invalid")
    if payload["review_required"] is not True:
        raise MetadataPreflightError("metadata candidate must require review")
    for name in ("company_catalog_complete", "doctype_catalog_complete"):
        if type(payload[name]) is not bool or payload[name] != bool(snapshot[name]):
            raise MetadataPreflightError("metadata candidate completeness is inconsistent")
    companies = payload["companies"]
    if not isinstance(companies, list) or any(not isinstance(item, str) for item in companies):
        raise MetadataPreflightError("metadata candidate companies are invalid")
    if companies != sorted(companies) or len(companies) != len(set(companies)):
        raise MetadataPreflightError("metadata candidate companies are not ordered and unique")
    if len(companies) != snapshot["company_count"]:
        raise MetadataPreflightError("metadata candidate company count is inconsistent")
    if companies:
        ERPNextCompanyAuthorization(config.tenant_id, tuple(companies))
    scopes = payload["candidate_scopes"]
    if not isinstance(scopes, Mapping) or any(
        not isinstance(entity, str)
        or not isinstance(fields, list)
        or any(not isinstance(item, str) for item in fields)
        for entity, fields in scopes.items()
    ):
        raise MetadataPreflightError("metadata candidate scopes are invalid")
    reviewed = tuple(
        ReviewedMetadataScope(entity, tuple(fields))
        for entity, fields in scopes.items()
    )
    if len(reviewed) != snapshot["candidate_entity_count"] or sum(
        len(scope.fields) for scope in reviewed
    ) != snapshot["candidate_field_count"]:
        raise MetadataPreflightError("metadata candidate scope counts are inconsistent")
    return payload


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise MetadataPreflightError("metadata JSON object keys must be unique")
        result[name] = value
    return result


def metadata_preflight_config_from_environment(
    environment: Mapping[str, str],
) -> ERPNextMetadataPreflightConfig:
    """Load preflight configuration and credential variable names, never values."""

    required = (
        "ORION_LIVE_BASE_URL",
        "ORION_LIVE_TENANT_ID",
        "ORION_LIVE_AUTHORIZATION_REFERENCE",
        "ORION_LIVE_API_KEY_REF",
        "ORION_LIVE_API_SECRET_REF",
        "ORION_LIVE_STATE_DIR",
        "ORION_LIVE_REPORT_DIR",
    )
    values: dict[str, str] = {}
    for name in required:
        value = environment.get(name)
        if not isinstance(value, str) or not value:
            raise MetadataPreflightError("required metadata preflight configuration is unavailable")
        values[name] = value
    return ERPNextMetadataPreflightConfig(
        base_url=values["ORION_LIVE_BASE_URL"],
        tenant_id=values["ORION_LIVE_TENANT_ID"],
        authorization_reference=values["ORION_LIVE_AUTHORIZATION_REFERENCE"],
        credential_references=CredentialEnvironmentReferences(
            values["ORION_LIVE_API_KEY_REF"],
            values["ORION_LIVE_API_SECRET_REF"],
        ),
        state_directory=Path(values["ORION_LIVE_STATE_DIR"]),
        report_directory=Path(values["ORION_LIVE_REPORT_DIR"]),
    )


def inspect_metadata_preflight_readiness(
    config: ERPNextMetadataPreflightConfig,
    *,
    environment: Mapping[str, str],
) -> MetadataPreflightReadinessReport:
    """Inspect local launch or offline-candidate state without writes or network I/O."""

    state_ready = _destination_ready(config.state_directory, private=True)
    report_ready = _destination_ready(config.report_directory, private=True)
    try:
        config.credential_references.resolve(environment)
    except (LiveSessionError, TypeError):
        credentials_available = False
    else:
        credentials_available = True
    ledger_path = _ledger_path(config)
    candidate_path = _candidate_path(config)
    ledger_present = _path_entry_exists(ledger_path)
    candidate_present = _path_entry_exists(candidate_path)
    prior_present = ledger_present or candidate_present
    status = "none"
    attempted = 0
    snapshot: Mapping[str, Any] | None = None
    if ledger_present:
        try:
            info = ledger_path.lstat()
            if (
                not stat.S_ISREG(info.st_mode)
                or info.st_nlink != 1
                or info.st_uid != os.geteuid()
                or stat.S_IMODE(info.st_mode) != 0o600
            ):
                raise MetadataPreflightError("metadata preflight state is not private")
            snapshot = _RunLedger(ledger_path).snapshot()
            status = str(snapshot["status"])
            attempted = int(snapshot["attempted_gets"])
        except Exception:  # noqa: BLE001 - readiness reports fixed safe state only
            status = "uncertain"
    candidate_ready = False
    candidate_review_required = False
    if status == "complete" and candidate_present and snapshot is not None:
        try:
            candidate = _validated_candidate(config, snapshot)
            candidate_review_required = True
            candidate_ready = bool(
                candidate["companies"]
                and candidate["candidate_scopes"]
                and candidate["company_catalog_complete"]
                and candidate["doctype_catalog_complete"]
                and snapshot["metadata_failed"] == 0
            )
        except Exception:  # noqa: BLE001 - readiness reports fixed safe state only
            status = "uncertain"
            candidate_ready = False
            candidate_review_required = False
    elif candidate_present and not ledger_present:
        status = "uncertain"
    launch_ready = (
        not prior_present
        and credentials_available
        and state_ready
        and report_ready
    )
    return MetadataPreflightReadinessReport(
        execution_allowed=False,
        ready_for_metadata_preflight=launch_ready,
        offline_candidate_ready=candidate_ready,
        credentials_available=credentials_available,
        state_destination_ready=state_ready,
        report_destination_ready=report_ready,
        prior_run_present=prior_present,
        prior_run_status=status,
        attempted_gets=attempted,
        candidate_review_required=candidate_review_required,
    )


def _run_report(
    *,
    started_at: datetime,
    ended_at: datetime,
    ledger: _RunLedger,
    sensitive_metadata_excluded: int,
    failure_stage: str,
    failure_category: str,
) -> MetadataPreflightRunReport:
    state = ledger.snapshot()
    return MetadataPreflightRunReport(
        execution_allowed=False,
        status=str(state["status"]),
        failure_stage=failure_stage,
        failure_category=failure_category,
        started_at=started_at,
        ended_at=ended_at,
        attempted_gets=int(state["attempted_gets"]),
        max_total_attempted_gets=MAX_TOTAL_ATTEMPTED_GETS,
        company_count=int(state["company_count"]),
        company_catalog_complete=bool(state["company_catalog_complete"]),
        doctype_catalog_count=int(state["doctype_catalog_count"]),
        doctype_catalog_complete=bool(state["doctype_catalog_complete"]),
        metadata_succeeded=int(state["metadata_succeeded"]),
        metadata_failed=int(state["metadata_failed"]),
        sensitive_metadata_excluded=sensitive_metadata_excluded,
        candidate_entity_count=int(state["candidate_entity_count"]),
        candidate_field_count=int(state["candidate_field_count"]),
        candidate_review_required=str(state["status"]) == "complete",
    )


def metadata_preflight_report_json(
    report: (
        MetadataPreflightReadinessReport
        | MetadataPreflightRunReport
        | MetadataCatalogRetryReport
    ),
) -> str:
    payload = asdict(report)
    for name in ("started_at", "ended_at"):
        if name in payload:
            payload[name] = payload[name].isoformat()
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _write_run_report(
    config: ERPNextMetadataPreflightConfig,
    report: MetadataPreflightRunReport,
) -> None:
    stamp = report.ended_at.strftime("%Y%m%dT%H%M%S.%f%z")
    _write_private_json(
        config.report_directory / f"metadata-preflight-report-{stamp}.json",
        json.loads(metadata_preflight_report_json(report)),
    )


def _existing_private_retry_ledger(config: ERPNextMetadataPreflightConfig) -> _RunLedger:
    path = _ledger_path(config)
    try:
        info = path.lstat()
    except OSError as exc:
        raise MetadataPreflightError("metadata catalog retry requires existing state") from exc
    if (
        not stat.S_ISREG(info.st_mode)
        or info.st_nlink != 1
        or info.st_uid != os.geteuid()
        or stat.S_IMODE(info.st_mode) != 0o600
    ):
        raise MetadataPreflightError("metadata catalog retry state is not private")
    ledger = _RunLedger(path)
    ledger.snapshot()
    return ledger


def _write_retry_report(
    config: ERPNextMetadataPreflightConfig,
    report: MetadataCatalogRetryReport,
) -> None:
    _write_private_json(
        _retry_report_path(config),
        json.loads(metadata_preflight_report_json(report)),
    )


def run_erpnext_doctype_catalog_retry_once(
    config: ERPNextMetadataPreflightConfig,
    *,
    environment: Mapping[str, str],
    opener: Callable[..., Any] | None = None,
    clock: Callable[[], datetime] = utc_now,
    termination_requested: Callable[[], bool] | None = None,
) -> MetadataCatalogRetryReport:
    """Spend only attempt three on the failed run's DocType name catalog."""

    if not isinstance(config, ERPNextMetadataPreflightConfig):
        raise TypeError("config must be ERPNextMetadataPreflightConfig")
    termination = termination_requested or (lambda: False)
    if not callable(termination):
        raise TypeError("termination_requested must be callable")
    started_at = clock()
    if not isinstance(started_at, datetime) or started_at.utcoffset() is None:
        raise MetadataPreflightError("clock must return a timezone-aware datetime")
    _validate_configured_paths(config)  # type: ignore[arg-type]
    _prepare_destinations(config)  # type: ignore[arg-type]
    ledger = _existing_private_retry_ledger(config)
    if _path_entry_exists(_candidate_path(config)):
        raise MetadataPreflightError("metadata catalog retry conflicts with a candidate")
    if _path_entry_exists(_retry_report_path(config)):
        raise MetadataPreflightError("metadata catalog retry result already exists")
    credentials = config.credential_references.resolve(environment)
    single_retry = _SingleRetryOpener(ledger, opener or _default_opener, termination)
    authenticated = _AuthenticatedOpener(
        single_retry,
        credentials.api_key,
        credentials.api_secret,
    )
    status = "succeeded"
    failure_stage = "none"
    failure_category = "none"
    doctype_count = 0
    doctype_complete = False
    try:
        rows, doctype_complete = _read_name_catalog(
            config,
            resource="DocType",
            requested=MAX_METADATA_DOCTYPES + 1,
            opener=authenticated,
        )
        doctype_count = len(rows[:MAX_METADATA_DOCTYPES])
    except (KeyboardInterrupt, SystemExit, _PreflightInterrupted):
        status = "interrupted"
        failure_stage = "doctype_catalog"
        failure_category = "interrupted"
    except _MetadataCatalogRetryRefused:
        raise
    except _CategorizedPreflightError as exc:
        status = "failed"
        failure_stage = "doctype_catalog"
        failure_category = exc.category
    except (LiveSessionError, TypeError, ValueError):
        status = "failed"
        failure_stage = "doctype_catalog"
        failure_category = "scope_validation"
    except Exception:  # noqa: BLE001 - retry report exposes only fixed categories
        status = "failed"
        failure_stage = "doctype_catalog"
        failure_category = "internal_failure"
    state = ledger.snapshot()
    if state["attempted_gets"] != DOCTYPE_CATALOG_RETRY_ATTEMPT:
        raise MetadataPreflightError("metadata catalog retry did not reserve attempt three")
    ended_at = clock()
    report = MetadataCatalogRetryReport(
        execution_allowed=False,
        status=status,
        failure_stage=failure_stage,
        failure_category=failure_category,
        started_at=started_at,
        ended_at=ended_at,
        attempted_gets=DOCTYPE_CATALOG_RETRY_ATTEMPT,
        request_attempts=1,
        max_total_attempted_gets=MAX_TOTAL_ATTEMPTED_GETS,
        doctype_catalog_count=doctype_count,
        doctype_catalog_complete=doctype_complete,
    )
    _write_retry_report(config, report)
    return report


def run_erpnext_metadata_preflight(
    config: ERPNextMetadataPreflightConfig,
    *,
    environment: Mapping[str, str],
    opener: Callable[..., Any] | None = None,
    clock: Callable[[], datetime] = utc_now,
    termination_requested: Callable[[], bool] | None = None,
) -> MetadataPreflightRunReport:
    """Execute one durable metadata-only allowance; never records, writes, or soak."""

    if not isinstance(config, ERPNextMetadataPreflightConfig):
        raise TypeError("config must be ERPNextMetadataPreflightConfig")
    termination = termination_requested or (lambda: False)
    if not callable(termination):
        raise TypeError("termination_requested must be callable")
    started_at = clock()
    if not isinstance(started_at, datetime) or started_at.utcoffset() is None:
        raise MetadataPreflightError("clock must return a timezone-aware datetime")
    _validate_configured_paths(config)  # type: ignore[arg-type]
    _prepare_destinations(config)  # type: ignore[arg-type]
    if _path_entry_exists(_ledger_path(config)) or _path_entry_exists(_candidate_path(config)):
        raise MetadataPreflightError("metadata preflight has prior durable state")
    credentials = config.credential_references.resolve(environment)
    ledger = _RunLedger.create(_ledger_path(config))
    budgeted = _BudgetedOpener(ledger, opener or _default_opener)
    authenticated = _AuthenticatedOpener(
        budgeted,
        credentials.api_key,
        credentials.api_secret,
    )
    sensitive_excluded = 0
    failure_stage = "company_catalog"
    failure_category = "none"
    try:
        if termination():
            raise _PreflightInterrupted
        company_rows, companies_complete = _read_name_catalog(
            config,
            resource="Company",
            requested=MAX_COMPANY_NAMES + 1,
            opener=authenticated,
        )
        companies = company_rows[:MAX_COMPANY_NAMES]
        ledger.update(
            company_count=len(companies),
            company_catalog_complete=int(companies_complete),
        )
        if not companies:
            raise MetadataPreflightError("metadata preflight found no exact company names")
        ERPNextCompanyAuthorization(config.tenant_id, companies)
        if termination():
            raise _PreflightInterrupted
        failure_stage = "doctype_catalog"
        doctype_rows, doctypes_complete = _read_name_catalog(
            config,
            resource="DocType",
            requested=MAX_METADATA_DOCTYPES + 1,
            opener=authenticated,
        )
        doctypes = doctype_rows[:MAX_METADATA_DOCTYPES]
        ledger.update(
            doctype_catalog_count=len(doctypes),
            doctype_catalog_complete=int(doctypes_complete),
        )
        candidates: list[ReviewedMetadataScope] = []
        succeeded = 0
        failed = 0
        failure_stage = "metadata_targets"
        for doctype in doctypes:
            if termination():
                raise _PreflightInterrupted
            if is_sensitive_metadata_name(doctype):
                sensitive_excluded += 1
                continue
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
                    sensitive_excluded += 1
                else:
                    candidate = derive_metadata_scope_candidate(
                        config.tenant_id,
                        doctype,
                        observations,
                    )
                    if candidate is not None:
                        candidates.append(candidate)
                succeeded += 1
            except (KeyboardInterrupt, SystemExit, _PreflightInterrupted):
                raise
            except Exception:  # noqa: BLE001 - each charged target is failure-counted
                failed += 1
            ledger.update(metadata_succeeded=succeeded, metadata_failed=failed)
        candidate_fields = sum(len(scope.fields) for scope in candidates)
        failure_stage = "candidate_persistence"
        _write_private_json(
            _candidate_path(config),
            {
                "candidate_scopes": {
                    scope.entity: list(scope.fields) for scope in candidates
                },
                "companies": list(companies),
                "company_catalog_complete": companies_complete,
                "doctype_catalog_complete": doctypes_complete,
                "review_required": True,
                "schema_version": 1,
            },
        )
        ledger.update(
            candidate_entity_count=len(candidates),
            candidate_field_count=candidate_fields,
            status="complete",
        )
        failure_stage = "none"
    except (KeyboardInterrupt, SystemExit, _PreflightInterrupted):
        failure_category = "interrupted"
        ledger.update(status="interrupted")
    except _CategorizedPreflightError as exc:
        failure_category = exc.category
        ledger.update(status="failed")
    except LiveSessionStorageError:
        failure_category = "storage_failure"
        ledger.update(status="failed")
    except (LiveSessionError, TypeError, ValueError):
        failure_category = "scope_validation"
        ledger.update(status="failed")
    except Exception:  # noqa: BLE001 - aggregate-only failure boundary
        failure_category = "internal_failure"
        ledger.update(status="failed")
    ended_at = clock()
    report = _run_report(
        started_at=started_at,
        ended_at=ended_at,
        ledger=ledger,
        sensitive_metadata_excluded=sensitive_excluded,
        failure_stage=failure_stage,
        failure_category=failure_category,
    )
    _write_run_report(config, report)
    return report


class _AuthenticatedOpener:
    def __init__(self, opener: Callable[..., Any], api_key: str, api_secret: str) -> None:
        self._opener = opener
        self._authorization = f"token {api_key}:{api_secret}"

    def __call__(self, request: Any, *, timeout: int) -> Any:
        request.add_header("Authorization", self._authorization)
        return self._opener(request, timeout=timeout)


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
        signum: signal.getsignal(signum)
        for signum in (signal.SIGINT, signal.SIGTERM)
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
        description="Run the bounded ERPNext metadata-only preflight",
        allow_abbrev=False,
    )
    execution = parser.add_mutually_exclusive_group()
    execution.add_argument(
        "--execute-metadata-preflight",
        action="store_true",
        help="explicitly use the separately authorized metadata-only allowance",
    )
    execution.add_argument(
        "--retry-doctype-catalog-once",
        action="store_true",
        help="explicitly spend attempt three on the failed DocType catalog only",
    )
    args = parser.parse_args(argv)
    try:
        config = metadata_preflight_config_from_environment(os.environ)
        readiness = inspect_metadata_preflight_readiness(config, environment=os.environ)
    except Exception:  # noqa: BLE001 - CLI emits fixed safe categories only
        print('{"execution_allowed":false,"ready":false,"status":"configuration_invalid"}')
        return 2
    if args.retry_doctype_catalog_once:
        try:
            with _termination_signals() as termination:
                report = run_erpnext_doctype_catalog_retry_once(
                    config,
                    environment=os.environ,
                    termination_requested=termination,
                )
        except Exception:  # noqa: BLE001 - CLI emits fixed safe categories only
            print('{"execution_allowed":false,"ready":false,"status":"catalog_retry_refused"}')
            return 2
        print(metadata_preflight_report_json(report))
        if report.status == "interrupted":
            return 130
        return 0 if report.status == "succeeded" else 2
    if not args.execute_metadata_preflight:
        print(metadata_preflight_report_json(readiness))
        return 0 if readiness.ready_for_metadata_preflight or readiness.offline_candidate_ready else 2
    if not readiness.ready_for_metadata_preflight:
        print(metadata_preflight_report_json(readiness))
        return 2
    try:
        with _termination_signals() as termination:
            report = run_erpnext_metadata_preflight(
                config,
                environment=os.environ,
                termination_requested=termination,
            )
    except Exception:  # noqa: BLE001 - CLI emits fixed safe categories only
        print('{"execution_allowed":false,"ready":false,"status":"preflight_failed"}')
        return 2
    print(metadata_preflight_report_json(report))
    if report.status == "interrupted":
        return 130
    return 0 if report.status == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DOCTYPE_CATALOG_RETRY_ATTEMPT",
    "MAX_TOTAL_ATTEMPTED_GETS",
    "ERPNextMetadataPreflightConfig",
    "MetadataCatalogRetryReport",
    "MetadataPreflightError",
    "MetadataPreflightReadinessReport",
    "MetadataPreflightRunReport",
    "inspect_metadata_preflight_readiness",
    "main",
    "metadata_preflight_config_from_environment",
    "metadata_preflight_report_json",
    "run_erpnext_doctype_catalog_retry_once",
    "run_erpnext_metadata_preflight",
]
