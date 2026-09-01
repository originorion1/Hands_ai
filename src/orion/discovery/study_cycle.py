"""One bounded governed ORION study cycle.

A study cycle plans once, preflights the entire plan, processes structural
metadata first, and only then permits record sampling.

It does not loop autonomously, persist data, modify authorization, promote
knowledge, or invoke any execution capability.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..contracts import Observation
from ..understanding.metadata import (
    MetadataUnderstanding,
    build_metadata_understanding,
    merge_metadata_understandings,
)
from .governed_runner import (
    DiscoveryReader,
    GovernedDiscoveryReport,
    preflight_governed_discovery,
    run_governed_discovery,
)
from .planner import (
    DiscoveryAuthorization,
    DiscoveryPlan,
    DiscoveryTargetKind,
    plan_authorized_discovery,
)


@dataclass(frozen=True, slots=True)
class StudyCycleResult:
    tenant_id: str
    plan: DiscoveryPlan
    discovery: GovernedDiscoveryReport
    understanding: MetadataUnderstanding
    metadata_observations: tuple[Observation, ...]
    record_observations: tuple[Observation, ...]
    sampled_records: frozenset[str]


def _subplan(
    plan: DiscoveryPlan,
    kind: DiscoveryTargetKind,
) -> DiscoveryPlan:
    return DiscoveryPlan(
        tenant_id=plan.tenant_id,
        items=tuple(
            item
            for item in plan.items
            if item.kind is kind
        ),
    )


def run_study_cycle(
    understanding: MetadataUnderstanding,
    *,
    authorization: DiscoveryAuthorization,
    metadata_reader: DiscoveryReader,
    record_reader: DiscoveryReader,
    already_sampled_records: frozenset[str] = frozenset(),
) -> StudyCycleResult:
    """Run exactly one authorized read-only learning cycle.

    The complete plan is validated before any external read. Metadata reads
    then complete and merge successfully before record sampling is allowed.
    """

    plan = plan_authorized_discovery(
        understanding,
        authorization=authorization,
        already_sampled_records=already_sampled_records,
    )

    # Preserve the governed-runner invariant that a bad later plan item causes
    # zero reads anywhere in the cycle.
    preflight_governed_discovery(
        plan,
        authorization=authorization,
        understanding=understanding,
    )

    metadata_plan = _subplan(
        plan,
        DiscoveryTargetKind.METADATA,
    )

    planned_metadata_targets = frozenset(
        item.target
        for item in metadata_plan.items
    )

    record_plan = _subplan(
        plan,
        DiscoveryTargetKind.RECORDS,
    )

    updated_understanding = understanding

    metadata_report = run_governed_discovery(
        metadata_plan,
        authorization=authorization,
        understanding=understanding,
        metadata_reader=metadata_reader,
        record_reader=record_reader,
    )

    metadata_observations = metadata_report.observations

    if metadata_observations:
        # Validate the complete returned metadata bundle against structure
        # ORION already knows. Incidental metadata is not authorized for
        # absorption, but it must not be allowed to contradict established
        # structural understanding silently.
        complete_bundle = build_metadata_understanding(
            metadata_observations,
            tenant_id=authorization.tenant_id,
        )

        # Discard the merged result. This pass exists only to detect
        # contradictions with current understanding before any record read.
        merge_metadata_understandings(
            understanding,
            complete_bundle,
        )

        # Only DocTypes explicitly present in this cycle's governed metadata
        # plan may become new structural understanding.
        incremental = build_metadata_understanding(
            metadata_observations,
            tenant_id=authorization.tenant_id,
            allowed_doctypes=planned_metadata_targets,
        )

        updated_understanding = merge_metadata_understandings(
            understanding,
            incremental,
        )

    record_report = run_governed_discovery(
        record_plan,
        authorization=authorization,
        understanding=updated_understanding,
        metadata_reader=metadata_reader,
        record_reader=record_reader,
    )

    record_observations = record_report.observations

    newly_sampled = {
        item.plan_item.target
        for item in record_report.items
    }

    discovery = GovernedDiscoveryReport(
        tenant_id=authorization.tenant_id,
        items=metadata_report.items + record_report.items,
    )

    return StudyCycleResult(
        tenant_id=authorization.tenant_id,
        plan=plan,
        discovery=discovery,
        understanding=updated_understanding,
        metadata_observations=metadata_observations,
        record_observations=record_observations,
        sampled_records=frozenset(
            set(already_sampled_records) | newly_sampled
        ),
    )
