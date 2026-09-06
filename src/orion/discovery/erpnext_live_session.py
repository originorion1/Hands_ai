"""Governed ERPNext live-session composition with no implicit authority.

This module assembles existing read-only adapters and the bounded shadow-soak
runtime.  Importing it, loading configuration, and running readiness checks do
not perform network I/O.  A caller must explicitly invoke the execute path.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import re
import signal
import stat
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Any

from ..contracts import Observation, utc_now
from ..discovery.checkpoint import StudyCheckpoint
from ..history.evidence import HistoricalEvidenceBatch, HistoricalEvidenceError
from ..learning.autonomous_loop import (
    AuthorizationEnvelope,
    LearningObjective,
    StudyIntent,
)
from ..learning.shadow_soak import (
    ShadowSoakReport,
    ShadowSoakSessionEnvelope,
    ShadowSoakStopReason,
    run_autonomous_shadow_soak,
)
from ..learning.study_capability import StudyCapability, derive_study_capability
from ..stores.sqlite_checkpoint import SQLiteStudyCheckpointStore
from ..stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore
from ..understanding.metadata import (
    MetadataUnderstanding,
    StructuralEntity,
    build_metadata_understanding,
    is_collection_relationship,
)
from .erpnext_adapter import _default_opener, _normalize_base_url
from .erpnext_historical_capture import (
    _repository_root,
    default_historical_evidence_path,
)
from .erpnext_metadata_adapter import ERPNextMetadataAdapter
from .erpnext_study_router import run_erpnext_governed_study
from .planner import validate_discovery_target

FULL_SOAK_STUDY_GETS = 100
FULL_SOAK_CYCLES = 100
FULL_SOAK_OBSERVATIONS_PER_STUDY = 5
FULL_SOAK_CUMULATIVE_OBSERVATIONS = 500
FULL_SOAK_CONSECUTIVE_NON_PROGRESS = 5
FULL_SOAK_SECONDS = 6 * 60 * 60

_ENVIRONMENT_NAME = re.compile(r"[A-Z][A-Z0-9_]{0,127}\Z")
_SENSITIVE_TOKENS = frozenset(
    {
        "access",
        "api",
        "auth",
        "authentication",
        "authorization",
        "credential",
        "credentials",
        "hash",
        "key",
        "oauth",
        "password",
        "passwd",
        "private",
        "salt",
        "secret",
        "session",
        "signature",
        "token",
        "user",
    }
)
_AUDIT_ONLY_FIELDS = frozenset({"name", "company", "docstatus"})
_LAYOUT_FIELD_TYPES = frozenset(
    {
        "Button",
        "Column Break",
        "Fold",
        "Heading",
        "HTML",
        "Image",
        "Section Break",
        "Tab Break",
    }
)
_SAFE_FAILURE_CATEGORIES = frozenset(
    {
        "erp_contract_failure",
        "no_progress",
        "persistence_failure",
        "persistence_failure_after_verified_append",
        "persistence_integrity_failure",
        "runner_failure_after_verified_append",
        "tenant_scope_mismatch",
        "unsupported_capability",
    }
)
_SAFE_STOP_REASONS = frozenset(reason.value for reason in ShadowSoakStopReason) | {
    "metadata_preflight_failure"
}


class LiveSessionError(ValueError):
    """Raised when live-session preparation violates a fixed boundary."""


class LiveSessionStorageError(LiveSessionError):
    """Raised when customer-local state cannot be opened safely."""


class _PreflightTermination(BaseException):
    pass


class _PreflightDuration(BaseException):
    pass


def _safe_text(value: object, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise LiveSessionError(f"{label} must be non-empty")
    if value != value.strip() or any(not character.isprintable() for character in value):
        raise LiveSessionError(f"{label} must be normalized text")
    return value


def _tokens(value: str) -> frozenset[str]:
    normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", value)
    return frozenset(re.findall(r"[a-z0-9]+", normalized.lower()))


def _reject_sensitive_name(value: str, label: str) -> None:
    if _tokens(value) & _SENSITIVE_TOKENS:
        raise LiveSessionError(f"{label} is excluded by sensitive-scope policy")


@dataclass(frozen=True, slots=True)
class ERPNextCompanyAuthorization:
    """Exact companies approved for one tenant-bound session."""

    tenant_id: str = field(repr=False)
    companies: tuple[str, ...] = field(repr=False)

    def __post_init__(self) -> None:
        _safe_text(self.tenant_id, "tenant_id")
        if not self.companies or len(self.companies) != len(set(self.companies)):
            raise LiveSessionError("company authorization must be non-empty and unique")
        for company in self.companies:
            _safe_text(company, "authorized company")
            if len(company) > 256 or "*" in company:
                raise LiveSessionError("authorized company is not an exact bounded value")

    def permits(self, company: str) -> bool:
        return company in self.companies


@dataclass(frozen=True, slots=True)
class ReviewedMetadataScope:
    """Human-reviewed business fields that metadata may validate, never widen."""

    entity: str
    fields: tuple[str, ...]

    def __post_init__(self) -> None:
        try:
            validate_discovery_target(self.entity)
        except ValueError as exc:
            raise LiveSessionError("reviewed entity is invalid") from exc
        _reject_sensitive_name(self.entity, "reviewed entity")
        if not self.fields or len(self.fields) != len(set(self.fields)):
            raise LiveSessionError("reviewed fields must be non-empty and unique")
        for name in self.fields:
            try:
                validate_discovery_target(name)
            except ValueError as exc:
                raise LiveSessionError("reviewed field is invalid") from exc
            if name in _AUDIT_ONLY_FIELDS:
                raise LiveSessionError("transport audit fields cannot be learning targets")
            _reject_sensitive_name(name, "reviewed field")


@dataclass(frozen=True, slots=True)
class CredentialEnvironmentReferences:
    """Names of environment variables; resolved secret values are never retained here."""

    api_key_variable: str
    api_secret_variable: str

    def __post_init__(self) -> None:
        for value in (self.api_key_variable, self.api_secret_variable):
            if not isinstance(value, str) or _ENVIRONMENT_NAME.fullmatch(value) is None:
                raise LiveSessionError("credential reference must be a safe environment name")
        if self.api_key_variable == self.api_secret_variable:
            raise LiveSessionError("credential references must be distinct")

    def resolve(self, environment: Mapping[str, str]) -> _ResolvedCredentials:
        if not isinstance(environment, Mapping):
            raise TypeError("environment must be a mapping")
        try:
            api_key = environment.get(self.api_key_variable)
            api_secret = environment.get(self.api_secret_variable)
        except Exception:  # noqa: BLE001 - untrusted mapping boundary is sanitized
            raise LiveSessionError("credential references could not be resolved") from None
        if not isinstance(api_key, str) or not api_key:
            raise LiveSessionError("API key credential is unavailable")
        if not isinstance(api_secret, str) or not api_secret:
            raise LiveSessionError("API secret credential is unavailable")
        if api_key == api_secret:
            raise LiveSessionError("resolved credentials must be distinct")
        return _ResolvedCredentials(api_key=api_key, api_secret=api_secret)


@dataclass(frozen=True, slots=True)
class _ResolvedCredentials:
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)


@dataclass(frozen=True, slots=True)
class LiveSessionLimits:
    """Separate preflight and shared-study request ceilings."""

    max_metadata_gets: int
    max_wall_clock_seconds: float = FULL_SOAK_SECONDS
    max_study_cycles: int = FULL_SOAK_CYCLES
    max_study_gets: int = FULL_SOAK_STUDY_GETS
    max_observations_per_study: int = FULL_SOAK_OBSERVATIONS_PER_STUDY
    max_cumulative_observations: int = FULL_SOAK_CUMULATIVE_OBSERVATIONS
    max_consecutive_non_progress: int = FULL_SOAK_CONSECUTIVE_NON_PROGRESS

    def __post_init__(self) -> None:
        for name in (
            "max_metadata_gets",
            "max_study_cycles",
            "max_study_gets",
            "max_observations_per_study",
            "max_cumulative_observations",
            "max_consecutive_non_progress",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise LiveSessionError(f"{name} must be a positive integer")
        if (
            not isinstance(self.max_wall_clock_seconds, (int, float))
            or isinstance(self.max_wall_clock_seconds, bool)
            or not math.isfinite(self.max_wall_clock_seconds)
            or self.max_wall_clock_seconds <= 0
        ):
            raise LiveSessionError("max_wall_clock_seconds must be positive")

    @property
    def max_live_gets(self) -> int:
        return self.max_metadata_gets + self.max_study_gets


@dataclass(frozen=True, slots=True)
class ERPNextLiveSessionConfig:
    """In-memory authority and destinations for exactly one live session."""

    base_url: str = field(repr=False)
    tenant_id: str = field(repr=False)
    authorization_reference: str = field(repr=False)
    company_authorization: ERPNextCompanyAuthorization = field(repr=False)
    reviewed_scopes: tuple[ReviewedMetadataScope, ...]
    credential_references: CredentialEnvironmentReferences = field(repr=False)
    state_directory: Path = field(repr=False)
    report_directory: Path = field(repr=False)
    objective: LearningObjective
    limits: LiveSessionLimits

    def __post_init__(self) -> None:
        _safe_text(self.tenant_id, "tenant_id")
        _safe_text(self.authorization_reference, "authorization reference")
        if _normalize_base_url(self.base_url) != self.base_url:
            raise LiveSessionError("base_url must be an exact HTTPS origin")
        if self.company_authorization.tenant_id != self.tenant_id:
            raise LiveSessionError("company authorization crosses tenant scope")
        if not self.reviewed_scopes:
            raise LiveSessionError("at least one reviewed metadata scope is required")
        entities = tuple(scope.entity for scope in self.reviewed_scopes)
        if len(entities) != len(set(entities)):
            raise LiveSessionError("reviewed metadata entities must be unique")
        if self.limits.max_metadata_gets != len(self.reviewed_scopes):
            raise LiveSessionError(
                "metadata GET budget must exactly match reviewed metadata scope"
            )
        if self.objective.objective_id == "":
            raise LiveSessionError("objective must be configured")
        _validate_configured_paths(self)


@dataclass(frozen=True, slots=True)
class LiveSessionReadinessReport:
    """Aggregate-only, non-mutating launch readiness facts."""

    ready: bool
    credentials_available: bool
    state_destination_ready: bool
    report_destination_ready: bool
    company_scope_count: int
    reviewed_entity_count: int
    reviewed_field_count: int
    metadata_get_budget: int
    study_get_budget: int
    max_live_gets: int
    live_gets_performed: int = 0
    erp_writes: int = 0
    recommendation_allowed: bool = False
    promotion_allowed: bool = False
    execution_allowed: bool = False


@dataclass(frozen=True, slots=True)
class LiveSessionRunReport:
    """Aggregate-only final report; customer values and paths are excluded."""

    session_started_at: datetime
    session_ended_at: datetime
    metadata_preflight_completed: bool
    metadata_get_budget: int
    metadata_gets: int
    study_get_budget: int
    study_gets: int
    max_live_gets: int
    total_live_gets: int
    company_scope_count: int
    reviewed_entity_count: int
    reviewed_field_count: int
    cycles_attempted: int
    cycles_completed: int
    observations_persisted: int
    evidence_batches_appended: int
    supported_proposal_count: int
    unsupported_proposal_count: int
    failure_category_counts: tuple[tuple[str, int], ...]
    distinct_entities_studied: int
    stop_reason: str
    erp_writes: int = 0
    recommendation_allowed: bool = False
    promotion_allowed: bool = False
    execution_allowed: bool = False

    def __post_init__(self) -> None:
        if self.stop_reason not in _SAFE_STOP_REASONS:
            raise LiveSessionError("live study stop reason is not allowlisted")
        if any(
            category not in _SAFE_FAILURE_CATEGORIES
            or type(count) is not int
            or count < 1
            for category, count in self.failure_category_counts
        ):
            raise LiveSessionError("live study failure categories are not allowlisted")
        if self.total_live_gets != self.metadata_gets + self.study_gets:
            raise LiveSessionError("live GET total is inconsistent")
        if self.total_live_gets > self.max_live_gets:
            raise LiveSessionError("live GET total exceeds configured maximum")
        if self.erp_writes != 0 or any(
            (
                self.recommendation_allowed,
                self.promotion_allowed,
                self.execution_allowed,
            )
        ):
            raise LiveSessionError("live study report cannot grant downstream authority")


class _AuthorizedCompanyEvidenceStore:
    """Reject durable evidence outside the current company/entity authorization."""

    def __init__(
        self,
        store: SQLiteHistoricalEvidenceStore,
        *,
        authorization: ERPNextCompanyAuthorization,
        entities: frozenset[str],
    ) -> None:
        self._store = store
        self._authorization = authorization
        self._entities = entities

    def append(self, batch: HistoricalEvidenceBatch) -> None:
        self._validate_batch(batch)
        self._store.append(batch)

    def load_all(
        self,
        *,
        tenant_id: str,
        resource: str,
    ) -> tuple[HistoricalEvidenceBatch, ...]:
        if tenant_id != self._authorization.tenant_id or resource not in self._entities:
            raise HistoricalEvidenceError("historical evidence crosses live authorization")
        batches = self._store.load_all(tenant_id=tenant_id, resource=resource)
        for batch in batches:
            self._validate_batch(batch)
        return batches

    def list_resources(self, *, tenant_id: str) -> tuple[str, ...]:
        if tenant_id != self._authorization.tenant_id:
            raise HistoricalEvidenceError("historical evidence crosses live authorization")
        resources = self._store.list_resources(tenant_id=tenant_id)
        if not set(resources).issubset(self._entities):
            raise HistoricalEvidenceError("historical evidence contains unauthorized resource")
        for resource in resources:
            self.load_all(tenant_id=tenant_id, resource=resource)
        return resources

    def _validate_batch(self, batch: HistoricalEvidenceBatch) -> None:
        if (
            not isinstance(batch, HistoricalEvidenceBatch)
            or batch.tenant_id != self._authorization.tenant_id
            or batch.resource not in self._entities
        ):
            raise HistoricalEvidenceError("historical evidence crosses live authorization")
        batch_companies: set[str] = set()
        for observation in batch.observations:
            record = observation.evidence.payload.get("record")
            if not isinstance(record, Mapping):
                raise HistoricalEvidenceError("historical evidence record is invalid")
            company = record.get("company")
            if not isinstance(company, str) or not self._authorization.permits(company):
                raise HistoricalEvidenceError("historical evidence company is unauthorized")
            batch_companies.add(company)
        if len(batch_companies) != 1:
            raise HistoricalEvidenceError("historical evidence batch crosses company scope")


class _MetadataReadBudget:
    def __init__(
        self,
        maximum: int,
        *,
        opener: Callable[..., Any],
        termination_requested: Callable[[], bool],
        deadline_reached: Callable[[], bool],
    ) -> None:
        self.maximum = maximum
        self.reads = 0
        self._opener = opener
        self._termination_requested = termination_requested
        self._deadline_reached = deadline_reached

    def __call__(self, request: Any, timeout: int) -> Any:
        if self._termination_requested():
            raise _PreflightTermination
        if self._deadline_reached():
            raise _PreflightDuration
        if self.reads >= self.maximum:
            raise LiveSessionError("metadata GET budget exhausted")
        self.reads += 1
        return self._opener(request, timeout=timeout)


class _CompanyScheduler:
    def __init__(self, authorization: ERPNextCompanyAuthorization) -> None:
        self._authorization = authorization
        self._index = 0

    def current(self) -> str:
        company = self._authorization.companies[
            self._index % len(self._authorization.companies)
        ]
        if not self._authorization.permits(company):
            raise LiveSessionError("company scheduler crossed authorization")
        return company

    def advance(self, company: str) -> None:
        if company != self.current():
            raise LiveSessionError("company scheduler advance is inconsistent")
        self._index += 1


def inspect_live_session_readiness(
    config: ERPNextLiveSessionConfig,
    *,
    environment: Mapping[str, str],
) -> LiveSessionReadinessReport:
    """Check launch inputs without network access, writes, or secret output."""

    state_ready = _destination_ready(config.state_directory, private=True)
    report_ready = _destination_ready(config.report_directory, private=False)
    try:
        config.credential_references.resolve(environment)
    except (LiveSessionError, TypeError):
        credentials_available = False
    else:
        credentials_available = True
    return LiveSessionReadinessReport(
        ready=credentials_available and state_ready and report_ready,
        credentials_available=credentials_available,
        state_destination_ready=state_ready,
        report_destination_ready=report_ready,
        company_scope_count=len(config.company_authorization.companies),
        reviewed_entity_count=len(config.reviewed_scopes),
        reviewed_field_count=sum(len(scope.fields) for scope in config.reviewed_scopes),
        metadata_get_budget=config.limits.max_metadata_gets,
        study_get_budget=config.limits.max_study_gets,
        max_live_gets=config.limits.max_live_gets,
    )


def run_erpnext_live_session(
    config: ERPNextLiveSessionConfig,
    *,
    environment: Mapping[str, str],
    metadata_opener: Callable[..., Any] | None = None,
    record_opener: Callable[..., Any] | None = None,
    clock: Callable[[], datetime] = utc_now,
    monotonic: Callable[[], float] = time.monotonic,
    termination_requested: Callable[[], bool] | None = None,
) -> LiveSessionRunReport:
    """Execute one explicitly requested, globally budgeted read-only session."""

    if not isinstance(config, ERPNextLiveSessionConfig):
        raise TypeError("config must be ERPNextLiveSessionConfig")
    termination = termination_requested or (lambda: False)
    if not callable(termination):
        raise TypeError("termination_requested must be callable")
    started_at = _read_clock(clock)
    started_tick = _read_monotonic(monotonic)
    last_tick = started_tick

    def elapsed() -> float:
        nonlocal last_tick
        current = _read_monotonic(monotonic)
        if current < last_tick:
            raise LiveSessionError("monotonic clock must not move backwards")
        last_tick = current
        return current - started_tick

    def deadline_reached() -> bool:
        return elapsed() >= config.limits.max_wall_clock_seconds

    credentials = config.credential_references.resolve(environment)
    state_directory, report_directory = _prepare_destinations(config)
    evidence_path = default_historical_evidence_path(
        config.tenant_id,
        resource="live-shadow-soak",
        state_root=state_directory,
    )
    checkpoint_path = _metadata_checkpoint_path(config, state_directory)
    _validate_storage_file(evidence_path)
    _validate_storage_file(checkpoint_path)
    _reject_storage_role_collision(evidence_path, checkpoint_path)
    try:
        evidence_store = SQLiteHistoricalEvidenceStore(evidence_path)
        _enforce_private_file(evidence_path)
        store = _AuthorizedCompanyEvidenceStore(
            evidence_store,
            authorization=config.company_authorization,
            entities=frozenset(scope.entity for scope in config.reviewed_scopes),
        )
        store.list_resources(tenant_id=config.tenant_id)
    except Exception:  # noqa: BLE001 - durable-state boundary fails closed
        report = _empty_run_report(
            config,
            started_at=started_at,
            ended_at=_read_clock(clock),
            metadata_gets=0,
            stop_reason=ShadowSoakStopReason.PERSISTENCE_FAILURE.value,
        )
        _write_report(report_directory, report)
        return report

    if termination():
        report = _empty_run_report(
            config,
            started_at=started_at,
            ended_at=_read_clock(clock),
            metadata_gets=0,
            stop_reason=ShadowSoakStopReason.USER_TERMINATION.value,
        )
        _write_report(report_directory, report)
        return report
    if deadline_reached():
        report = _empty_run_report(
            config,
            started_at=started_at,
            ended_at=_read_clock(clock),
            metadata_gets=0,
            stop_reason=ShadowSoakStopReason.DURATION_LIMIT.value,
        )
        _write_report(report_directory, report)
        return report

    metadata_budget = _MetadataReadBudget(
        config.limits.max_metadata_gets,
        opener=metadata_opener or _default_opener,
        termination_requested=termination,
        deadline_reached=deadline_reached,
    )

    try:
        observations = ERPNextMetadataAdapter(
            base_url=config.base_url,
            tenant_id=config.tenant_id,
            api_key=credentials.api_key,
            api_secret=credentials.api_secret,
            doctypes=tuple(scope.entity for scope in config.reviewed_scopes),
            opener=metadata_budget,
        ).discover()
        understanding = _validate_reviewed_understanding(config, observations)
    except (KeyboardInterrupt, SystemExit, _PreflightTermination):
        report = _empty_run_report(
            config,
            started_at=started_at,
            ended_at=_read_clock(clock),
            metadata_gets=metadata_budget.reads,
            stop_reason=ShadowSoakStopReason.USER_TERMINATION.value,
        )
        _write_report(report_directory, report)
        return report
    except _PreflightDuration:
        report = _empty_run_report(
            config,
            started_at=started_at,
            ended_at=_read_clock(clock),
            metadata_gets=metadata_budget.reads,
            stop_reason=ShadowSoakStopReason.DURATION_LIMIT.value,
        )
        _write_report(report_directory, report)
        return report
    except Exception:  # noqa: BLE001 - adapter/metadata boundary is failure-categorized
        report = _empty_run_report(
            config,
            started_at=started_at,
            ended_at=_read_clock(clock),
            metadata_gets=metadata_budget.reads,
            stop_reason="metadata_preflight_failure",
        )
        _write_report(report_directory, report)
        return report

    try:
        _persist_understanding_checkpoint(config, state_directory, understanding, clock)
    except (KeyboardInterrupt, SystemExit):
        report = _empty_run_report(
            config,
            started_at=started_at,
            ended_at=_read_clock(clock),
            metadata_gets=metadata_budget.reads,
            stop_reason=ShadowSoakStopReason.USER_TERMINATION.value,
        )
        _write_report(report_directory, report)
        return report
    except Exception:  # noqa: BLE001 - checkpoint boundary is failure-categorized
        report = _empty_run_report(
            config,
            started_at=started_at,
            ended_at=_read_clock(clock),
            metadata_gets=metadata_budget.reads,
            stop_reason=ShadowSoakStopReason.PERSISTENCE_FAILURE.value,
        )
        _write_report(report_directory, report)
        return report

    if termination():
        report = _empty_run_report(
            config,
            started_at=started_at,
            ended_at=_read_clock(clock),
            metadata_gets=metadata_budget.reads,
            stop_reason=ShadowSoakStopReason.USER_TERMINATION.value,
            metadata_preflight_completed=True,
        )
        _write_report(report_directory, report)
        return report

    remaining_seconds = config.limits.max_wall_clock_seconds - elapsed()
    if remaining_seconds <= 0:
        report = _empty_run_report(
            config,
            started_at=started_at,
            ended_at=_read_clock(clock),
            metadata_gets=metadata_budget.reads,
            stop_reason=ShadowSoakStopReason.DURATION_LIMIT.value,
            metadata_preflight_completed=True,
        )
        _write_report(report_directory, report)
        return report

    authorization = _study_authorization(config)
    session = ShadowSoakSessionEnvelope(
        authorization=authorization,
        max_wall_clock_seconds=remaining_seconds,
        max_study_cycles=config.limits.max_study_cycles,
        max_erp_reads=config.limits.max_study_gets,
        max_observations_per_study=config.limits.max_observations_per_study,
        max_cumulative_observations=config.limits.max_cumulative_observations,
        max_consecutive_non_progress=config.limits.max_consecutive_non_progress,
    )
    scheduler = _CompanyScheduler(config.company_authorization)
    transport = record_opener or _default_opener

    def study_runner(request, evidence_sink, permit_read):
        company = scheduler.current()

        def permitted_opener(http_request, *, timeout):
            permit_read()
            scheduler.advance(company)
            return transport(http_request, timeout=timeout)

        return run_erpnext_governed_study(
            request,
            envelope=authorization,
            understanding=understanding,
            base_url=config.base_url,
            api_key=credentials.api_key,
            api_secret=credentials.api_secret,
            company=company,
            opener=permitted_opener,
            evidence_sink=evidence_sink,
        )

    soak = run_autonomous_shadow_soak(
        config.objective,
        understanding,
        session,
        store=store,
        study_runner=study_runner,
        clock=clock,
        monotonic=monotonic,
        termination_requested=termination,
    )
    report = _run_report_from_soak(
        config,
        started_at=started_at,
        metadata_gets=metadata_budget.reads,
        soak=soak,
    )
    _write_report(report_directory, report)
    return report


def _validate_reviewed_understanding(
    config: ERPNextLiveSessionConfig,
    observations: tuple[Observation, ...],
) -> MetadataUnderstanding:
    allowed = frozenset(scope.entity for scope in config.reviewed_scopes)
    understanding = build_metadata_understanding(
        observations,
        tenant_id=config.tenant_id,
        allowed_doctypes=allowed,
    )
    if {entity.doctype for entity in understanding.entities} != allowed:
        raise LiveSessionError("metadata preflight did not resolve exact reviewed scope")
    by_name = {entity.doctype: entity for entity in understanding.entities}
    filtered: list[StructuralEntity] = []
    for scope in config.reviewed_scopes:
        entity = by_name[scope.entity]
        if entity.is_child_table or entity.is_single:
            raise LiveSessionError("reviewed entity is incompatible with company study")
        fields = {item.fieldname: item for item in entity.fields}
        company_field = fields.get("company")
        if company_field is None or is_collection_relationship(company_field):
            raise LiveSessionError("reviewed entity lacks exact company structure")
        selected = []
        for name in scope.fields:
            item = fields.get(name)
            if item is None:
                raise LiveSessionError("reviewed field is missing from metadata")
            if (
                item.hidden
                or item.read_only
                or is_collection_relationship(item)
                or item.fieldtype in _LAYOUT_FIELD_TYPES
            ):
                raise LiveSessionError("reviewed field is incompatible with safe record study")
            selected.append(item)
        candidate = replace(entity, fields=(*selected, company_field))
        for item in selected:
            capability = derive_study_capability(
                _study_intent(config, candidate.doctype, item.fieldname),
                MetadataUnderstanding(config.tenant_id, (candidate,)),
            )
            if capability not in {
                StudyCapability.ORDINARY_RECORD,
                StudyCapability.SUBMITTED_DOCUMENT,
            }:
                raise LiveSessionError("reviewed field has no safe company reader")
        filtered.append(candidate)
    return MetadataUnderstanding(config.tenant_id, tuple(filtered))


def _study_intent(
    config: ERPNextLiveSessionConfig,
    entity: str,
    field_name: str,
) -> StudyIntent:
    return StudyIntent(
        config.tenant_id,
        entity,
        (field_name,),
        "record_evidence",
        1,
        "reviewed structural field can provide evidence",
        "aggregate observations",
        "metadata preflight compatibility",
    )


def _study_authorization(config: ERPNextLiveSessionConfig) -> AuthorizationEnvelope:
    return AuthorizationEnvelope(
        tenant_id=config.tenant_id,
        objective_id=config.objective.objective_id,
        allowed_record_entities=frozenset(scope.entity for scope in config.reviewed_scopes),
        allowed_record_fields=tuple(
            (scope.entity, scope.fields) for scope in config.reviewed_scopes
        ),
        max_fields_per_proposal=1,
        max_records_per_proposal=config.limits.max_observations_per_study,
        max_cycles=config.limits.max_study_cycles,
        max_cumulative_records=config.limits.max_cumulative_observations,
        max_metadata_targets=config.limits.max_metadata_gets,
    )


def _persist_understanding_checkpoint(
    config: ERPNextLiveSessionConfig,
    state_directory: Path,
    understanding: MetadataUnderstanding,
    clock: Callable[[], datetime],
) -> None:
    path = _metadata_checkpoint_path(config, state_directory)
    _validate_storage_file(path)
    store = SQLiteStudyCheckpointStore(path)
    _enforce_private_file(path)
    latest = store.load_latest(tenant_id=config.tenant_id)
    checkpoint = StudyCheckpoint(
        tenant_id=config.tenant_id,
        sequence=1 if latest is None else latest.sequence + 1,
        created_at=_read_clock(clock),
        understanding=understanding,
        sampled_records=frozenset(),
        metadata_targets_studied=tuple(scope.entity for scope in config.reviewed_scopes),
        record_targets_sampled=(),
    )
    store.append(checkpoint)
    reloaded = store.load_latest(tenant_id=config.tenant_id)
    if reloaded != checkpoint:
        raise LiveSessionStorageError("metadata checkpoint reload failed")


def _metadata_checkpoint_path(
    config: ERPNextLiveSessionConfig,
    state_directory: Path,
) -> Path:
    digest = hashlib.sha256(
        f"{config.tenant_id}\0live-metadata".encode()
    ).hexdigest()
    return state_directory / f"study-checkpoints-{digest}.sqlite3"


def _read_clock(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.utcoffset() is None:
        raise LiveSessionError("clock must return a timezone-aware datetime")
    return value


def _read_monotonic(monotonic: Callable[[], float]) -> float:
    value = monotonic()
    if (
        not isinstance(value, (int, float))
        or isinstance(value, bool)
        or not math.isfinite(value)
    ):
        raise LiveSessionError("monotonic clock must return a finite number")
    return float(value)


def _empty_run_report(
    config: ERPNextLiveSessionConfig,
    *,
    started_at: datetime,
    ended_at: datetime,
    metadata_gets: int,
    stop_reason: str,
    metadata_preflight_completed: bool = False,
) -> LiveSessionRunReport:
    return LiveSessionRunReport(
        session_started_at=started_at,
        session_ended_at=ended_at,
        metadata_preflight_completed=metadata_preflight_completed,
        metadata_get_budget=config.limits.max_metadata_gets,
        metadata_gets=metadata_gets,
        study_get_budget=config.limits.max_study_gets,
        study_gets=0,
        max_live_gets=config.limits.max_live_gets,
        total_live_gets=metadata_gets,
        company_scope_count=len(config.company_authorization.companies),
        reviewed_entity_count=len(config.reviewed_scopes),
        reviewed_field_count=sum(len(scope.fields) for scope in config.reviewed_scopes),
        cycles_attempted=0,
        cycles_completed=0,
        observations_persisted=0,
        evidence_batches_appended=0,
        supported_proposal_count=0,
        unsupported_proposal_count=0,
        failure_category_counts=(),
        distinct_entities_studied=0,
        stop_reason=stop_reason,
    )


def _run_report_from_soak(
    config: ERPNextLiveSessionConfig,
    *,
    started_at: datetime,
    metadata_gets: int,
    soak: ShadowSoakReport,
) -> LiveSessionRunReport:
    return LiveSessionRunReport(
        session_started_at=started_at,
        session_ended_at=soak.session_ended_at,
        metadata_preflight_completed=True,
        metadata_get_budget=config.limits.max_metadata_gets,
        metadata_gets=metadata_gets,
        study_get_budget=config.limits.max_study_gets,
        study_gets=soak.erp_reads,
        max_live_gets=config.limits.max_live_gets,
        total_live_gets=metadata_gets + soak.erp_reads,
        company_scope_count=len(config.company_authorization.companies),
        reviewed_entity_count=len(config.reviewed_scopes),
        reviewed_field_count=sum(len(scope.fields) for scope in config.reviewed_scopes),
        cycles_attempted=soak.cycles_attempted,
        cycles_completed=soak.cycles_completed,
        observations_persisted=soak.observations_persisted,
        evidence_batches_appended=soak.evidence_batches_appended,
        supported_proposal_count=soak.supported_proposal_count,
        unsupported_proposal_count=soak.unsupported_proposal_count,
        failure_category_counts=soak.failure_category_counts,
        distinct_entities_studied=soak.distinct_entities_studied,
        stop_reason=soak.stop_reason.value,
    )


def live_session_report_json(report: LiveSessionReadinessReport | LiveSessionRunReport) -> str:
    """Serialize only the fixed safe report schemas."""

    if not isinstance(report, (LiveSessionReadinessReport, LiveSessionRunReport)):
        raise TypeError("unsupported live session report")
    payload = asdict(report)
    for name in ("session_started_at", "session_ended_at"):
        if name in payload:
            payload[name] = payload[name].isoformat()
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def _write_report(directory: Path, report: LiveSessionRunReport) -> Path:
    stamp = report.session_ended_at.strftime("%Y%m%dT%H%M%S.%f%z")
    path = directory / f"shadow-soak-report-{stamp}.json"
    _reject_symlink_path(path)
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    try:
        descriptor = os.open(path, flags, 0o600)
    except OSError as exc:
        raise LiveSessionStorageError("aggregate report could not be created") from exc
    try:
        data = (live_session_report_json(report) + "\n").encode("utf-8")
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
    except Exception:  # noqa: BLE001 - partial report writes are removed
        try:
            path.unlink(missing_ok=True)
        except OSError:
            pass
        raise LiveSessionStorageError("aggregate report could not be written") from None
    return path


def _validate_configured_paths(config: ERPNextLiveSessionConfig) -> None:
    state = _validate_absolute_path(config.state_directory, "state directory")
    report = _validate_absolute_path(config.report_directory, "report directory")
    if state == report or state in report.parents or report in state.parents:
        raise LiveSessionStorageError("state and report destinations must be isolated")
    if state.exists() and report.exists() and os.path.samefile(state, report):
        raise LiveSessionStorageError("state and report destinations must be distinct")
    repository = _repository_root()
    if repository is not None:
        root = repository.resolve(strict=False)
        for destination in (state.resolve(strict=False), report.resolve(strict=False)):
            try:
                destination.relative_to(root)
            except ValueError:
                continue
            raise LiveSessionStorageError("live session destinations must be outside repository")
    _reject_symlink_path(state)
    _reject_symlink_path(report)


def _validate_absolute_path(path: Path, label: str) -> Path:
    if not isinstance(path, Path) or not path.is_absolute() or ".." in path.parts:
        raise LiveSessionStorageError(f"{label} must be an absolute normalized path")
    return path


def _reject_symlink_path(path: Path) -> None:
    current = Path(path.anchor)
    for part in path.parts[1:]:
        current = current / part
        if current.is_symlink():
            raise LiveSessionStorageError("live session destination cannot use symlinks")


def _nearest_existing_parent(path: Path) -> Path | None:
    current = path
    while not current.exists():
        if current.parent == current:
            return None
        current = current.parent
    return current if current.is_dir() else None


def _destination_ready(path: Path, *, private: bool) -> bool:
    try:
        _reject_symlink_path(path)
        if path.exists():
            if not path.is_dir() or not os.access(path, os.W_OK | os.X_OK):
                return False
            if private:
                info = path.stat()
                return info.st_uid == os.geteuid() and stat.S_IMODE(info.st_mode) == 0o700
            return True
        parent = _nearest_existing_parent(path.parent)
        return parent is not None and os.access(parent, os.W_OK | os.X_OK)
    except OSError:
        return False


def _prepare_destinations(config: ERPNextLiveSessionConfig) -> tuple[Path, Path]:
    for path, mode in ((config.state_directory, 0o700), (config.report_directory, 0o700)):
        _reject_symlink_path(path)
        try:
            path.mkdir(parents=True, exist_ok=True, mode=mode)
        except OSError as exc:
            raise LiveSessionStorageError("live session destination could not be created") from exc
        _reject_symlink_path(path)
        if not path.is_dir():
            raise LiveSessionStorageError("live session destination is not a directory")
    try:
        config.state_directory.chmod(0o700)
        state_info = config.state_directory.stat()
    except OSError as exc:
        raise LiveSessionStorageError("private state permissions could not be enforced") from exc
    if state_info.st_uid != os.geteuid() or stat.S_IMODE(state_info.st_mode) != 0o700:
        raise LiveSessionStorageError("private state directory must be owner-only")
    return config.state_directory, config.report_directory


def _validate_storage_file(path: Path) -> None:
    _reject_symlink_path(path)
    if path.exists() and not path.is_file():
        raise LiveSessionStorageError("live session database path is not a regular file")


def _reject_storage_role_collision(first: Path, second: Path) -> None:
    if first.exists() and second.exists() and os.path.samefile(first, second):
        raise LiveSessionStorageError("live session storage roles must be distinct")


def _enforce_private_file(path: Path) -> None:
    try:
        path.chmod(0o600)
        info = path.stat()
    except OSError as exc:
        raise LiveSessionStorageError("private state file permissions could not be enforced") from exc
    if info.st_uid != os.geteuid() or stat.S_IMODE(info.st_mode) != 0o600:
        raise LiveSessionStorageError("private state file must be owner-only")


def live_session_config_from_environment(
    environment: Mapping[str, str],
) -> ERPNextLiveSessionConfig:
    """Load non-secret scope plus credential references from environment only."""

    if not isinstance(environment, Mapping):
        raise TypeError("environment must be a mapping")
    required = (
        "ORION_LIVE_BASE_URL",
        "ORION_LIVE_TENANT_ID",
        "ORION_LIVE_AUTHORIZATION_REFERENCE",
        "ORION_LIVE_COMPANIES_JSON",
        "ORION_LIVE_REVIEWED_SCOPES_JSON",
        "ORION_LIVE_API_KEY_REF",
        "ORION_LIVE_API_SECRET_REF",
        "ORION_LIVE_STATE_DIR",
        "ORION_LIVE_REPORT_DIR",
        "ORION_LIVE_OBJECTIVE_ID",
        "ORION_LIVE_OBJECTIVE_DESCRIPTION",
    )
    values: dict[str, str] = {}
    for name in required:
        value = environment.get(name)
        if not isinstance(value, str) or not value:
            raise LiveSessionError("required live-session configuration is unavailable")
        values[name] = value
    try:
        raw_companies = json.loads(values["ORION_LIVE_COMPANIES_JSON"])
        raw_scopes = json.loads(
            values["ORION_LIVE_REVIEWED_SCOPES_JSON"],
            object_pairs_hook=_unique_json_object,
        )
    except (json.JSONDecodeError, LiveSessionError):
        raise LiveSessionError("live-session JSON configuration is invalid") from None
    if not isinstance(raw_companies, list) or any(
        not isinstance(item, str) for item in raw_companies
    ):
        raise LiveSessionError("company configuration must be a JSON string list")
    if not isinstance(raw_scopes, Mapping) or any(
        not isinstance(entity, str)
        or not isinstance(fields, list)
        or any(not isinstance(item, str) for item in fields)
        for entity, fields in raw_scopes.items()
    ):
        raise LiveSessionError("reviewed scope configuration is invalid")
    tenant_id = values["ORION_LIVE_TENANT_ID"]
    scopes = tuple(
        ReviewedMetadataScope(entity, tuple(fields))
        for entity, fields in sorted(raw_scopes.items())
    )
    return ERPNextLiveSessionConfig(
        base_url=values["ORION_LIVE_BASE_URL"],
        tenant_id=tenant_id,
        authorization_reference=values["ORION_LIVE_AUTHORIZATION_REFERENCE"],
        company_authorization=ERPNextCompanyAuthorization(
            tenant_id,
            tuple(raw_companies),
        ),
        reviewed_scopes=scopes,
        credential_references=CredentialEnvironmentReferences(
            values["ORION_LIVE_API_KEY_REF"],
            values["ORION_LIVE_API_SECRET_REF"],
        ),
        state_directory=Path(values["ORION_LIVE_STATE_DIR"]),
        report_directory=Path(values["ORION_LIVE_REPORT_DIR"]),
        objective=LearningObjective(
            values["ORION_LIVE_OBJECTIVE_ID"],
            values["ORION_LIVE_OBJECTIVE_DESCRIPTION"],
            prohibited_effects=("ERP_WRITE", "RECOMMENDATION", "PROMOTION", "EXECUTION"),
        ),
        limits=LiveSessionLimits(max_metadata_gets=len(scopes)),
    )


def _unique_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for name, value in pairs:
        if name in result:
            raise LiveSessionError("live-session JSON object keys must be unique")
        result[name] = value
    return result


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
def _live_session_termination_signals() -> Iterator[_TerminationLatch]:
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
        description="Check or explicitly execute one governed ERPNext live session"
    )
    parser.add_argument(
        "--execute",
        action="store_true",
        help="explicitly perform the separately authorized read-only session",
    )
    args = parser.parse_args(argv)
    try:
        config = live_session_config_from_environment(os.environ)
        readiness = inspect_live_session_readiness(config, environment=os.environ)
    except Exception:  # noqa: BLE001 - CLI emits only a fixed safe category
        print('{"execution_allowed":false,"ready":false,"status":"configuration_invalid"}')
        return 2
    if not args.execute:
        print(live_session_report_json(readiness))
        return 0 if readiness.ready else 2
    if not readiness.ready:
        print(live_session_report_json(readiness))
        return 2
    try:
        with _live_session_termination_signals() as termination:
            report = run_erpnext_live_session(
                config,
                environment=os.environ,
                termination_requested=termination,
            )
    except Exception:  # noqa: BLE001 - CLI emits only a fixed safe category
        print('{"execution_allowed":false,"ready":false,"status":"session_failed"}')
        return 2
    print(live_session_report_json(report))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "CredentialEnvironmentReferences",
    "ERPNextCompanyAuthorization",
    "ERPNextLiveSessionConfig",
    "LiveSessionError",
    "LiveSessionLimits",
    "LiveSessionReadinessReport",
    "LiveSessionRunReport",
    "LiveSessionStorageError",
    "ReviewedMetadataScope",
    "inspect_live_session_readiness",
    "live_session_config_from_environment",
    "live_session_report_json",
    "main",
    "run_erpnext_live_session",
]
