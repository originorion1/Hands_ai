"""Single-use residual-budget launcher for the reviewed six-hour continuation."""

from __future__ import annotations

import argparse
import hashlib
import hmac
import json
import os
import re
import sqlite3
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from ..discovery.checkpoint import (
    StudyCheckpointError,
    checkpoint_checksum,
    checkpoint_from_json,
)
from ..history.evidence import (
    HistoricalEvidenceError,
    historical_evidence_checksum,
    historical_evidence_from_json,
)
from .erpnext_adapter import _default_opener
from .erpnext_bounded_trial import (
    TRIAL_METADATA_CUMULATIVE_MAX,
    TRIAL_METADATA_REQUESTS,
    TRIAL_OBSERVATIONS,
    TRIAL_STUDY_REQUESTS,
    _trial_inputs,
    _TrialLedger,
)
from .erpnext_historical_capture import default_historical_evidence_path
from .erpnext_live_session import (
    ERPNextLiveSessionConfig,
    LiveSessionError,
    LiveSessionLimits,
    LiveSessionRunReport,
    _AuthorizedCompanyEvidenceStore,
    _destination_ready,
    _live_session_termination_signals,
    _metadata_checkpoint_path,
    run_erpnext_live_session,
)
from .erpnext_metadata_preflight import (
    ERPNextMetadataPreflightConfig,
    MetadataPreflightError,
    _admin_binding_digest,
    _existing_private_retry_ledger,
    _unique_json_object,
)
from .erpnext_metadata_refresh import _read_private_bytes

PRIOR_METADATA_REQUESTS = TRIAL_METADATA_CUMULATIVE_MAX
CONTINUATION_METADATA_REQUESTS = 7
METADATA_CUMULATIVE_MAX = PRIOR_METADATA_REQUESTS + CONTINUATION_METADATA_REQUESTS
METADATA_ALLOWANCE_MAX = 100
PRIOR_STUDY_REQUESTS = TRIAL_STUDY_REQUESTS
CONTINUATION_STUDY_REQUESTS = 99
STUDY_CUMULATIVE_MAX = PRIOR_STUDY_REQUESTS + CONTINUATION_STUDY_REQUESTS
PRIOR_CYCLES = 1
CONTINUATION_CYCLES = 99
PRIOR_OBSERVATIONS = TRIAL_OBSERVATIONS
CONTINUATION_OBSERVATIONS = 499
OBSERVATIONS_PER_STUDY = 5
EFFECTIVE_ADDITIONAL_OBSERVATIONS = min(
    CONTINUATION_OBSERVATIONS,
    CONTINUATION_CYCLES * OBSERVATIONS_PER_STUDY,
)
CONTINUATION_SECONDS = 6 * 60 * 60
CONTINUATION_NON_PROGRESS = 5
CONTINUATION_COMBINED_REQUESTS = (
    CONTINUATION_METADATA_REQUESTS + CONTINUATION_STUDY_REQUESTS
)
CUMULATIVE_COMBINED_REQUESTS = METADATA_CUMULATIVE_MAX + STUDY_CUMULATIVE_MAX
ENTITY_COUNT = 7
FIELD_COUNT = 133

_CONTINUATION_SCHEMA = """
CREATE TABLE six_hour_continuation (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    status TEXT NOT NULL,
    candidate_digest TEXT NOT NULL,
    trial_report_digest TEXT NOT NULL,
    binding TEXT NOT NULL,
    prior_metadata_requests INTEGER NOT NULL CHECK (prior_metadata_requests = 49),
    prior_study_requests INTEGER NOT NULL CHECK (prior_study_requests = 1),
    prior_cycles INTEGER NOT NULL CHECK (prior_cycles = 1),
    prior_observations INTEGER NOT NULL CHECK (prior_observations = 1),
    metadata_requests INTEGER NOT NULL CHECK (metadata_requests BETWEEN 0 AND 7),
    study_requests INTEGER NOT NULL CHECK (study_requests BETWEEN 0 AND 99),
    stop_reason TEXT NOT NULL
)
"""


class SixHourContinuationError(MetadataPreflightError):
    """Raised before transport when continuation state is not exact."""


@dataclass(frozen=True, slots=True)
class SixHourContinuationReadinessReport:
    execution_allowed: bool
    offline_inputs_ready: bool
    credentials_available: bool
    ready_for_authorized_launch: bool
    status: str
    company_scope_count: int = 1
    reviewed_entity_count: int = ENTITY_COUNT
    reviewed_field_count: int = FIELD_COUNT
    prior_metadata_requests: int = PRIOR_METADATA_REQUESTS
    additional_metadata_get_budget: int = CONTINUATION_METADATA_REQUESTS
    metadata_cumulative_max: int = METADATA_CUMULATIVE_MAX
    metadata_allowance_max: int = METADATA_ALLOWANCE_MAX
    prior_study_requests: int = PRIOR_STUDY_REQUESTS
    additional_study_get_budget: int = CONTINUATION_STUDY_REQUESTS
    study_cumulative_max: int = STUDY_CUMULATIVE_MAX
    additional_combined_get_max: int = CONTINUATION_COMBINED_REQUESTS
    cumulative_combined_get_max: int = CUMULATIVE_COMBINED_REQUESTS
    max_wall_clock_seconds: int = CONTINUATION_SECONDS
    prior_cycles: int = PRIOR_CYCLES
    additional_cycle_budget: int = CONTINUATION_CYCLES
    cycle_cumulative_max: int = PRIOR_CYCLES + CONTINUATION_CYCLES
    prior_observations: int = PRIOR_OBSERVATIONS
    additional_observation_budget: int = CONTINUATION_OBSERVATIONS
    observation_cumulative_max: int = PRIOR_OBSERVATIONS + CONTINUATION_OBSERVATIONS
    effective_additional_observation_max: int = EFFECTIVE_ADDITIONAL_OBSERVATIONS
    effective_cumulative_observation_max: int = (
        PRIOR_OBSERVATIONS + EFFECTIVE_ADDITIONAL_OBSERVATIONS
    )
    max_observations_per_study: int = OBSERVATIONS_PER_STUDY
    max_consecutive_non_progress: int = CONTINUATION_NON_PROGRESS
    live_requests_performed: int = 0
    erp_writes: int = 0
    recommendation_allowed: bool = False
    promotion_allowed: bool = False

    def __post_init__(self) -> None:
        if self.status not in {"ready", "already_claimed", "invalid"}:
            raise SixHourContinuationError("continuation readiness status is invalid")
        if self.offline_inputs_ready != (self.status == "ready"):
            raise SixHourContinuationError("continuation readiness is inconsistent")
        if self.ready_for_authorized_launch != (
            self.offline_inputs_ready and self.credentials_available
        ):
            raise SixHourContinuationError("continuation credential readiness is inconsistent")
        if (
            self.execution_allowed
            or self.recommendation_allowed
            or self.promotion_allowed
            or self.erp_writes
            or self.live_requests_performed
        ):
            raise SixHourContinuationError("readiness cannot grant authority or claim effects")
        fixed = (
            self.company_scope_count,
            self.reviewed_entity_count,
            self.reviewed_field_count,
            self.prior_metadata_requests,
            self.additional_metadata_get_budget,
            self.metadata_cumulative_max,
            self.metadata_allowance_max,
            self.prior_study_requests,
            self.additional_study_get_budget,
            self.study_cumulative_max,
            self.additional_combined_get_max,
            self.cumulative_combined_get_max,
            self.max_wall_clock_seconds,
            self.prior_cycles,
            self.additional_cycle_budget,
            self.cycle_cumulative_max,
            self.prior_observations,
            self.additional_observation_budget,
            self.observation_cumulative_max,
            self.effective_additional_observation_max,
            self.effective_cumulative_observation_max,
            self.max_observations_per_study,
            self.max_consecutive_non_progress,
        )
        expected = (
            1,
            ENTITY_COUNT,
            FIELD_COUNT,
            PRIOR_METADATA_REQUESTS,
            CONTINUATION_METADATA_REQUESTS,
            METADATA_CUMULATIVE_MAX,
            METADATA_ALLOWANCE_MAX,
            PRIOR_STUDY_REQUESTS,
            CONTINUATION_STUDY_REQUESTS,
            STUDY_CUMULATIVE_MAX,
            CONTINUATION_COMBINED_REQUESTS,
            CUMULATIVE_COMBINED_REQUESTS,
            CONTINUATION_SECONDS,
            PRIOR_CYCLES,
            CONTINUATION_CYCLES,
            PRIOR_CYCLES + CONTINUATION_CYCLES,
            PRIOR_OBSERVATIONS,
            CONTINUATION_OBSERVATIONS,
            PRIOR_OBSERVATIONS + CONTINUATION_OBSERVATIONS,
            EFFECTIVE_ADDITIONAL_OBSERVATIONS,
            PRIOR_OBSERVATIONS + EFFECTIVE_ADDITIONAL_OBSERVATIONS,
            OBSERVATIONS_PER_STUDY,
            CONTINUATION_NON_PROGRESS,
        )
        if fixed != expected:
            raise SixHourContinuationError("continuation readiness limits are invalid")


@dataclass(frozen=True, slots=True)
class SixHourContinuationRunReport:
    execution_allowed: bool
    status: str
    stop_reason: str
    prior_metadata_requests: int
    additional_metadata_get_budget: int
    additional_metadata_gets: int
    metadata_cumulative_requests: int
    metadata_allowance_max: int
    prior_study_requests: int
    additional_study_get_budget: int
    additional_study_gets: int
    study_cumulative_requests: int
    additional_combined_get_max: int
    additional_combined_gets: int
    cumulative_combined_gets: int
    max_wall_clock_seconds: int
    prior_cycles: int
    additional_cycle_budget: int
    cycle_cumulative_max: int
    cycles_attempted: int
    cycles_completed: int
    prior_observations: int
    additional_observation_budget: int
    observation_cumulative_max: int
    effective_additional_observation_max: int
    effective_cumulative_observation_max: int
    observations_persisted: int
    company_scope_count: int
    reviewed_entity_count: int
    reviewed_field_count: int
    erp_writes: int
    recommendation_allowed: bool
    promotion_allowed: bool

    def __post_init__(self) -> None:
        if self.status not in {"complete", "failed", "interrupted"}:
            raise SixHourContinuationError("continuation result status is invalid")
        if (
            self.execution_allowed
            or self.recommendation_allowed
            or self.promotion_allowed
            or self.erp_writes
        ):
            raise SixHourContinuationError("continuation cannot grant authority or write")
        if (
            self.prior_metadata_requests != PRIOR_METADATA_REQUESTS
            or self.additional_metadata_get_budget != CONTINUATION_METADATA_REQUESTS
            or self.metadata_allowance_max != METADATA_ALLOWANCE_MAX
            or self.prior_study_requests != PRIOR_STUDY_REQUESTS
            or self.additional_study_get_budget != CONTINUATION_STUDY_REQUESTS
            or self.additional_combined_get_max != CONTINUATION_COMBINED_REQUESTS
            or self.max_wall_clock_seconds != CONTINUATION_SECONDS
            or self.prior_cycles != PRIOR_CYCLES
            or self.additional_cycle_budget != CONTINUATION_CYCLES
            or self.cycle_cumulative_max != PRIOR_CYCLES + CONTINUATION_CYCLES
            or self.prior_observations != PRIOR_OBSERVATIONS
            or self.additional_observation_budget != CONTINUATION_OBSERVATIONS
            or self.observation_cumulative_max
            != PRIOR_OBSERVATIONS + CONTINUATION_OBSERVATIONS
            or self.effective_additional_observation_max
            != EFFECTIVE_ADDITIONAL_OBSERVATIONS
            or self.effective_cumulative_observation_max
            != PRIOR_OBSERVATIONS + EFFECTIVE_ADDITIONAL_OBSERVATIONS
            or self.company_scope_count != 1
            or self.reviewed_entity_count != ENTITY_COUNT
            or self.reviewed_field_count != FIELD_COUNT
        ):
            raise SixHourContinuationError("continuation result limits are invalid")
        if not (
            0 <= self.additional_metadata_gets <= CONTINUATION_METADATA_REQUESTS
            and 0 <= self.additional_study_gets <= CONTINUATION_STUDY_REQUESTS
            and self.metadata_cumulative_requests
            == PRIOR_METADATA_REQUESTS + self.additional_metadata_gets
            and self.study_cumulative_requests
            == PRIOR_STUDY_REQUESTS + self.additional_study_gets
            and self.additional_combined_gets
            == self.additional_metadata_gets + self.additional_study_gets
            and self.cumulative_combined_gets
            == self.metadata_cumulative_requests + self.study_cumulative_requests
            and 0 <= self.cycles_attempted <= CONTINUATION_CYCLES
            and 0 <= self.cycles_completed <= self.cycles_attempted
            and 0 <= self.observations_persisted <= CONTINUATION_OBSERVATIONS
            and self.observations_persisted <= EFFECTIVE_ADDITIONAL_OBSERVATIONS
        ):
            raise SixHourContinuationError("continuation result accounting is invalid")


@dataclass(frozen=True, slots=True)
class _ContinuationInputs:
    preflight: ERPNextMetadataPreflightConfig
    live: ERPNextLiveSessionConfig
    candidate_path: Path
    candidate_digest: str
    trial_report_path: Path
    trial_report_digest: str


def _continuation_inputs(
    environment: Mapping[str, str],
    candidate_path: Path,
    candidate_sha256: str,
    trial_report_path: Path,
    trial_report_sha256: str,
) -> _ContinuationInputs:
    trial = _trial_inputs(environment, candidate_path, candidate_sha256)
    live = replace(
        trial.live,
        limits=LiveSessionLimits(
            max_metadata_gets=CONTINUATION_METADATA_REQUESTS,
            max_wall_clock_seconds=CONTINUATION_SECONDS,
            max_study_cycles=CONTINUATION_CYCLES,
            max_study_gets=CONTINUATION_STUDY_REQUESTS,
            max_observations_per_study=OBSERVATIONS_PER_STUDY,
            max_cumulative_observations=CONTINUATION_OBSERVATIONS,
            max_consecutive_non_progress=CONTINUATION_NON_PROGRESS,
        ),
    )
    if (
        not trial_report_path.is_absolute()
        or trial_report_path.parent != live.report_directory
        or re.fullmatch(r"shadow-soak-report-[0-9TZ.+-]+\.json", trial_report_path.name)
        is None
        or re.fullmatch(r"[0-9a-f]{64}", trial_report_sha256) is None
    ):
        raise SixHourContinuationError("completed trial report reference is invalid")
    return _ContinuationInputs(
        trial.preflight,
        live,
        trial.candidate_path,
        trial.candidate_digest,
        trial_report_path,
        trial_report_sha256,
    )


def _deserialize_private_database(path: Path, label: str) -> sqlite3.Connection:
    body = _read_private_bytes(path, label)
    if path.with_name(f"{path.name}-wal").exists():
        raise SixHourContinuationError(f"{label} has an active WAL sidecar")
    snapshot = bytearray(body)
    if len(snapshot) < 100 or snapshot[:16] != b"SQLite format 3\x00":
        raise SixHourContinuationError(f"{label} is invalid")
    if snapshot[18:20] == b"\x02\x02":
        snapshot[18:20] = b"\x01\x01"
    elif snapshot[18:20] != b"\x01\x01":
        raise SixHourContinuationError(f"{label} is invalid")
    connection = sqlite3.connect(":memory:")
    try:
        connection.deserialize(bytes(snapshot))
    except sqlite3.Error:
        connection.close()
        raise SixHourContinuationError(f"{label} is invalid") from None
    return connection


def _validate_trial_report(inputs: _ContinuationInputs) -> None:
    body = _read_private_bytes(inputs.trial_report_path, "completed trial report")
    if not hmac.compare_digest(hashlib.sha256(body).hexdigest(), inputs.trial_report_digest):
        raise SixHourContinuationError("completed trial report digest differs")
    try:
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, MetadataPreflightError):
        raise SixHourContinuationError("completed trial report is invalid") from None
    expected = {
        "metadata_preflight_completed": True,
        "metadata_get_budget": TRIAL_METADATA_REQUESTS,
        "metadata_gets": TRIAL_METADATA_REQUESTS,
        "study_get_budget": TRIAL_STUDY_REQUESTS,
        "study_gets": TRIAL_STUDY_REQUESTS,
        "max_live_gets": 8,
        "total_live_gets": 8,
        "company_scope_count": 1,
        "reviewed_entity_count": ENTITY_COUNT,
        "reviewed_field_count": FIELD_COUNT,
        "cycles_attempted": 1,
        "cycles_completed": 1,
        "observations_persisted": 1,
        "evidence_batches_appended": 1,
        "supported_proposal_count": 1,
        "unsupported_proposal_count": 0,
        "failure_category_counts": [],
        "distinct_entities_studied": 1,
        "distinct_companies_attempted": 1,
        "stop_reason": "cycle_limit",
        "erp_writes": 0,
        "recommendation_allowed": False,
        "promotion_allowed": False,
        "execution_allowed": False,
    }
    if not (
        isinstance(payload, Mapping)
        and set(payload) == set(expected) | {"session_started_at", "session_ended_at"}
        and all(payload.get(key) == value for key, value in expected.items())
        and all(
            isinstance(payload.get(key), str) and payload[key]
            for key in ("session_started_at", "session_ended_at")
        )
    ):
        raise SixHourContinuationError("completed trial report facts differ")


def _validate_prior_evidence(inputs: _ContinuationInputs) -> None:
    evidence_path = default_historical_evidence_path(
        inputs.preflight.tenant_id,
        resource="live-shadow-soak",
        state_root=inputs.live.state_directory,
    )
    checkpoint_path = _metadata_checkpoint_path(inputs.live, inputs.live.state_directory)
    with _deserialize_private_database(evidence_path, "completed trial evidence") as connection:
        rows = connection.execute(
            "SELECT tenant_id, resource, sequence, created_at, payload_json, checksum_sha256 "
            "FROM orion_historical_evidence ORDER BY resource, sequence"
        ).fetchall()
    if len(rows) != 1:
        raise SixHourContinuationError("completed trial evidence count differs")
    row = rows[0]
    try:
        batch = historical_evidence_from_json(row[4])
    except HistoricalEvidenceError:
        raise SixHourContinuationError("completed trial evidence is invalid") from None
    if not (
        row[:4] == (batch.tenant_id, batch.resource, batch.sequence, batch.created_at.isoformat())
        and hmac.compare_digest(row[5], historical_evidence_checksum(row[4]))
        and batch.sequence == 1
        and len(batch.observations) == PRIOR_OBSERVATIONS
    ):
        raise SixHourContinuationError("completed trial evidence envelope differs")
    validator = _AuthorizedCompanyEvidenceStore(
        object(),
        authorization=inputs.live.company_authorization,
        entities=frozenset(scope.entity for scope in inputs.live.reviewed_scopes),
    )
    try:
        validator._validate_batch(batch)
    except HistoricalEvidenceError:
        raise SixHourContinuationError("completed trial evidence crosses authorization") from None

    with _deserialize_private_database(checkpoint_path, "completed trial checkpoint") as connection:
        checkpoint_rows = connection.execute(
            "SELECT tenant_id, sequence, created_at, payload_json, checksum_sha256 "
            "FROM orion_study_checkpoints ORDER BY sequence"
        ).fetchall()
    if len(checkpoint_rows) != 1:
        raise SixHourContinuationError("completed trial checkpoint count differs")
    row = checkpoint_rows[0]
    try:
        checkpoint = checkpoint_from_json(row[3])
    except StudyCheckpointError:
        raise SixHourContinuationError("completed trial checkpoint is invalid") from None
    scopes = {scope.entity: frozenset(scope.fields) for scope in inputs.live.reviewed_scopes}
    if not (
        row[:3] == (checkpoint.tenant_id, checkpoint.sequence, checkpoint.created_at.isoformat())
        and hmac.compare_digest(row[4], checkpoint_checksum(row[3]))
        and checkpoint.tenant_id == inputs.preflight.tenant_id
        and checkpoint.sequence == 1
        and {entity.doctype for entity in checkpoint.understanding.entities} == set(scopes)
        and all(
            {field.fieldname for field in entity.fields} == scopes[entity.doctype] | {"company"}
            for entity in checkpoint.understanding.entities
        )
        and set(checkpoint.metadata_targets_studied) == set(scopes)
        and not checkpoint.sampled_records
        and not checkpoint.record_targets_sampled
    ):
        raise SixHourContinuationError("completed trial checkpoint facts differ")


class _ContinuationLedger:
    def __init__(self, inputs: _ContinuationInputs) -> None:
        self._inputs = inputs
        self._ledger = _existing_private_retry_ledger(inputs.preflight)

    def _validate_prior(self, connection: sqlite3.Connection) -> bool:
        if not _TrialLedger(self._inputs)._validate_source(connection):
            raise SixHourContinuationError("completed trial accounting is unavailable")
        row = connection.execute(
            "SELECT status, candidate_digest, binding, prior_metadata_requests, "
            "metadata_requests, study_requests, stop_reason "
            "FROM bounded_readonly_trial WHERE singleton=1"
        ).fetchone()
        if row != (
            "complete",
            self._inputs.candidate_digest,
            _admin_binding_digest(self._inputs.preflight),
            42,
            TRIAL_METADATA_REQUESTS,
            TRIAL_STUDY_REQUESTS,
            "cycle_limit",
        ):
            raise SixHourContinuationError("exact completed trial claim is required")
        present = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='six_hour_continuation'"
        ).fetchone()
        return present is not None

    def inspect(self) -> str:
        _validate_trial_report(self._inputs)
        _validate_prior_evidence(self._inputs)
        with self._ledger._connect() as connection:
            return "already_claimed" if self._validate_prior(connection) else "ready"

    def claim(self) -> None:
        _validate_trial_report(self._inputs)
        _validate_prior_evidence(self._inputs)
        connection = self._ledger._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if self._validate_prior(connection):
                raise SixHourContinuationError("six-hour continuation was already claimed")
            connection.execute(_CONTINUATION_SCHEMA)
            connection.execute(
                "INSERT INTO six_hour_continuation VALUES "
                "(1, 'running', ?, ?, ?, 49, 1, 1, 1, 0, 0, 'none')",
                (
                    self._inputs.candidate_digest,
                    self._inputs.trial_report_digest,
                    _admin_binding_digest(self._inputs.preflight),
                ),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _reserve(self, kind: str) -> None:
        connection = self._ledger._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, metadata_requests, study_requests "
                "FROM six_hour_continuation WHERE singleton=1"
            ).fetchone()
            if row is None or row[0] != "running":
                raise SixHourContinuationError("six-hour continuation is not running")
            metadata, study = int(row[1]), int(row[2])
            if kind == "metadata":
                if metadata >= CONTINUATION_METADATA_REQUESTS or study:
                    raise SixHourContinuationError("continuation metadata budget is exhausted")
                metadata += 1
            elif kind == "study":
                if metadata != CONTINUATION_METADATA_REQUESTS or study >= CONTINUATION_STUDY_REQUESTS:
                    raise SixHourContinuationError("continuation study budget is unavailable")
                study += 1
            else:
                raise SixHourContinuationError("continuation request kind is invalid")
            if PRIOR_METADATA_REQUESTS + metadata > METADATA_ALLOWANCE_MAX:
                raise SixHourContinuationError("metadata allowance is exhausted")
            connection.execute(
                "UPDATE six_hour_continuation SET metadata_requests=?, study_requests=? "
                "WHERE singleton=1",
                (metadata, study),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reserve_metadata(self) -> None:
        self._reserve("metadata")

    def reserve_study(self) -> None:
        self._reserve("study")

    def snapshot(self) -> tuple[str, int, int, str]:
        with self._ledger._connect() as connection:
            row = connection.execute(
                "SELECT status, metadata_requests, study_requests, stop_reason "
                "FROM six_hour_continuation WHERE singleton=1"
            ).fetchone()
        if row is None or row[0] not in {"running", "complete", "failed", "interrupted"}:
            raise SixHourContinuationError("continuation accounting is invalid")
        metadata, study = int(row[1]), int(row[2])
        if not (
            0 <= metadata <= CONTINUATION_METADATA_REQUESTS
            and 0 <= study <= CONTINUATION_STUDY_REQUESTS
        ):
            raise SixHourContinuationError("continuation accounting exceeded its budget")
        return str(row[0]), metadata, study, str(row[3])

    def finish(self, status: str, stop_reason: str) -> None:
        if status not in {"complete", "failed", "interrupted"}:
            raise SixHourContinuationError("continuation final status is invalid")
        connection = self._ledger._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE six_hour_continuation SET status=?, stop_reason=? "
                "WHERE singleton=1 AND status='running'",
                (status, stop_reason),
            )
            if cursor.rowcount != 1:
                raise SixHourContinuationError("continuation finalization failed")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def inspect_six_hour_continuation_readiness(
    environment: Mapping[str, str],
    candidate_path: Path,
    candidate_sha256: str,
    trial_report_path: Path,
    trial_report_sha256: str,
) -> SixHourContinuationReadinessReport:
    credentials_available = False
    try:
        inputs = _continuation_inputs(
            environment, candidate_path, candidate_sha256, trial_report_path, trial_report_sha256
        )
        status = _ContinuationLedger(inputs).inspect()
        if not (
            _destination_ready(inputs.live.state_directory, private=True)
            and _destination_ready(inputs.live.report_directory, private=True)
        ):
            status = "invalid"
        try:
            inputs.live.credential_references.resolve(environment)
        except (LiveSessionError, TypeError):
            pass
        else:
            credentials_available = True
    except Exception:  # noqa: BLE001 - fixed aggregate readiness only
        status = "invalid"
    return SixHourContinuationReadinessReport(
        execution_allowed=False,
        offline_inputs_ready=status == "ready",
        credentials_available=credentials_available,
        ready_for_authorized_launch=status == "ready" and credentials_available,
        status=status,
    )


def _result(report: LiveSessionRunReport, ledger: _ContinuationLedger) -> SixHourContinuationRunReport:
    _, metadata, study, _ = ledger.snapshot()
    if metadata != report.metadata_gets or study != report.study_gets:
        ledger.finish("failed", "accounting_mismatch")
        raise SixHourContinuationError("continuation request accounting mismatch")
    failures = {
        "metadata_preflight_failure",
        "persistence_failure",
        "tenant_scope_mismatch",
        "erp_contract_failure",
    }
    status = (
        "interrupted"
        if report.stop_reason == "user_termination"
        else "failed"
        if report.stop_reason in failures
        else "complete"
    )
    ledger.finish(status, report.stop_reason)
    return SixHourContinuationRunReport(
        execution_allowed=False,
        status=status,
        stop_reason=report.stop_reason,
        prior_metadata_requests=PRIOR_METADATA_REQUESTS,
        additional_metadata_get_budget=CONTINUATION_METADATA_REQUESTS,
        additional_metadata_gets=metadata,
        metadata_cumulative_requests=PRIOR_METADATA_REQUESTS + metadata,
        metadata_allowance_max=METADATA_ALLOWANCE_MAX,
        prior_study_requests=PRIOR_STUDY_REQUESTS,
        additional_study_get_budget=CONTINUATION_STUDY_REQUESTS,
        additional_study_gets=study,
        study_cumulative_requests=PRIOR_STUDY_REQUESTS + study,
        additional_combined_get_max=CONTINUATION_COMBINED_REQUESTS,
        additional_combined_gets=metadata + study,
        cumulative_combined_gets=PRIOR_METADATA_REQUESTS + metadata + PRIOR_STUDY_REQUESTS + study,
        max_wall_clock_seconds=CONTINUATION_SECONDS,
        prior_cycles=PRIOR_CYCLES,
        additional_cycle_budget=CONTINUATION_CYCLES,
        cycle_cumulative_max=PRIOR_CYCLES + CONTINUATION_CYCLES,
        cycles_attempted=report.cycles_attempted,
        cycles_completed=report.cycles_completed,
        prior_observations=PRIOR_OBSERVATIONS,
        additional_observation_budget=CONTINUATION_OBSERVATIONS,
        observation_cumulative_max=PRIOR_OBSERVATIONS + CONTINUATION_OBSERVATIONS,
        effective_additional_observation_max=EFFECTIVE_ADDITIONAL_OBSERVATIONS,
        effective_cumulative_observation_max=(
            PRIOR_OBSERVATIONS + EFFECTIVE_ADDITIONAL_OBSERVATIONS
        ),
        observations_persisted=report.observations_persisted,
        company_scope_count=report.company_scope_count,
        reviewed_entity_count=report.reviewed_entity_count,
        reviewed_field_count=report.reviewed_field_count,
        erp_writes=report.erp_writes,
        recommendation_allowed=report.recommendation_allowed,
        promotion_allowed=report.promotion_allowed,
    )


def run_six_hour_continuation(
    environment: Mapping[str, str],
    candidate_path: Path,
    candidate_sha256: str,
    trial_report_path: Path,
    trial_report_sha256: str,
    *,
    metadata_opener: Callable[..., Any] | None = None,
    record_opener: Callable[..., Any] | None = None,
    clock: Callable[..., Any] | None = None,
    monotonic: Callable[[], float] | None = None,
    termination_requested: Callable[[], bool] | None = None,
) -> SixHourContinuationRunReport:
    inputs = _continuation_inputs(
        environment, candidate_path, candidate_sha256, trial_report_path, trial_report_sha256
    )
    inputs.live.credential_references.resolve(environment)
    ledger = _ContinuationLedger(inputs)
    ledger.claim()
    metadata_transport = metadata_opener or _default_opener
    record_transport = record_opener or _default_opener

    def charged_metadata(request: Any, *, timeout: int) -> Any:
        ledger.reserve_metadata()
        return metadata_transport(request, timeout=timeout)

    def charged_record(request: Any, *, timeout: int) -> Any:
        ledger.reserve_study()
        return record_transport(request, timeout=timeout)

    kwargs: dict[str, Any] = {
        "environment": environment,
        "metadata_opener": charged_metadata,
        "record_opener": charged_record,
        "termination_requested": termination_requested,
    }
    if clock is not None:
        kwargs["clock"] = clock
    if monotonic is not None:
        kwargs["monotonic"] = monotonic
    try:
        report = run_erpnext_live_session(inputs.live, **kwargs)
    except (KeyboardInterrupt, SystemExit):
        ledger.finish("interrupted", "user_termination")
        raise
    except Exception:
        ledger.finish("failed", "internal_failure")
        raise
    return _result(report, ledger)


def continuation_report_json(
    report: SixHourContinuationReadinessReport | SixHourContinuationRunReport,
) -> str:
    return json.dumps(asdict(report), sort_keys=True, separators=(",", ":"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check or explicitly execute the residual six-hour continuation",
        allow_abbrev=False,
    )
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--trial-report", required=True, type=Path)
    parser.add_argument("--trial-report-sha256", required=True)
    parser.add_argument("--execute-six-hour-continuation", action="store_true")
    args = parser.parse_args(argv)
    values = (
        os.environ,
        args.candidate,
        args.candidate_sha256,
        args.trial_report,
        args.trial_report_sha256,
    )
    if not args.execute_six_hour_continuation:
        report = inspect_six_hour_continuation_readiness(*values)
        print(continuation_report_json(report))
        return 0 if report.offline_inputs_ready else 2
    try:
        with _live_session_termination_signals() as termination:
            report = run_six_hour_continuation(
                *values,
                termination_requested=termination,
            )
    except Exception:  # noqa: BLE001 - CLI emits only a fixed safe category
        print('{"execution_allowed":false,"status":"continuation_refused"}')
        return 2
    print(continuation_report_json(report))
    if report.status == "interrupted":
        return 130
    return 0 if report.status == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "SixHourContinuationReadinessReport",
    "SixHourContinuationRunReport",
    "continuation_report_json",
    "inspect_six_hour_continuation_readiness",
    "main",
    "run_six_hour_continuation",
]
