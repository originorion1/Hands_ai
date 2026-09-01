"""Restart-resilient orchestration for bounded autonomous ORION study.

The session restores durable learned state, requires fresh authorization from
the caller, and checkpoints after every successfully completed study cycle.

Checkpoint state never restores authority, credentials, or execution rights.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime

from ..understanding.metadata import MetadataUnderstanding
from .autonomous_study import (
    AutonomousStudyLimits,
    AutonomousStudyProgress,
    AutonomousStudyReport,
    run_autonomous_study,
)
from .checkpoint import (
    StudyCheckpoint,
    StudyCheckpointStore,
)
from .governed_runner import DiscoveryReader
from .planner import DiscoveryAuthorization


class ResumableStudySessionError(ValueError):
    """Raised when resumable study violates a recovery boundary."""


@dataclass(frozen=True, slots=True)
class ResumableStudySessionResult:
    tenant_id: str
    resumed_from_sequence: int | None
    study: AutonomousStudyReport
    latest_checkpoint: StudyCheckpoint
    checkpoints_written: int


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _merge_unique(
    existing: tuple[str, ...],
    additional: tuple[str, ...],
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(existing + additional)
    )


def run_resumable_study_session(
    seed_understanding: MetadataUnderstanding,
    *,
    authorization: DiscoveryAuthorization,
    checkpoint_store: StudyCheckpointStore,
    metadata_reader: DiscoveryReader,
    record_reader: DiscoveryReader,
    limits: AutonomousStudyLimits | None = None,
    clock: Callable[[], datetime] = _utc_now,
) -> ResumableStudySessionResult:
    """Resume tenant study from durable state using fresh authorization.

    If a checkpoint exists, it is the canonical recovery state and the seed is
    not merged into or allowed to overwrite it.

    Every successfully completed autonomous cycle is checkpointed before the
    next cycle begins. A later failure therefore cannot erase previously
    completed cycles.
    """

    tenant_id = authorization.tenant_id

    if seed_understanding.tenant_id != tenant_id:
        raise ResumableStudySessionError(
            "seed understanding crosses authorization tenant boundary"
        )

    restored = checkpoint_store.load_latest(
        tenant_id=tenant_id,
    )

    if restored is not None and restored.tenant_id != tenant_id:
        raise ResumableStudySessionError(
            "checkpoint store returned cross-tenant state"
        )

    resumed_from_sequence = (
        restored.sequence
        if restored is not None
        else None
    )

    if restored is None:
        current_understanding = seed_understanding
        sampled_records = frozenset()
        previous_metadata_targets: tuple[str, ...] = ()
        previous_record_targets: tuple[str, ...] = ()
        next_sequence = 1
    else:
        current_understanding = restored.understanding
        sampled_records = restored.sampled_records
        previous_metadata_targets = (
            restored.metadata_targets_studied
        )
        previous_record_targets = (
            restored.record_targets_sampled
        )
        next_sequence = restored.sequence + 1

    latest_checkpoint = restored
    checkpoints_written = 0

    def persist_progress(
        progress: AutonomousStudyProgress,
    ) -> None:
        nonlocal latest_checkpoint
        nonlocal checkpoints_written
        nonlocal next_sequence

        if progress.tenant_id != tenant_id:
            raise ResumableStudySessionError(
                "autonomous progress crosses tenant boundary"
            )

        checkpoint = StudyCheckpoint(
            tenant_id=tenant_id,
            sequence=next_sequence,
            created_at=clock(),
            understanding=progress.understanding,
            sampled_records=progress.sampled_records,
            metadata_targets_studied=_merge_unique(
                previous_metadata_targets,
                progress.metadata_targets_studied,
            ),
            record_targets_sampled=_merge_unique(
                previous_record_targets,
                progress.record_targets_sampled,
            ),
        )

        # Append must succeed before another autonomous cycle is permitted.
        checkpoint_store.append(checkpoint)

        latest_checkpoint = checkpoint
        checkpoints_written += 1
        next_sequence += 1

    study = run_autonomous_study(
        current_understanding,
        authorization=authorization,
        metadata_reader=metadata_reader,
        record_reader=record_reader,
        limits=limits,
        already_sampled_records=sampled_records,
        progress_observer=persist_progress,
    )

    if latest_checkpoint is None:
        # No checkpoint existed and no autonomous cycle was required.
        # Persist the validated seed so restart still has a durable base.
        latest_checkpoint = StudyCheckpoint(
            tenant_id=tenant_id,
            sequence=1,
            created_at=clock(),
            understanding=study.understanding,
            sampled_records=study.sampled_records,
            metadata_targets_studied=(
                study.metadata_targets_studied
            ),
            record_targets_sampled=(
                study.record_targets_sampled
            ),
        )

        checkpoint_store.append(latest_checkpoint)
        checkpoints_written = 1

    return ResumableStudySessionResult(
        tenant_id=tenant_id,
        resumed_from_sequence=resumed_from_sequence,
        study=study,
        latest_checkpoint=latest_checkpoint,
        checkpoints_written=checkpoints_written,
    )
