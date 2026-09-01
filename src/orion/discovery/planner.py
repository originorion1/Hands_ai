"""Fail-closed planning for ORION's next read-only discovery targets.

The planner recommends what ORION may study next. It never performs discovery,
grants authorization, promotes knowledge, or executes actions.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from ..understanding.metadata import MetadataUnderstanding


class DiscoveryPlanError(ValueError):
    """Raised when a discovery plan would violate a safety boundary."""


class DiscoveryTargetKind(StrEnum):
    METADATA = "metadata"
    RECORDS = "records"


@dataclass(frozen=True, slots=True)
class DiscoveryAuthorization:
    """Explicit tenant-scoped permission envelope for discovery planning."""

    tenant_id: str
    metadata_targets: frozenset[str] = frozenset()
    record_targets: frozenset[str] = frozenset()
    max_targets: int = 10

    def __post_init__(self) -> None:
        if not isinstance(self.tenant_id, str) or not self.tenant_id.strip():
            raise DiscoveryPlanError("tenant_id must be non-empty")

        if type(self.max_targets) is not int or not 1 <= self.max_targets <= 100:
            raise DiscoveryPlanError(
                "max_targets must be between 1 and 100"
            )

        for target in self.metadata_targets | self.record_targets:
            _validate_target(target)


@dataclass(frozen=True, slots=True)
class DiscoveryPlanItem:
    kind: DiscoveryTargetKind
    target: str
    rationale: str
    provenance_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class DiscoveryPlan:
    tenant_id: str
    items: tuple[DiscoveryPlanItem, ...]

    @property
    def is_empty(self) -> bool:
        return not self.items


def _validate_target(target: str) -> None:
    if not isinstance(target, str) or not target.strip():
        raise DiscoveryPlanError(
            "discovery target must be non-empty"
        )

    if target != target.strip():
        raise DiscoveryPlanError(
            "discovery target must not contain surrounding whitespace"
        )

    if "*" in target:
        raise DiscoveryPlanError(
            "wildcard discovery authorization is forbidden"
        )

    if any(character in target for character in "/?#"):
        raise DiscoveryPlanError(
            "discovery target must be a simple resource name"
        )

    if any(ord(character) < 32 for character in target):
        raise DiscoveryPlanError(
            "discovery target must not contain control characters"
        )


def _merge_provenance(
    current: tuple[UUID, ...],
    additional: tuple[UUID, ...],
) -> tuple[UUID, ...]:
    return tuple(dict.fromkeys(current + additional))


def plan_authorized_discovery(
    understanding: MetadataUnderstanding,
    *,
    authorization: DiscoveryAuthorization,
    already_sampled_records: frozenset[str] = frozenset(),
) -> DiscoveryPlan:
    """Produce a deterministic bounded plan inside explicit authorization.

    Structural metadata is planned before record sampling. A record resource
    can only be proposed after its metadata is already understood.
    """

    if understanding.tenant_id != authorization.tenant_id:
        raise DiscoveryPlanError(
            "discovery authorization crosses tenant boundary"
        )

    for target in already_sampled_records:
        _validate_target(target)

    understood = {
        entity.doctype: entity
        for entity in understanding.entities
    }

    referenced: dict[str, tuple[UUID, ...]] = {}

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

            _validate_target(target)

            if target in understood:
                continue

            if target not in authorization.metadata_targets:
                continue

            referenced[target] = _merge_provenance(
                referenced.get(target, ()),
                entity.provenance_ids,
            )

    items: list[DiscoveryPlanItem] = []

    # Expand structure before sampling business records.
    for target in sorted(referenced):
        items.append(
            DiscoveryPlanItem(
                kind=DiscoveryTargetKind.METADATA,
                target=target,
                rationale=(
                    "authorized structural reference discovered in "
                    "observed metadata"
                ),
                provenance_ids=referenced[target],
            )
        )

    for target in sorted(authorization.record_targets):
        entity = understood.get(target)

        if entity is None:
            continue

        if target in already_sampled_records:
            continue

        items.append(
            DiscoveryPlanItem(
                kind=DiscoveryTargetKind.RECORDS,
                target=target,
                rationale=(
                    "authorized record sampling for an already "
                    "understood structure"
                ),
                provenance_ids=entity.provenance_ids,
            )
        )

    return DiscoveryPlan(
        tenant_id=understanding.tenant_id,
        items=tuple(items[: authorization.max_targets]),
    )
