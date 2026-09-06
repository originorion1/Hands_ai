"""One-use launcher for the smallest reviewed ORION read-only trial.

The launcher composes the existing live-session runner with fixed limits.  It
does not create another metadata allowance: each revalidation attempt is
charged in a separate one-use table beside the completed 42/100 preflight
history, whose original rows and artifacts remain immutable.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from ..learning.autonomous_loop import LearningObjective
from .erpnext_adapter import _default_opener
from .erpnext_live_session import (
    ERPNextCompanyAuthorization,
    ERPNextLiveSessionConfig,
    LiveSessionError,
    LiveSessionLimits,
    LiveSessionRunReport,
    ReviewedMetadataScope,
    _destination_ready,
    _live_session_termination_signals,
    run_erpnext_live_session,
)
from .erpnext_metadata_preflight import (
    ERPNextMetadataPreflightConfig,
    MetadataPreflightError,
    _admin_binding_digest,
    _existing_private_retry_ledger,
    _unique_json_object,
    metadata_preflight_config_from_environment,
)
from .erpnext_metadata_refresh import (
    _read_private_bytes,
    _refresh_candidate_path,
)

PRIOR_METADATA_REQUESTS = 42
TRIAL_METADATA_REQUESTS = 7
TRIAL_METADATA_CUMULATIVE_MAX = PRIOR_METADATA_REQUESTS + TRIAL_METADATA_REQUESTS
TRIAL_STUDY_REQUESTS = 1
TRIAL_COMBINED_REQUESTS = TRIAL_METADATA_REQUESTS + TRIAL_STUDY_REQUESTS
TRIAL_SECONDS = 5 * 60
TRIAL_CYCLES = 1
TRIAL_OBSERVATIONS = 1
TRIAL_ENTITY_COUNT = 7
TRIAL_FIELD_COUNT = 133

_TRIAL_SCHEMA = """
CREATE TABLE bounded_readonly_trial (
    singleton INTEGER PRIMARY KEY CHECK (singleton = 1),
    status TEXT NOT NULL,
    candidate_digest TEXT NOT NULL,
    binding TEXT NOT NULL,
    prior_metadata_requests INTEGER NOT NULL CHECK (prior_metadata_requests = 42),
    metadata_requests INTEGER NOT NULL CHECK (metadata_requests BETWEEN 0 AND 7),
    study_requests INTEGER NOT NULL CHECK (study_requests BETWEEN 0 AND 1),
    stop_reason TEXT NOT NULL
)
"""


class BoundedTrialError(MetadataPreflightError):
    """Raised before transport when the reviewed trial contract is not exact."""


@dataclass(frozen=True, slots=True)
class BoundedTrialReadinessReport:
    execution_allowed: bool
    offline_inputs_ready: bool
    credentials_available: bool
    ready_for_authorized_launch: bool
    status: str
    company_scope_count: int
    reviewed_entity_count: int
    reviewed_field_count: int
    prior_metadata_requests: int
    metadata_request_budget: int
    metadata_cumulative_max: int
    metadata_allowance_max: int
    study_request_budget: int
    combined_request_max: int
    max_wall_clock_seconds: int
    max_study_cycles: int
    max_observations: int
    live_requests_performed: int = 0
    erp_writes: int = 0
    recommendation_allowed: bool = False
    promotion_allowed: bool = False

    def __post_init__(self) -> None:
        if self.status not in {"ready", "already_claimed", "invalid"}:
            raise BoundedTrialError("bounded trial readiness status is invalid")
        if self.offline_inputs_ready != (self.status == "ready"):
            raise BoundedTrialError("bounded trial offline readiness is inconsistent")
        if self.ready_for_authorized_launch != (
            self.offline_inputs_ready and self.credentials_available
        ):
            raise BoundedTrialError("bounded trial launch readiness is inconsistent")
        if any(
            value is not False
            for value in (
                self.execution_allowed,
                self.recommendation_allowed,
                self.promotion_allowed,
            )
        ) or self.erp_writes != 0:
            raise BoundedTrialError("bounded trial readiness cannot grant authority")
        if self.live_requests_performed != 0:
            raise BoundedTrialError("bounded trial readiness cannot claim live requests")
        expected = (
            self.company_scope_count,
            self.reviewed_entity_count,
            self.reviewed_field_count,
            self.prior_metadata_requests,
            self.metadata_request_budget,
            self.metadata_cumulative_max,
            self.metadata_allowance_max,
            self.study_request_budget,
            self.combined_request_max,
            self.max_wall_clock_seconds,
            self.max_study_cycles,
            self.max_observations,
        )
        if expected != (
            1,
            TRIAL_ENTITY_COUNT,
            TRIAL_FIELD_COUNT,
            PRIOR_METADATA_REQUESTS,
            TRIAL_METADATA_REQUESTS,
            TRIAL_METADATA_CUMULATIVE_MAX,
            100,
            TRIAL_STUDY_REQUESTS,
            TRIAL_COMBINED_REQUESTS,
            TRIAL_SECONDS,
            TRIAL_CYCLES,
            TRIAL_OBSERVATIONS,
        ):
            raise BoundedTrialError("bounded trial readiness limits are invalid")


@dataclass(frozen=True, slots=True)
class BoundedTrialRunReport:
    execution_allowed: bool
    status: str
    stop_reason: str
    prior_metadata_requests: int
    metadata_request_budget: int
    metadata_requests: int
    metadata_cumulative_requests: int
    metadata_allowance_max: int
    study_request_budget: int
    study_requests: int
    combined_request_max: int
    combined_requests: int
    max_wall_clock_seconds: int
    max_study_cycles: int
    cycles_attempted: int
    cycles_completed: int
    max_observations: int
    observations_persisted: int
    company_scope_count: int
    reviewed_entity_count: int
    reviewed_field_count: int
    erp_writes: int
    recommendation_allowed: bool
    promotion_allowed: bool

    def __post_init__(self) -> None:
        if self.status not in {"complete", "failed", "interrupted"}:
            raise BoundedTrialError("bounded trial result status is invalid")
        if any(
            value is not False
            for value in (
                self.execution_allowed,
                self.recommendation_allowed,
                self.promotion_allowed,
            )
        ) or self.erp_writes != 0:
            raise BoundedTrialError("bounded trial result cannot grant authority")
        if (
            self.prior_metadata_requests != PRIOR_METADATA_REQUESTS
            or self.metadata_request_budget != TRIAL_METADATA_REQUESTS
            or self.metadata_allowance_max != 100
            or self.study_request_budget != TRIAL_STUDY_REQUESTS
            or self.combined_request_max != TRIAL_COMBINED_REQUESTS
            or self.max_wall_clock_seconds != TRIAL_SECONDS
            or self.max_study_cycles != TRIAL_CYCLES
            or self.max_observations != TRIAL_OBSERVATIONS
            or self.company_scope_count != 1
            or self.reviewed_entity_count != TRIAL_ENTITY_COUNT
            or self.reviewed_field_count != TRIAL_FIELD_COUNT
        ):
            raise BoundedTrialError("bounded trial result limits are invalid")
        counts = (
            self.metadata_requests,
            self.metadata_cumulative_requests,
            self.study_requests,
            self.combined_requests,
            self.cycles_attempted,
            self.cycles_completed,
            self.observations_persisted,
        )
        if any(type(value) is not int or value < 0 for value in counts):
            raise BoundedTrialError("bounded trial result counts are invalid")
        if (
            self.metadata_requests > TRIAL_METADATA_REQUESTS
            or self.study_requests > TRIAL_STUDY_REQUESTS
            or self.metadata_cumulative_requests
            != PRIOR_METADATA_REQUESTS + self.metadata_requests
            or self.combined_requests != self.metadata_requests + self.study_requests
            or self.combined_requests > TRIAL_COMBINED_REQUESTS
            or self.cycles_attempted > TRIAL_CYCLES
            or self.cycles_completed > self.cycles_attempted
            or self.observations_persisted > TRIAL_OBSERVATIONS
        ):
            raise BoundedTrialError("bounded trial result accounting is invalid")
        success = (
            self.metadata_requests == TRIAL_METADATA_REQUESTS
            and self.study_requests == TRIAL_STUDY_REQUESTS
            and self.cycles_completed == TRIAL_CYCLES
            and self.observations_persisted == TRIAL_OBSERVATIONS
        )
        if (self.status == "complete") != success:
            raise BoundedTrialError("bounded trial completion status is inconsistent")


@dataclass(frozen=True, slots=True)
class _TrialInputs:
    preflight: ERPNextMetadataPreflightConfig
    live: ERPNextLiveSessionConfig
    candidate_path: Path
    candidate_digest: str


def _load_candidate(
    config: ERPNextMetadataPreflightConfig,
    path: Path,
    expected_sha256: str,
) -> tuple[ERPNextCompanyAuthorization, tuple[ReviewedMetadataScope, ...], str]:
    if path != _refresh_candidate_path(config):
        raise BoundedTrialError("bounded trial requires the exact refresh candidate path")
    if re.fullmatch(r"[0-9a-f]{64}", expected_sha256) is None:
        raise BoundedTrialError("bounded trial candidate digest is invalid")
    body = _read_private_bytes(path, "bounded trial candidate")
    digest = hashlib.sha256(body).hexdigest()
    if digest != expected_sha256:
        raise BoundedTrialError("bounded trial candidate differs from reviewed bytes")
    try:
        payload = json.loads(body.decode("utf-8"), object_pairs_hook=_unique_json_object)
    except (UnicodeDecodeError, json.JSONDecodeError, MetadataPreflightError) as exc:
        raise BoundedTrialError("bounded trial candidate is invalid") from exc
    expected = {
        "candidate_scopes",
        "companies",
        "company_catalog_complete",
        "doctype_catalog_complete",
        "review_required",
        "schema_version",
    }
    if not isinstance(payload, Mapping) or set(payload) != expected:
        raise BoundedTrialError("bounded trial candidate schema is invalid")
    companies = payload["companies"]
    raw_scopes = payload["candidate_scopes"]
    if not (
        isinstance(companies, list)
        and len(companies) == 1
        and isinstance(companies[0], str)
        and isinstance(raw_scopes, Mapping)
        and len(raw_scopes) == TRIAL_ENTITY_COUNT
        and payload["company_catalog_complete"] is True
        and payload["doctype_catalog_complete"] is False
        and payload["review_required"] is True
        and payload["schema_version"] == 1
    ):
        raise BoundedTrialError("bounded trial candidate contract is invalid")
    try:
        scopes = tuple(
            ReviewedMetadataScope(entity, tuple(fields))
            for entity, fields in sorted(raw_scopes.items())
            if isinstance(entity, str) and isinstance(fields, list)
        )
        authorization = ERPNextCompanyAuthorization(config.tenant_id, tuple(companies))
    except (LiveSessionError, TypeError, ValueError) as exc:
        raise BoundedTrialError("bounded trial candidate scope is invalid") from exc
    if (
        len(scopes) != TRIAL_ENTITY_COUNT
        or sum(len(scope.fields) for scope in scopes) != TRIAL_FIELD_COUNT
    ):
        raise BoundedTrialError("bounded trial candidate counts are invalid")
    return authorization, scopes, digest


def _trial_inputs(
    environment: Mapping[str, str],
    candidate_path: Path,
    candidate_sha256: str,
) -> _TrialInputs:
    preflight = metadata_preflight_config_from_environment(environment)
    authorization, scopes, digest = _load_candidate(
        preflight, candidate_path, candidate_sha256
    )
    objective_id = environment.get("ORION_LIVE_OBJECTIVE_ID")
    objective_description = environment.get("ORION_LIVE_OBJECTIVE_DESCRIPTION")
    if not isinstance(objective_id, str) or not objective_id:
        raise BoundedTrialError("bounded trial objective is unavailable")
    if not isinstance(objective_description, str) or not objective_description:
        raise BoundedTrialError("bounded trial objective is unavailable")
    live = ERPNextLiveSessionConfig(
        base_url=preflight.base_url,
        tenant_id=preflight.tenant_id,
        authorization_reference=preflight.authorization_reference,
        company_authorization=authorization,
        reviewed_scopes=scopes,
        credential_references=preflight.credential_references,
        state_directory=preflight.state_directory,
        report_directory=preflight.report_directory,
        objective=LearningObjective(
            objective_id,
            objective_description,
            prohibited_effects=("ERP_WRITE", "RECOMMENDATION", "PROMOTION", "EXECUTION"),
        ),
        limits=LiveSessionLimits(
            max_metadata_gets=TRIAL_METADATA_REQUESTS,
            max_wall_clock_seconds=TRIAL_SECONDS,
            max_study_cycles=TRIAL_CYCLES,
            max_study_gets=TRIAL_STUDY_REQUESTS,
            max_observations_per_study=TRIAL_OBSERVATIONS,
            max_cumulative_observations=TRIAL_OBSERVATIONS,
            max_consecutive_non_progress=1,
        ),
    )
    return _TrialInputs(preflight, live, candidate_path, digest)


class _TrialLedger:
    def __init__(self, inputs: _TrialInputs) -> None:
        self._inputs = inputs
        self._ledger = _existing_private_retry_ledger(inputs.preflight)

    def _validate_source(self, connection: Any) -> bool:
        if self._ledger.accounting_base() != 6 or self._ledger.snapshot() != {
            "status": "complete",
            "attempted_gets": 24,
            "company_count": 1,
            "doctype_catalog_count": 18,
            "metadata_succeeded": 18,
            "metadata_failed": 0,
            "candidate_entity_count": 0,
            "candidate_field_count": 0,
            "company_catalog_complete": 1,
            "doctype_catalog_complete": 0,
        }:
            raise BoundedTrialError("bounded trial requires the exact completed preflight")
        refresh = connection.execute(
            "SELECT status, catalog_digest, binding, attempted_gets, metadata_succeeded, "
            "metadata_failed, sensitive_excluded, candidate_entity_count, "
            "candidate_field_count FROM metadata_filter_refresh WHERE singleton=1"
        ).fetchone()
        admin = connection.execute(
            "SELECT digest, binding FROM admin_catalog_claim"
        ).fetchall()
        resume = connection.execute(
            "SELECT catalog_digest, binding FROM admin_catalog_resume_claim"
        ).fetchall()
        binding = _admin_binding_digest(self._inputs.preflight)
        if not (
            refresh is not None
            and refresh[0] == "complete"
            and re.fullmatch(r"[0-9a-f]{64}", refresh[1]) is not None
            and refresh[2:] == (binding, 18, 18, 0, 0, 7, 133)
            and admin == [(refresh[1], binding)]
            and resume == [(refresh[1], binding)]
        ):
            raise BoundedTrialError("bounded trial requires the exact completed refresh")
        outcomes = connection.execute(
            "SELECT status, category, COUNT(*) FROM metadata_filter_refresh_targets "
            "GROUP BY status, category ORDER BY status, category"
        ).fetchall()
        if outcomes != [("candidate", "none", 7), ("no_candidate", "scope_incompatible", 11)]:
            raise BoundedTrialError("bounded trial refresh outcomes are invalid")
        present = connection.execute(
            "SELECT name FROM sqlite_master WHERE type='table' "
            "AND name='bounded_readonly_trial'"
        ).fetchone()
        return present is not None

    def inspect(self) -> str:
        with self._ledger._connect() as connection:
            return "already_claimed" if self._validate_source(connection) else "ready"

    def claim(self) -> None:
        if hashlib.sha256(
            _read_private_bytes(self._inputs.candidate_path, "bounded trial candidate")
        ).hexdigest() != self._inputs.candidate_digest:
            raise BoundedTrialError("bounded trial candidate changed before claim")
        connection = self._ledger._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if self._validate_source(connection):
                raise BoundedTrialError("bounded trial was already claimed")
            connection.execute(_TRIAL_SCHEMA)
            connection.execute(
                "INSERT INTO bounded_readonly_trial VALUES "
                "(1, 'running', ?, ?, 42, 0, 0, 'none')",
                (
                    self._inputs.candidate_digest,
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
        if kind not in {"metadata", "study"}:
            raise BoundedTrialError("bounded trial request kind is invalid")
        connection = self._ledger._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                "SELECT status, metadata_requests, study_requests "
                "FROM bounded_readonly_trial WHERE singleton=1"
            ).fetchone()
            if row is None or row[0] != "running":
                raise BoundedTrialError("bounded trial is not running")
            metadata, study = int(row[1]), int(row[2])
            if kind == "metadata":
                if metadata >= TRIAL_METADATA_REQUESTS or study != 0:
                    raise BoundedTrialError("bounded trial metadata budget is exhausted")
                metadata += 1
            else:
                if metadata != TRIAL_METADATA_REQUESTS or study >= TRIAL_STUDY_REQUESTS:
                    raise BoundedTrialError("bounded trial study budget is unavailable")
                study += 1
            if PRIOR_METADATA_REQUESTS + metadata > 100:
                raise BoundedTrialError("bounded trial metadata allowance is exhausted")
            connection.execute(
                "UPDATE bounded_readonly_trial SET metadata_requests=?, study_requests=? "
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
                "FROM bounded_readonly_trial WHERE singleton=1"
            ).fetchone()
        if row is None or row[0] not in {"running", "complete", "failed", "interrupted"}:
            raise BoundedTrialError("bounded trial accounting is invalid")
        metadata, study = int(row[1]), int(row[2])
        if not 0 <= metadata <= 7 or not 0 <= study <= 1:
            raise BoundedTrialError("bounded trial accounting exceeded its budget")
        return str(row[0]), metadata, study, str(row[3])

    def finish(self, status: str, stop_reason: str) -> None:
        if status not in {"complete", "failed", "interrupted"}:
            raise BoundedTrialError("bounded trial final status is invalid")
        connection = self._ledger._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            cursor = connection.execute(
                "UPDATE bounded_readonly_trial SET status=?, stop_reason=? "
                "WHERE singleton=1 AND status='running'",
                (status, stop_reason),
            )
            if cursor.rowcount != 1:
                raise BoundedTrialError("bounded trial finalization failed")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()


def inspect_bounded_trial_readiness(
    environment: Mapping[str, str],
    candidate_path: Path,
    candidate_sha256: str,
) -> BoundedTrialReadinessReport:
    credentials_available = False
    try:
        inputs = _trial_inputs(environment, candidate_path, candidate_sha256)
        status = _TrialLedger(inputs).inspect()
        state_ready = _destination_ready(inputs.live.state_directory, private=True)
        report_ready = _destination_ready(inputs.live.report_directory, private=True)
        try:
            inputs.live.credential_references.resolve(environment)
        except (LiveSessionError, TypeError):
            pass
        else:
            credentials_available = True
        if not state_ready or not report_ready:
            status = "invalid"
    except Exception:  # noqa: BLE001 - readiness exposes fixed aggregate state only
        status = "invalid"
    return BoundedTrialReadinessReport(
        execution_allowed=False,
        offline_inputs_ready=status == "ready",
        credentials_available=credentials_available,
        ready_for_authorized_launch=status == "ready" and credentials_available,
        status=status,
        company_scope_count=1,
        reviewed_entity_count=TRIAL_ENTITY_COUNT,
        reviewed_field_count=TRIAL_FIELD_COUNT,
        prior_metadata_requests=PRIOR_METADATA_REQUESTS,
        metadata_request_budget=TRIAL_METADATA_REQUESTS,
        metadata_cumulative_max=TRIAL_METADATA_CUMULATIVE_MAX,
        metadata_allowance_max=100,
        study_request_budget=TRIAL_STUDY_REQUESTS,
        combined_request_max=TRIAL_COMBINED_REQUESTS,
        max_wall_clock_seconds=TRIAL_SECONDS,
        max_study_cycles=TRIAL_CYCLES,
        max_observations=TRIAL_OBSERVATIONS,
    )


def _result(report: LiveSessionRunReport, ledger: _TrialLedger) -> BoundedTrialRunReport:
    _, metadata, study, _ = ledger.snapshot()
    if metadata != report.metadata_gets or study != report.study_gets:
        ledger.finish("failed", "accounting_mismatch")
        raise BoundedTrialError("bounded trial request accounting mismatch")
    complete = (
        metadata == TRIAL_METADATA_REQUESTS
        and study == TRIAL_STUDY_REQUESTS
        and report.cycles_completed == TRIAL_CYCLES
        and report.observations_persisted == TRIAL_OBSERVATIONS
    )
    status = (
        "complete"
        if complete
        else "interrupted"
        if report.stop_reason == "user_termination"
        else "failed"
    )
    ledger.finish(status, report.stop_reason)
    return BoundedTrialRunReport(
        execution_allowed=False,
        status=status,
        stop_reason=report.stop_reason,
        prior_metadata_requests=PRIOR_METADATA_REQUESTS,
        metadata_request_budget=TRIAL_METADATA_REQUESTS,
        metadata_requests=metadata,
        metadata_cumulative_requests=PRIOR_METADATA_REQUESTS + metadata,
        metadata_allowance_max=100,
        study_request_budget=TRIAL_STUDY_REQUESTS,
        study_requests=study,
        combined_request_max=TRIAL_COMBINED_REQUESTS,
        combined_requests=metadata + study,
        max_wall_clock_seconds=TRIAL_SECONDS,
        max_study_cycles=TRIAL_CYCLES,
        cycles_attempted=report.cycles_attempted,
        cycles_completed=report.cycles_completed,
        max_observations=TRIAL_OBSERVATIONS,
        observations_persisted=report.observations_persisted,
        company_scope_count=report.company_scope_count,
        reviewed_entity_count=report.reviewed_entity_count,
        reviewed_field_count=report.reviewed_field_count,
        erp_writes=report.erp_writes,
        recommendation_allowed=report.recommendation_allowed,
        promotion_allowed=report.promotion_allowed,
    )


def run_bounded_trial(
    environment: Mapping[str, str],
    candidate_path: Path,
    candidate_sha256: str,
    *,
    metadata_opener: Callable[..., Any] | None = None,
    record_opener: Callable[..., Any] | None = None,
    clock: Callable[..., Any] | None = None,
    monotonic: Callable[[], float] | None = None,
    termination_requested: Callable[[], bool] | None = None,
) -> BoundedTrialRunReport:
    inputs = _trial_inputs(environment, candidate_path, candidate_sha256)
    inputs.live.credential_references.resolve(environment)
    ledger = _TrialLedger(inputs)
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


def bounded_trial_report_json(
    report: BoundedTrialReadinessReport | BoundedTrialRunReport,
) -> str:
    return json.dumps(asdict(report), sort_keys=True, separators=(",", ":"))


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check or explicitly execute one bounded read-only ORION trial",
        allow_abbrev=False,
    )
    parser.add_argument("--candidate", required=True, type=Path)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--execute-bounded-trial", action="store_true")
    args = parser.parse_args(argv)
    if not args.execute_bounded_trial:
        report = inspect_bounded_trial_readiness(
            os.environ, args.candidate, args.candidate_sha256
        )
        print(bounded_trial_report_json(report))
        return 0 if report.offline_inputs_ready else 2
    try:
        with _live_session_termination_signals() as termination:
            report = run_bounded_trial(
                os.environ,
                args.candidate,
                args.candidate_sha256,
                termination_requested=termination,
            )
    except Exception:  # noqa: BLE001 - CLI exposes a fixed safe category only
        print('{"execution_allowed":false,"status":"trial_refused"}')
        return 2
    print(bounded_trial_report_json(report))
    if report.status == "interrupted":
        return 130
    return 0 if report.status == "complete" else 2


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "BoundedTrialReadinessReport",
    "BoundedTrialRunReport",
    "bounded_trial_report_json",
    "inspect_bounded_trial_readiness",
    "main",
    "run_bounded_trial",
]
