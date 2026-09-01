"""Vendor-neutral persist-before-acknowledge historical sampling orchestration."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from ..contracts import Observation, utc_now
from ..discovery.planner import DiscoveryPlanError, validate_discovery_target
from .evidence import (
    HistoricalEvidenceBatch,
    HistoricalEvidenceError,
    HistoricalEvidenceStore,
)


class HistoricalSampleSource(Protocol):
    """A bounded source of already validated candidate observations."""

    def discover(self) -> tuple[Observation, ...]: ...


@dataclass(frozen=True, slots=True)
class PersistedHistoricalSample:
    """Evidence-persistence facts returned only after durable reload verification."""

    tenant_id: str
    resource: str
    sequence: int
    observation_count: int
    observation_ids: tuple[UUID, ...]
    evidence_ids: tuple[UUID, ...]


def persist_historical_sample(
    source: HistoricalSampleSource,
    store: HistoricalEvidenceStore,
    *,
    tenant_id: str,
    resource: str,
    clock: Callable[[], datetime] = utc_now,
) -> PersistedHistoricalSample:
    """Persist one sample and acknowledge it only after integrity-checked reload.

    The store is preflighted before discovery. No retry or resampling is performed;
    any failure propagates without returning an acknowledgement.
    """

    _validate_scope(tenant_id, resource)
    history = store.load_all(tenant_id=tenant_id, resource=resource)
    _validate_verified_history(history, tenant_id=tenant_id, resource=resource)
    expected_sequence = 1 if not history else history[-1].sequence + 1

    observations = source.discover()
    if not isinstance(observations, tuple):
        raise HistoricalEvidenceError("historical sample source must return a tuple")
    if not observations:
        raise HistoricalEvidenceError("historical sample must contain observations")

    batch = HistoricalEvidenceBatch(
        tenant_id=tenant_id,
        resource=resource,
        sequence=expected_sequence,
        created_at=clock(),
        observations=observations,
    )
    store.append(batch)

    reloaded = store.load_all(tenant_id=tenant_id, resource=resource)
    _validate_verified_history(reloaded, tenant_id=tenant_id, resource=resource)
    if len(reloaded) != len(history) + 1 or reloaded[-1] != batch:
        raise HistoricalEvidenceError(
            "historical sample reload did not verify the appended batch"
        )
    if reloaded[-1].sequence != expected_sequence:
        raise HistoricalEvidenceError(
            "historical sample reload sequence did not advance exactly once"
        )

    return PersistedHistoricalSample(
        tenant_id=tenant_id,
        resource=resource,
        sequence=batch.sequence,
        observation_count=len(batch.observations),
        observation_ids=tuple(item.observation_id for item in batch.observations),
        evidence_ids=tuple(item.evidence.evidence_id for item in batch.observations),
    )


def _validate_scope(tenant_id: str, resource: str) -> None:
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise HistoricalEvidenceError("tenant_id must be non-empty")
    if tenant_id != tenant_id.strip():
        raise HistoricalEvidenceError("tenant_id must not contain surrounding whitespace")
    try:
        validate_discovery_target(resource)
    except (DiscoveryPlanError, ValueError) as exc:
        raise HistoricalEvidenceError(str(exc)) from exc


def _validate_verified_history(
    history: object,
    *,
    tenant_id: str,
    resource: str,
) -> None:
    if not isinstance(history, tuple):
        raise HistoricalEvidenceError("historical evidence history must be a tuple")
    for expected_sequence, batch in enumerate(history, start=1):
        if not isinstance(batch, HistoricalEvidenceBatch):
            raise HistoricalEvidenceError("historical evidence history contains an invalid batch")
        if (
            batch.tenant_id != tenant_id
            or batch.resource != resource
            or batch.sequence != expected_sequence
        ):
            raise HistoricalEvidenceError("historical evidence history is not verified")
