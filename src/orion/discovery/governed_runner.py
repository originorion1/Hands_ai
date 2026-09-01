"""Governed enforcement boundary for executing read-only discovery plans.

A discovery plan expresses intent. This runner independently verifies that
intent against the current tenant-scoped understanding and explicit
authorization before any read function is invoked.

The runner has no credentials, ERP-specific networking, write capability,
knowledge promotion, or execution authority.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from uuid import UUID

from ..contracts import EvidenceKind, Observation, ObservationMode
from ..understanding.metadata import MetadataUnderstanding
from .planner import (
    DiscoveryAuthorization,
    DiscoveryPlan,
    DiscoveryPlanItem,
    DiscoveryTargetKind,
    validate_discovery_target,
)


class GovernedDiscoveryError(ValueError):
    """Raised when governed discovery would violate a safety boundary."""


DiscoveryReader = Callable[[str], tuple[Observation, ...]]


@dataclass(frozen=True, slots=True)
class GovernedDiscoveryItemResult:
    plan_item: DiscoveryPlanItem
    observations: tuple[Observation, ...]


@dataclass(frozen=True, slots=True)
class GovernedDiscoveryReport:
    tenant_id: str
    items: tuple[GovernedDiscoveryItemResult, ...]

    @property
    def observations(self) -> tuple[Observation, ...]:
        return tuple(
            observation
            for item in self.items
            for observation in item.observations
        )

    @property
    def observation_count(self) -> int:
        return len(self.observations)


def _understood_targets(
    understanding: MetadataUnderstanding,
) -> frozenset[str]:
    return frozenset(
        entity.doctype
        for entity in understanding.entities
    )


def _known_provenance(
    understanding: MetadataUnderstanding,
) -> frozenset[UUID]:
    return frozenset(
        evidence_id
        for entity in understanding.entities
        for evidence_id in entity.provenance_ids
    )


def _referenced_metadata_targets(
    understanding: MetadataUnderstanding,
) -> frozenset[str]:
    targets: set[str] = set()

    for entity in understanding.entities:
        for field in entity.fields:
            if field.fieldtype not in {
                "Link",
                "Table",
                "Table MultiSelect",
            }:
                continue

            target = field.options

            if not isinstance(target, str) or not target:
                continue

            validate_discovery_target(target)
            targets.add(target)

    return frozenset(targets)


def preflight_governed_discovery(
    plan: DiscoveryPlan,
    *,
    authorization: DiscoveryAuthorization,
    understanding: MetadataUnderstanding,
) -> None:
    """Validate the complete plan before the first external read."""

    if plan.tenant_id != authorization.tenant_id:
        raise GovernedDiscoveryError(
            "plan crosses authorization tenant boundary"
        )

    if understanding.tenant_id != authorization.tenant_id:
        raise GovernedDiscoveryError(
            "understanding crosses authorization tenant boundary"
        )

    if len(plan.items) > authorization.max_targets:
        raise GovernedDiscoveryError(
            "plan exceeds authorized target bound"
        )

    understood = _understood_targets(understanding)
    referenced = _referenced_metadata_targets(understanding)
    known_provenance = _known_provenance(understanding)

    seen: set[tuple[DiscoveryTargetKind, str]] = set()

    for item in plan.items:
        if not isinstance(item.kind, DiscoveryTargetKind):
            raise GovernedDiscoveryError(
                "plan contains unsupported target kind"
            )

        validate_discovery_target(item.target)

        identity = (item.kind, item.target)

        if identity in seen:
            raise GovernedDiscoveryError(
                "plan contains duplicate target"
            )
        seen.add(identity)

        if not item.provenance_ids:
            raise GovernedDiscoveryError(
                "plan item requires structural provenance"
            )

        if any(
            evidence_id not in known_provenance
            for evidence_id in item.provenance_ids
        ):
            raise GovernedDiscoveryError(
                "plan item contains unknown structural provenance"
            )

        if item.kind is DiscoveryTargetKind.METADATA:
            if item.target not in authorization.metadata_targets:
                raise GovernedDiscoveryError(
                    "metadata target is not authorized"
                )

            if item.target in understood:
                raise GovernedDiscoveryError(
                    "metadata target is already understood"
                )

            if item.target not in referenced:
                raise GovernedDiscoveryError(
                    "metadata target is not supported by observed structure"
                )

        elif item.kind is DiscoveryTargetKind.RECORDS:
            if item.target not in authorization.record_targets:
                raise GovernedDiscoveryError(
                    "record target is not authorized"
                )

            if item.target not in understood:
                raise GovernedDiscoveryError(
                    "record target structure is not understood"
                )


def _validate_observations(
    item: DiscoveryPlanItem,
    observations: tuple[Observation, ...],
    *,
    tenant_id: str,
) -> None:
    if item.kind is DiscoveryTargetKind.METADATA and len(observations) != 1:
        raise GovernedDiscoveryError(
            "metadata reader must return exactly one observation per target"
        )

    for observation in observations:
        if not isinstance(observation, Observation):
            raise GovernedDiscoveryError(
                "reader returned unsupported observation type"
            )

        if observation.mode is not ObservationMode.READ_ONLY:
            raise GovernedDiscoveryError(
                "reader returned non-read-only observation"
            )

        evidence = observation.evidence

        if evidence.tenant_id != tenant_id:
            raise GovernedDiscoveryError(
                "reader observation crosses tenant boundary"
            )

        if item.kind is DiscoveryTargetKind.METADATA:
            if evidence.kind is not EvidenceKind.METADATA:
                raise GovernedDiscoveryError(
                    "metadata reader returned non-metadata evidence"
                )

            if evidence.payload.get("doctype") != item.target:
                raise GovernedDiscoveryError(
                    "metadata observation target does not match plan"
                )

        elif item.kind is DiscoveryTargetKind.RECORDS:
            if evidence.kind is not EvidenceKind.API:
                raise GovernedDiscoveryError(
                    "record reader returned non-API evidence"
                )

            if evidence.payload.get("resource") != item.target:
                raise GovernedDiscoveryError(
                    "record observation target does not match plan"
                )


def run_governed_discovery(
    plan: DiscoveryPlan,
    *,
    authorization: DiscoveryAuthorization,
    understanding: MetadataUnderstanding,
    metadata_reader: DiscoveryReader,
    record_reader: DiscoveryReader,
) -> GovernedDiscoveryReport:
    """Run an already-planned read-only discovery operation.

    Full plan validation occurs before any reader invocation.
    Returned observations are validated again before being exposed.
    Nothing is persisted or promoted by this component.
    """

    preflight_governed_discovery(
        plan,
        authorization=authorization,
        understanding=understanding,
    )

    results: list[GovernedDiscoveryItemResult] = []

    for item in plan.items:
        reader = (
            metadata_reader
            if item.kind is DiscoveryTargetKind.METADATA
            else record_reader
        )

        try:
            observations = tuple(reader(item.target))
        except Exception as exc:
            raise GovernedDiscoveryError(
                f"read-only discovery failed for {item.target}"
            ) from exc

        _validate_observations(
            item,
            observations,
            tenant_id=authorization.tenant_id,
        )

        results.append(
            GovernedDiscoveryItemResult(
                plan_item=item,
                observations=observations,
            )
        )

    return GovernedDiscoveryReport(
        tenant_id=authorization.tenant_id,
        items=tuple(results),
    )
