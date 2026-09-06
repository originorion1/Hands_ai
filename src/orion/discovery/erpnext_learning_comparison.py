"""Fresh, single-use allowance for an aggregate corrected-selector comparison.

All entry points default to offline work. Historical authorization and counters
remain distinct from the explicit new allowance; evidence stays in its existing
append-only stores. Reports contain counts and anonymous entity indexes only.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import time
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from ..discovery.checkpoint import checkpoint_checksum, checkpoint_from_json
from ..history.evidence import historical_evidence_checksum, historical_evidence_from_json
from ..learning.offline_proposal import project_historical_coverage
from ..learning.shadow_soak import ShadowSoakStopReason, _PersistenceFailure, _StopBeforeRead
from .erpnext_adapter import _default_opener
from .erpnext_bounded_trial import _trial_inputs, _TrialLedger
from .erpnext_historical_capture import default_historical_evidence_path
from .erpnext_live_session import (
    LiveSessionLimits,
    LiveSessionRunReport,
    _AuthorizedCompanyEvidenceStore,
    _destination_ready,
    _live_session_termination_signals,
    _metadata_checkpoint_path,
    _PreflightDuration,
    _PreflightTermination,
    _read_monotonic,
    run_erpnext_live_session,
)
from .erpnext_metadata_preflight import (
    MetadataPreflightError,
    _admin_binding_digest,
    _existing_private_retry_ledger,
    _unique_json_object,
    _write_private_json,
)
from .erpnext_metadata_refresh import _read_private_bytes
from .erpnext_six_hour_continuation import (
    _continuation_inputs,
    _deserialize_private_database,
    _validate_trial_report,
)

METADATA_GETS = 7
STUDY_GETS = CYCLES = 20
OBSERVATIONS = 100
SECONDS = 900
PRIOR_METADATA_GETS = 56
PRIOR_STUDY_GETS = PRIOR_CYCLES = 100
PRIOR_OBSERVATIONS = 304
_TABLE = "corrected_selector_comparison"
_SCHEMA = f"""
CREATE TABLE {_TABLE} (
 singleton INTEGER PRIMARY KEY CHECK(singleton=1),
 status TEXT NOT NULL,
 authorization_digest TEXT NOT NULL,
 source_commit TEXT NOT NULL,
 manifest_digest TEXT NOT NULL,
 prior_metadata_gets INTEGER NOT NULL CHECK(prior_metadata_gets=56),
 prior_study_gets INTEGER NOT NULL CHECK(prior_study_gets=100),
 metadata_gets INTEGER NOT NULL CHECK(metadata_gets BETWEEN 0 AND 7),
 study_gets INTEGER NOT NULL CHECK(study_gets BETWEEN 0 AND 20),
 stop_reason TEXT NOT NULL
)
"""


class LearningComparisonError(MetadataPreflightError):
    """A comparison binding, baseline or accounting invariant failed closed."""


def _digest(body: bytes) -> str:
    return hashlib.sha256(body).hexdigest()


def _json_digest(value: Any) -> str:
    return _digest(json.dumps(value, sort_keys=True, separators=(",", ":")).encode())


def _private_json(path: Path) -> Any:
    try:
        return json.loads(
            _read_private_bytes(path, "comparison input"),
            object_pairs_hook=_unique_json_object,
        )
    except (ValueError, TypeError) as exc:
        raise LearningComparisonError("comparison input JSON is invalid") from exc


@dataclass(frozen=True)
class ComparisonInputs:
    environment: Mapping[str, str]
    candidate_path: Path
    candidate_sha256: str
    continuation_report_path: Path
    continuation_report_sha256: str
    source_commit: str
    authorization_reference: str

    def trial(self):
        return _trial_inputs(self.environment, self.candidate_path, self.candidate_sha256)

    def live(self):
        trial = self.trial()
        if (
            re.fullmatch(r"[0-9a-f]{40}", self.source_commit) is None
            or not isinstance(self.authorization_reference, str)
            or not self.authorization_reference.strip()
            or self.authorization_reference == trial.live.authorization_reference
        ):
            raise LearningComparisonError("a fresh comparison authorization and source are required")
        return replace(
            trial.live,
            authorization_reference=self.authorization_reference,
            limits=LiveSessionLimits(
                max_metadata_gets=METADATA_GETS,
                max_study_gets=STUDY_GETS,
                max_study_cycles=CYCLES,
                max_observations_per_study=5,
                max_cumulative_observations=OBSERVATIONS,
                max_wall_clock_seconds=SECONDS,
                max_consecutive_non_progress=5,
            ),
        )


def _paths(inputs: ComparisonInputs) -> tuple[Path, Path]:
    live = inputs.live()
    return (
        default_historical_evidence_path(
            live.tenant_id, resource="live-shadow-soak", state_root=live.state_directory
        ),
        _metadata_checkpoint_path(live, live.state_directory),
    )


def _historical_rows(connection: sqlite3.Connection) -> dict[str, list[Any]]:
    # Fixed historical tables only: the new allowance cannot alter their digest.
    tables = (
        "preflight_run", "admin_catalog_claim", "admin_catalog_resume_claim",
        "metadata_filter_refresh", "metadata_filter_refresh_targets",
        "bounded_readonly_trial", "six_hour_continuation",
    )
    return {
        table: sorted(connection.execute(f"SELECT * FROM {table}").fetchall(), key=repr)
        for table in tables
    }


def _lineage(inputs: ComparisonInputs, connection: sqlite3.Connection) -> str:
    trial = inputs.trial()
    if not _TrialLedger(trial)._validate_source(connection):
        raise LearningComparisonError("completed trial claim is required")
    binding = _admin_binding_digest(trial.preflight)
    row = connection.execute(
        "SELECT status,candidate_digest,binding,prior_metadata_requests,"
        "metadata_requests,study_requests,stop_reason FROM bounded_readonly_trial"
    ).fetchall()
    if row != [("complete", inputs.candidate_sha256, binding, 42, 7, 1, "cycle_limit")]:
        raise LearningComparisonError("completed trial accounting differs")
    continuation = connection.execute(
        "SELECT status,candidate_digest,trial_report_digest,binding,prior_metadata_requests,"
        "prior_study_requests,prior_cycles,prior_observations,metadata_requests,study_requests,"
        "stop_reason FROM six_hour_continuation"
    ).fetchall()
    if len(continuation) != 1:
        raise LearningComparisonError("completed continuation accounting is required")
    row = continuation[0]
    if row[:2] != ("complete", inputs.candidate_sha256) or row[3:] != (
        binding, 49, 1, 1, 1, 7, 99, "cycle_limit"
    ):
        raise LearningComparisonError("historical allowance is not exactly exhausted")
    return row[2]


def _report(path: Path, digest: str, directory: Path) -> LiveSessionRunReport:
    if (
        not path.is_absolute() or path.parent != directory
        or re.fullmatch(r"shadow-soak-report-[0-9TZ.+-]+\.json", path.name) is None
        or _digest(_read_private_bytes(path, "comparison prior report")) != digest
    ):
        raise LearningComparisonError("comparison prior report binding differs")
    payload = _private_json(path)
    try:
        payload["session_started_at"] = datetime.fromisoformat(payload["session_started_at"])
        payload["session_ended_at"] = datetime.fromisoformat(payload["session_ended_at"])
        payload["failure_category_counts"] = tuple(
            tuple(pair) for pair in payload["failure_category_counts"]
        )
        return LiveSessionRunReport(**payload)
    except (KeyError, TypeError, ValueError) as exc:
        raise LearningComparisonError("comparison prior report is invalid") from exc


def _state(inputs: ComparisonInputs):
    live = inputs.live()
    evidence_path, checkpoint_path = _paths(inputs)
    connection = _deserialize_private_database(evidence_path, "comparison evidence")
    try:
        rows = connection.execute(
            "SELECT tenant_id,resource,sequence,created_at,payload_json,checksum_sha256 "
            "FROM orion_historical_evidence ORDER BY resource,sequence"
        ).fetchall()
    finally:
        connection.close()
    validator = _AuthorizedCompanyEvidenceStore(
        object(), authorization=live.company_authorization,
        entities=frozenset(scope.entity for scope in live.reviewed_scopes),
    )
    scopes = {scope.entity: frozenset(scope.fields) for scope in live.reviewed_scopes}
    sequences: Counter[str] = Counter()
    batches = []
    for row in rows:
        batch = historical_evidence_from_json(row[4])
        sequences[batch.resource] += 1
        if (
            row[:4] != (batch.tenant_id, batch.resource, batch.sequence, batch.created_at.isoformat())
            or row[5] != historical_evidence_checksum(row[4])
            or batch.sequence != sequences[batch.resource]
        ):
            raise LearningComparisonError("comparison evidence envelope differs")
        validator._validate_batch(batch)
        for observation in batch.observations:
            if not set(observation.evidence.payload["record"]).issubset(
                scopes[batch.resource] | {"name", "company", "docstatus"}
            ):
                raise LearningComparisonError("comparison evidence fields cross reviewed scope")
        batches.append(batch)
    connection = _deserialize_private_database(checkpoint_path, "comparison checkpoint")
    try:
        checkpoints = connection.execute(
            "SELECT tenant_id,sequence,created_at,payload_json,checksum_sha256 "
            "FROM orion_study_checkpoints ORDER BY sequence"
        ).fetchall()
    finally:
        connection.close()
    for index, row in enumerate(checkpoints, 1):
        checkpoint = checkpoint_from_json(row[3])
        if not (
            row[:3] == (live.tenant_id, index, checkpoint.created_at.isoformat())
            and checkpoint.tenant_id == live.tenant_id and checkpoint.sequence == index
            and row[4] == checkpoint_checksum(row[3])
            and {entity.doctype for entity in checkpoint.understanding.entities} == set(scopes)
            and all(
                {field.fieldname for field in entity.fields} == scopes[entity.doctype] | {"company"}
                for entity in checkpoint.understanding.entities
            )
            and set(checkpoint.metadata_targets_studied) == set(scopes)
            and not checkpoint.sampled_records and not checkpoint.record_targets_sampled
        ):
            raise LearningComparisonError("comparison checkpoint facts differ")
    if not checkpoints:
        raise LearningComparisonError("comparison checkpoint is required")
    understanding = checkpoint_from_json(checkpoints[-1][3]).understanding
    return tuple(rows), tuple(checkpoints), tuple(batches), understanding


def _coverage(inputs: ComparisonInputs, understanding, batches) -> tuple[set, set]:
    reviewed = {
        (scope.entity, field)
        for scope in inputs.live().reviewed_scopes
        for field in scope.fields if field not in {"name", "company", "docstatus"}
    }
    coverage = project_historical_coverage(understanding, batches)
    captured = {(item.entity, item.field) for item in coverage if item.observations_seen > 0}
    valid = {(item.entity, item.field) for item in coverage if item.valid_observations > 0}
    return captured & reviewed, valid & reviewed


def _identities(batches) -> list[tuple[str, str]]:
    return [
        (batch.resource, observation.evidence.payload["record"]["name"])
        for batch in batches for observation in batch.observations
    ]


def _baseline(inputs: ComparisonInputs) -> dict[str, Any]:
    live = inputs.live()
    trial = inputs.trial()
    ledger = _existing_private_retry_ledger(trial.preflight)
    with ledger._connect() as connection:
        trial_digest = _lineage(inputs, connection)
        historical_digest = _json_digest(_historical_rows(connection))
    matches = [
        path for path in live.report_directory.glob("shadow-soak-report-*.json")
        if _digest(_read_private_bytes(path, "retained report")) == trial_digest
    ]
    if len(matches) != 1:
        raise LearningComparisonError("exact retained trial report is required")
    trial_path = matches[0]
    prior_inputs = _continuation_inputs(
        inputs.environment, inputs.candidate_path, inputs.candidate_sha256, trial_path, trial_digest
    )
    _validate_trial_report(prior_inputs)
    trial_report = _report(trial_path, trial_digest, live.report_directory)
    continuation = _report(
        inputs.continuation_report_path, inputs.continuation_report_sha256, live.report_directory
    )
    if not (
        continuation.metadata_preflight_completed
        and continuation.metadata_get_budget == continuation.metadata_gets == 7
        and continuation.study_get_budget == continuation.study_gets == 99
        and continuation.cycles_attempted == continuation.cycles_completed == 99
        and continuation.evidence_batches_appended == 99
        and continuation.observations_persisted == 303
        and continuation.distinct_entities_studied == 3
        and continuation.company_scope_count == continuation.distinct_companies_attempted == 1
        and continuation.reviewed_entity_count == 7 and continuation.reviewed_field_count == 133
        and continuation.stop_reason == "cycle_limit" and not continuation.failure_category_counts
        and trial_report.session_ended_at < continuation.session_started_at
    ):
        raise LearningComparisonError("completed continuation report facts differ")
    rows, checkpoints, batches, understanding = _state(inputs)
    captured, valid = _coverage(inputs, understanding, batches)
    trial_batches = [
        batch for batch in batches
        if trial_report.session_started_at <= batch.created_at <= trial_report.session_ended_at
    ]
    continued = [batch for batch in batches if batch not in trial_batches]
    distribution = sorted(Counter(batch.resource for batch in batches).values(), reverse=True)
    continuation_ids = _identities(continued)
    if not (
        len(rows) == 100 and len(_identities(batches)) == PRIOR_OBSERVATIONS
        and len(checkpoints) == 2
        and len(trial_batches) == 1 and len(trial_batches[0].observations) == 1
        and trial_batches[0].sequence == 1
        and len(continued) == 99 and len(continuation_ids) == 303
        and len(continuation_ids) - len(set(continuation_ids) - set(_identities(trial_batches))) == 291
        and all(
            continuation.session_started_at <= batch.created_at <= continuation.session_ended_at
            for batch in continued
        )
        and distribution == [96, 2, 2]
        and len({entity for entity, _ in captured}) == 3 and len(captured) == 6
    ):
        raise LearningComparisonError("retained baseline does not reconcile with reviewed totals")
    evidence, checkpoint = _paths(inputs)
    files = {
        "candidate": inputs.candidate_path,
        "continuation_report": inputs.continuation_report_path,
        "trial_report": trial_path,
        "evidence": evidence,
        "checkpoint": checkpoint,
    }
    return {
        "schema_version": 1,
        "source_commit": inputs.source_commit,
        "authorization_digest": _digest(inputs.authorization_reference.encode()),
        "prior_binding": _admin_binding_digest(trial.preflight),
        "historical_ledger_digest": historical_digest,
        "files": {
            role: {"path": str(path), "sha256": _digest(_read_private_bytes(path, "baseline"))}
            for role, path in files.items()
        },
        "evidence_prefix": [_json_digest(row) for row in rows],
        "checkpoint_prefix": [_json_digest(row) for row in checkpoints],
        "baseline": {
            "metadata_gets": 56, "study_gets": 100, "combined_gets": 156,
            "cycles": 100, "observations": 304, "entities_covered": 3, "fields_covered": 6,
            "valid_fields_covered": len(valid), "entity_batch_distribution": distribution,
            "continuation_observations": 303, "continuation_repeated_identities": 291,
            "distinct_identities": len(set(_identities(batches))),
        },
    }


def prepare_comparison_manifest(inputs: ComparisonInputs, path: Path) -> str:
    """Write a private, exclusive offline baseline; this grants no live authority."""
    live = inputs.live()
    if not path.is_absolute() or path.parent != live.report_directory:
        raise LearningComparisonError("comparison manifest must be in the private report directory")
    manifest = _baseline(inputs)
    _write_private_json(path, manifest)
    return _digest(_read_private_bytes(path, "comparison manifest"))


def _validate_manifest(inputs: ComparisonInputs, path: Path, digest: str) -> dict[str, Any]:
    if (
        not path.is_absolute() or path.parent != inputs.live().report_directory
        or re.fullmatch(r"[0-9a-f]{64}", digest) is None
        or _digest(_read_private_bytes(path, "comparison manifest")) != digest
    ):
        raise LearningComparisonError("comparison manifest binding differs")
    manifest = _private_json(path)
    if manifest != _baseline(inputs):
        raise LearningComparisonError("comparison baseline changed after review")
    return manifest


class _ComparisonLedger:
    def __init__(self, inputs: ComparisonInputs, manifest_path: Path, manifest_digest: str):
        self.inputs = inputs
        self.path = manifest_path
        self.digest = manifest_digest
        self.ledger = _existing_private_retry_ledger(inputs.trial().preflight)

    def exists(self, connection) -> bool:
        return connection.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (_TABLE,)
        ).fetchone() is not None

    def claim(self) -> dict[str, Any]:
        manifest = _validate_manifest(self.inputs, self.path, self.digest)
        connection = self.ledger._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            _lineage(self.inputs, connection)
            if self.exists(connection):
                raise LearningComparisonError("comparison was already claimed")
            if _json_digest(_historical_rows(connection)) != manifest["historical_ledger_digest"]:
                raise LearningComparisonError("historical accounting changed before claim")
            # Re-read immutable state while the competing claim writer is excluded.
            if manifest != _baseline(self.inputs):
                raise LearningComparisonError("comparison baseline changed before claim")
            connection.execute(_SCHEMA)
            connection.execute(
                f"INSERT INTO {_TABLE} VALUES (1,'running',?,?,?,56,100,0,0,'none')",
                (manifest["authorization_digest"], self.inputs.source_commit, self.digest),
            )
            connection.commit()
            return manifest
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def reserve(self, kind: str) -> None:
        connection = self.ledger._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            row = connection.execute(
                f"SELECT status,metadata_gets,study_gets FROM {_TABLE} WHERE singleton=1"
            ).fetchone()
            if row is None or row[0] != "running":
                raise LearningComparisonError("comparison is not running")
            metadata, study = row[1:]
            if kind == "metadata" and metadata < METADATA_GETS and study == 0:
                metadata += 1
            elif kind == "study" and metadata == METADATA_GETS and study < STUDY_GETS:
                study += 1
            else:
                raise LearningComparisonError("comparison request allowance exhausted")
            connection.execute(
                f"UPDATE {_TABLE} SET metadata_gets=?,study_gets=? WHERE singleton=1",
                (metadata, study),
            )
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def snapshot(self):
        with self.ledger._connect() as connection:
            return connection.execute(
                f"SELECT status,metadata_gets,study_gets,stop_reason FROM {_TABLE} WHERE singleton=1"
            ).fetchone()

    def finish(self, status: str, reason: str):
        if status not in {"complete", "failed", "interrupted"}:
            raise LearningComparisonError("invalid comparison final status")
        with self.ledger._connect() as connection:
            cursor = connection.execute(
                f"UPDATE {_TABLE} SET status=?,stop_reason=? WHERE singleton=1 AND status='running'",
                (status, reason),
            )
            if cursor.rowcount != 1:
                raise LearningComparisonError("comparison finalization failed")


def inspect_learning_comparison_readiness(
    inputs: ComparisonInputs, manifest_path: Path, manifest_sha256: str
) -> dict[str, Any]:
    status = "invalid"
    credentials = False
    try:
        live = inputs.live()
        ledger = _ComparisonLedger(inputs, manifest_path, manifest_sha256)
        with ledger.ledger._connect() as connection:
            claimed = ledger.exists(connection)
        if claimed:
            status = "already_claimed"
        else:
            _validate_manifest(inputs, manifest_path, manifest_sha256)
            if all(_destination_ready(path, private=True) for path in (
                live.state_directory, live.report_directory
            )):
                status = "ready"
        try:
            live.credential_references.resolve(inputs.environment)
            credentials = True
        except Exception:  # noqa: BLE001 - readiness emits only fixed aggregate facts
            credentials = False
    except Exception:  # noqa: BLE001 - readiness emits only fixed aggregate facts
        status = "invalid"
    return {
        "execution_allowed": False, "status": status, "offline_inputs_ready": status == "ready",
        "credentials_available": credentials,
        "ready_for_authorized_launch": status == "ready" and credentials,
        "new_metadata_get_budget": 7, "new_study_get_budget": 20, "new_combined_get_max": 27,
        "prior_metadata_gets": 56, "prior_study_gets": 100, "prior_combined_gets": 156,
        "metadata_cumulative_max": 63, "study_cumulative_max": 120, "combined_cumulative_max": 183,
        "max_cycles": 20, "max_observations": 100, "max_wall_clock_seconds": 900,
        "max_consecutive_non_progress": 5, "live_requests_performed": 0, "erp_writes": 0,
        "recommendation_allowed": False, "promotion_allowed": False,
    }


class _ComparisonMetrics:
    def __init__(self, inputs, manifest):
        self.inputs = inputs
        self.manifest = manifest
        rows, checkpoints, batches, self.understanding = _state(inputs)
        if (
            [_json_digest(row) for row in rows] != manifest["evidence_prefix"]
            or [_json_digest(row) for row in checkpoints] != manifest["checkpoint_prefix"]
        ):
            raise LearningComparisonError("comparison baseline changed after claim")
        self.rows = {(row[1], row[2]): row for row in rows}
        self.checkpoints = checkpoints
        self.batches = batches
        self.covered, self.valid = _coverage(inputs, self.understanding, batches)
        self.initial_covered = set(self.covered)
        self.identities = set(_identities(batches))
        self.entities = {scope.entity: index for index, scope in enumerate(
            inputs.live().reviewed_scopes, 1
        )}
        self.studies: list[dict[str, Any]] = []
        self.pending: str | None = None

    def collect(self):
        rows, checkpoints, batches, understanding = _state(self.inputs)
        current = {(row[1], row[2]): row for row in rows}
        if any(current.get(key) != row for key, row in self.rows.items()):
            raise LearningComparisonError("comparison changed retained evidence")
        if checkpoints[:len(self.checkpoints)] != self.checkpoints:
            raise LearningComparisonError("comparison changed retained checkpoints")
        new_batches = [batch for batch in batches if (batch.resource, batch.sequence) not in self.rows]
        if new_batches and (
            self.pending is None or len(new_batches) != 1
            or new_batches[0].resource != self.pending or len(new_batches[0].observations) > 5
        ):
            raise LearningComparisonError("comparison evidence does not match study GET")
        if self.pending is not None:
            covered, valid = _coverage(self.inputs, understanding, batches)
            new_identities = repeated = 0
            for identity in _identities(new_batches):
                if identity in self.identities:
                    repeated += 1
                else:
                    new_identities += 1
                    self.identities.add(identity)
            self.studies[-1].update(
                observations=sum(len(batch.observations) for batch in new_batches),
                newly_covered_fields=len(covered - self.covered),
                new_identities=new_identities, repeated_identities=repeated,
            )
            self.covered, self.valid = covered, valid
        self.pending = None
        self.rows, self.batches = current, batches

    def begin(self, request):
        self.collect()
        parsed = urlparse(request.full_url)
        entity = unquote(parsed.path.rsplit("/", 1)[-1])
        query = parse_qs(parsed.query)
        if request.get_method() != "GET" or entity not in self.entities:
            raise LearningComparisonError("comparison request scope differs")
        fields = json.loads(query["fields"][0])
        self.studies.append({
            "study_get": len(self.studies) + 1, "entity_index": self.entities[entity],
            "requested_field_count": len(set(fields) - {"name", "company", "docstatus"}),
        })
        self.pending = entity

    def report(self):
        self.collect()
        return {
            "entities_covered": len({entity for entity, _ in self.covered}),
            "fields_covered": len(self.covered), "valid_fields_covered": len(self.valid),
            "newly_covered_fields": len(self.covered - self.initial_covered),
            "new_identities": sum(study["new_identities"] for study in self.studies),
            "repeated_identities": sum(study["repeated_identities"] for study in self.studies),
            "observations": sum(study["observations"] for study in self.studies),
            "study_distribution": [
                {"entity_index": index, "study_gets": count}
                for index, count in sorted(Counter(
                    study["entity_index"] for study in self.studies
                ).items())
            ],
            "per_study_get": self.studies,
        }


def run_learning_comparison(
    inputs: ComparisonInputs, manifest_path: Path, manifest_sha256: str, *,
    metadata_opener: Callable[..., Any] | None = None,
    record_opener: Callable[..., Any] | None = None,
    clock: Callable[..., Any] | None = None,
    monotonic: Callable[[], float] | None = None,
    termination_requested: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    monotonic = monotonic or time.monotonic
    started_tick = _read_monotonic(monotonic)
    last_tick = started_tick

    def stop_at_transport(kind):
        nonlocal last_tick
        current = _read_monotonic(monotonic)
        if current < last_tick:
            raise LearningComparisonError("comparison clock moved backwards")
        last_tick = current
        reason = (
            ShadowSoakStopReason.USER_TERMINATION
            if termination_requested is not None and termination_requested()
            else ShadowSoakStopReason.DURATION_LIMIT if current - started_tick >= SECONDS
            else None
        )
        if reason is not None:
            if kind == "study":
                raise _StopBeforeRead(reason)
            if reason is ShadowSoakStopReason.USER_TERMINATION:
                raise _PreflightTermination
            raise _PreflightDuration

    live = inputs.live()
    live.credential_references.resolve(inputs.environment)
    ledger = _ComparisonLedger(inputs, manifest_path, manifest_sha256)
    manifest = ledger.claim()
    metadata_transport = metadata_opener or _default_opener
    record_transport = record_opener or _default_opener
    accounting_failed = False
    try:
        metrics = _ComparisonMetrics(inputs, manifest)

        def verify_retained():
            with ledger.ledger._connect() as connection:
                if _json_digest(_historical_rows(connection)) != manifest["historical_ledger_digest"]:
                    raise LearningComparisonError("comparison changed historical accounting")
            for role in ("candidate", "trial_report", "continuation_report"):
                artifact = manifest["files"][role]
                body = _read_private_bytes(Path(artifact["path"]), "retained artifact")
                if _digest(body) != artifact["sha256"]:
                    raise LearningComparisonError("comparison changed retained artifact")
            if _digest(_read_private_bytes(manifest_path, "comparison manifest")) != manifest_sha256:
                raise LearningComparisonError("comparison manifest changed during run")

        def charged_metadata(request, *, timeout):
            nonlocal accounting_failed
            try:
                verify_retained()
                metrics.collect()
                ledger.reserve("metadata")
                stop_at_transport("metadata")
            except Exception as exc:
                accounting_failed = True
                raise _PersistenceFailure("comparison metadata reservation failed") from exc
            return metadata_transport(request, timeout=timeout)

        def charged_record(request, *, timeout):
            nonlocal accounting_failed
            try:
                verify_retained()
                metrics.begin(request)
                ledger.reserve("study")
                stop_at_transport("study")
            except Exception as exc:
                accounting_failed = True
                raise _PersistenceFailure("comparison study accounting failed") from exc
            return record_transport(request, timeout=timeout)

        kwargs: dict[str, Any] = {
            "environment": inputs.environment, "metadata_opener": charged_metadata,
            "record_opener": charged_record, "termination_requested": termination_requested,
        }
        if clock is not None:
            kwargs["clock"] = clock
        if monotonic is not None:
            kwargs["monotonic"] = monotonic
        report = run_erpnext_live_session(live, **kwargs)
        _, metadata, study, _ = ledger.snapshot()
        if accounting_failed or (metadata, study) != (report.metadata_gets, report.study_gets):
            raise LearningComparisonError("comparison request accounting failed")
        aggregates = metrics.report()
        if aggregates["observations"] != report.observations_persisted:
            raise LearningComparisonError("comparison observation accounting differs")
        verify_retained()
        status = (
            "interrupted" if report.stop_reason == "user_termination" else "failed"
            if report.stop_reason in {
                "metadata_preflight_failure", "persistence_failure",
                "tenant_scope_mismatch", "erp_contract_failure",
            } else "complete"
        )
        result = {
            "execution_allowed": False, "recommendation_allowed": False,
            "promotion_allowed": False, "erp_writes": 0, "status": status,
            "stop_reason": report.stop_reason, "source_commit": inputs.source_commit,
            "baseline_manifest_sha256": manifest_sha256,
            "prior_metadata_gets": 56, "prior_study_gets": 100, "prior_combined_gets": 156,
            "new_metadata_gets": metadata, "new_study_gets": study,
            "new_combined_gets": metadata + study,
            "metadata_cumulative_gets": 56 + metadata, "study_cumulative_gets": 100 + study,
            "combined_cumulative_gets": 156 + metadata + study,
            "cycles_attempted": report.cycles_attempted, "cycles_completed": report.cycles_completed,
            "baseline": manifest["baseline"], "comparison": aggregates,
            "new_fields_per_study_get": aggregates["newly_covered_fields"] / study if study else 0.0,
            "observations_per_study_get": aggregates["observations"] / study if study else 0.0,
            "descriptive_only": True,
        }
        path = live.report_directory / f"learning-comparison-report-{manifest_sha256}.json"
        _write_private_json(path, result)
        ledger.finish(status, report.stop_reason)
        return result
    except (KeyboardInterrupt, SystemExit):
        ledger.finish("interrupted", "user_termination")
        raise
    except Exception:
        ledger.finish("failed", "accounting_or_storage_failure")
        raise


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Prepare or inspect a bounded learning comparison", allow_abbrev=False)
    parser.add_argument("--candidate", type=Path, required=True)
    parser.add_argument("--candidate-sha256", required=True)
    parser.add_argument("--continuation-report", type=Path, required=True)
    parser.add_argument("--continuation-report-sha256", required=True)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--comparison-authorization-reference", required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--manifest-sha256")
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--prepare-manifest", action="store_true")
    actions.add_argument("--execute-learning-comparison", action="store_true")
    args = parser.parse_args(argv)
    inputs = ComparisonInputs(
        dict(os.environ), args.candidate, args.candidate_sha256,
        args.continuation_report, args.continuation_report_sha256,
        args.source_commit, args.comparison_authorization_reference,
    )
    try:
        if args.prepare_manifest:
            digest = prepare_comparison_manifest(inputs, args.manifest)
            result = {"execution_allowed": False, "status": "prepared", "manifest_sha256": digest}
        elif args.execute_learning_comparison:
            if not args.manifest_sha256:
                raise LearningComparisonError("reviewed manifest digest is required")
            with _live_session_termination_signals() as termination:
                result = run_learning_comparison(
                    inputs, args.manifest, args.manifest_sha256, termination_requested=termination
                )
        else:
            result = inspect_learning_comparison_readiness(
                inputs, args.manifest, args.manifest_sha256 or ""
            )
    except Exception:  # noqa: BLE001 - CLI emits only a fixed safe category
        print('{"execution_allowed":false,"status":"comparison_refused"}')
        return 2
    print(json.dumps(result, sort_keys=True, separators=(",", ":")))
    return 130 if result["status"] == "interrupted" else 0 if result["status"] in {
        "ready", "prepared", "complete"
    } else 2


if __name__ == "__main__":
    raise SystemExit(main())
