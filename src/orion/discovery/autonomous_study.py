"""Bounded autonomous read-only study for ORION.

The controller may repeat governed study cycles, but only inside one fixed
tenant-scoped authorization envelope and explicit resource budgets.

It cannot widen authorization, persist state, promote knowledge, or execute
ERP actions.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum

from ..understanding.metadata import MetadataUnderstanding
from .governed_runner import DiscoveryReader
from .planner import (
    DiscoveryAuthorization,
    DiscoveryTargetKind,
    plan_authorized_discovery,
)
from .study_cycle import StudyCycleResult, run_study_cycle


class AutonomousStudyError(ValueError):
    """Raised when autonomous study violates a controller invariant."""


class AutonomousStudyStopReason(StrEnum):
    EXHAUSTED = "exhausted"
    CYCLE_LIMIT = "cycle_limit"
    METADATA_TARGET_LIMIT = "metadata_target_limit"
    RECORD_TARGET_LIMIT = "record_target_limit"


@dataclass(frozen=True, slots=True)
class AutonomousStudyLimits:
    """Hard bounds for one autonomous study invocation."""

    max_cycles: int = 10
    max_metadata_targets: int = 50
    max_record_targets: int = 20

    def __post_init__(self) -> None:
        if (
            type(self.max_cycles) is not int
            or not 1 <= self.max_cycles <= 100
        ):
            raise AutonomousStudyError(
                "max_cycles must be between 1 and 100"
            )

        if (
            type(self.max_metadata_targets) is not int
            or not 0 <= self.max_metadata_targets <= 1000
        ):
            raise AutonomousStudyError(
                "max_metadata_targets must be between 0 and 1000"
            )

        if (
            type(self.max_record_targets) is not int
            or not 0 <= self.max_record_targets <= 1000
        ):
            raise AutonomousStudyError(
                "max_record_targets must be between 0 and 1000"
            )


@dataclass(frozen=True, slots=True)
class AutonomousStudyReport:
    tenant_id: str
    cycles: tuple[StudyCycleResult, ...]
    understanding: MetadataUnderstanding
    sampled_records: frozenset[str]
    metadata_targets_studied: tuple[str, ...]
    record_targets_sampled: tuple[str, ...]
    stop_reason: AutonomousStudyStopReason

    @property
    def cycles_completed(self) -> int:
        return len(self.cycles)

    @property
    def observation_count(self) -> int:
        return sum(
            cycle.discovery.observation_count
            for cycle in self.cycles
        )


@dataclass(frozen=True, slots=True)
class AutonomousStudyProgress:
    """Immutable progress snapshot after one completed study cycle."""

    tenant_id: str
    cycle_number: int
    cycle: StudyCycleResult
    understanding: MetadataUnderstanding
    sampled_records: frozenset[str]
    metadata_targets_studied: tuple[str, ...]
    record_targets_sampled: tuple[str, ...]


StudyProgressObserver = Callable[
    [AutonomousStudyProgress],
    None,
]


def _report(
    *,
    tenant_id: str,
    cycles: list[StudyCycleResult],
    understanding: MetadataUnderstanding,
    sampled_records: frozenset[str],
    metadata_targets: list[str],
    record_targets: list[str],
    stop_reason: AutonomousStudyStopReason,
) -> AutonomousStudyReport:
    return AutonomousStudyReport(
        tenant_id=tenant_id,
        cycles=tuple(cycles),
        understanding=understanding,
        sampled_records=sampled_records,
        metadata_targets_studied=tuple(metadata_targets),
        record_targets_sampled=tuple(record_targets),
        stop_reason=stop_reason,
    )


def run_autonomous_study(
    understanding: MetadataUnderstanding,
    *,
    authorization: DiscoveryAuthorization,
    metadata_reader: DiscoveryReader,
    record_reader: DiscoveryReader,
    limits: AutonomousStudyLimits | None = None,
    already_sampled_records: frozenset[str] = frozenset(),
    progress_observer: StudyProgressObserver | None = None,
) -> AutonomousStudyReport:
    """Study repeatedly until work is exhausted or a hard bound is reached.

    Every cycle still passes through the planner and governed runner. The
    authorization object is never modified or expanded by this controller.
    """

    if limits is None:
        limits = AutonomousStudyLimits()

    current = understanding
    sampled = already_sampled_records
    cycles: list[StudyCycleResult] = []
    metadata_targets: list[str] = []
    record_targets: list[str] = []

    for _ in range(limits.max_cycles):
        prospective_plan = plan_authorized_discovery(
            current,
            authorization=authorization,
            already_sampled_records=sampled,
        )

        if prospective_plan.is_empty:
            return _report(
                tenant_id=authorization.tenant_id,
                cycles=cycles,
                understanding=current,
                sampled_records=sampled,
                metadata_targets=metadata_targets,
                record_targets=record_targets,
                stop_reason=AutonomousStudyStopReason.EXHAUSTED,
            )

        prospective_metadata = tuple(
            item.target
            for item in prospective_plan.items
            if item.kind is DiscoveryTargetKind.METADATA
        )
        prospective_records = tuple(
            item.target
            for item in prospective_plan.items
            if item.kind is DiscoveryTargetKind.RECORDS
        )

        if (
            len(metadata_targets) + len(prospective_metadata)
            > limits.max_metadata_targets
        ):
            return _report(
                tenant_id=authorization.tenant_id,
                cycles=cycles,
                understanding=current,
                sampled_records=sampled,
                metadata_targets=metadata_targets,
                record_targets=record_targets,
                stop_reason=(
                    AutonomousStudyStopReason.METADATA_TARGET_LIMIT
                ),
            )

        if (
            len(record_targets) + len(prospective_records)
            > limits.max_record_targets
        ):
            return _report(
                tenant_id=authorization.tenant_id,
                cycles=cycles,
                understanding=current,
                sampled_records=sampled,
                metadata_targets=metadata_targets,
                record_targets=record_targets,
                stop_reason=(
                    AutonomousStudyStopReason.RECORD_TARGET_LIMIT
                ),
            )

        result = run_study_cycle(
            current,
            authorization=authorization,
            metadata_reader=metadata_reader,
            record_reader=record_reader,
            already_sampled_records=sampled,
        )

        if result.plan != prospective_plan:
            raise AutonomousStudyError(
                "study plan changed between controller preflight and cycle"
            )

        previous_understanding = current
        previous_sampled = sampled

        current = result.understanding
        sampled = result.sampled_records

        metadata_targets.extend(prospective_metadata)
        record_targets.extend(prospective_records)
        cycles.append(result)

        if (
            current == previous_understanding
            and sampled == previous_sampled
        ):
            raise AutonomousStudyError(
                "non-empty study cycle made no structural or sampling progress"
            )

        if progress_observer is not None:
            progress_observer(
                AutonomousStudyProgress(
                    tenant_id=authorization.tenant_id,
                    cycle_number=len(cycles),
                    cycle=result,
                    understanding=current,
                    sampled_records=sampled,
                    metadata_targets_studied=tuple(
                        metadata_targets
                    ),
                    record_targets_sampled=tuple(
                        record_targets
                    ),
                )
            )

    remaining_plan = plan_authorized_discovery(
        current,
        authorization=authorization,
        already_sampled_records=sampled,
    )

    stop_reason = (
        AutonomousStudyStopReason.EXHAUSTED
        if remaining_plan.is_empty
        else AutonomousStudyStopReason.CYCLE_LIMIT
    )

    return _report(
        tenant_id=authorization.tenant_id,
        cycles=cycles,
        understanding=current,
        sampled_records=sampled,
        metadata_targets=metadata_targets,
        record_targets=record_targets,
        stop_reason=stop_reason,
    )
