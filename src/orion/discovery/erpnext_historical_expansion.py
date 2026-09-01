"""Overlap-safe, one-window ERPNext historical evidence expansion."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from ..contracts import Observation, utc_now
from ..history.evidence import HistoricalEvidenceBatch, HistoricalEvidenceError
from ..history.sampling import persist_historical_sample
from ..stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore
from .erpnext_historical_capture import (
    FIRST_CAPTURE_FIELDS,
    PURCHASE_INVOICE_RESOURCE,
    _ensure_state_directory,
    _reject_repository_destination,
    default_historical_evidence_path,
)
from .erpnext_historical_sample import ERPNextHistoricalSampleAdapter

EXPANSION_WINDOW_SIZE = 25
EXPANSION_MAX_NEW_OBSERVATIONS = 20


@dataclass(frozen=True, slots=True)
class ERPNextHistoricalExpansionConfig:
    """In-memory runtime configuration for one bounded expansion."""

    base_url: str
    tenant_id: str
    company: str
    api_key: str = field(repr=False)
    api_secret: str = field(repr=False)

    def __post_init__(self) -> None:
        for name in ("base_url", "tenant_id", "company", "api_key", "api_secret"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} must be non-empty")
        if self.tenant_id != self.tenant_id.strip():
            raise ValueError("tenant_id must not contain surrounding whitespace")
        if self.company != self.company.strip():
            raise ValueError("company must not contain surrounding whitespace")


@dataclass(frozen=True, slots=True)
class ERPNextHistoricalExpansionSummary:
    """Safe aggregate facts returned after durable expansion verification."""

    tenant_bound: bool
    resource: str
    previous_batch_count: int
    previous_observation_count: int
    remote_window_count: int
    skipped_known_count: int
    new_observation_count: int
    new_batch_sequence: int
    durable_batch_count_after: int
    durable_observation_count_after: int
    persisted: bool
    overlap_rejected: bool
    duplicate_history: bool
    submitted_only: bool
    company_scope_valid: bool
    erp_writes: int
    recommendation_allowed: bool
    promotion_allowed: bool
    execution_allowed: bool


class _UnseenHistoricalSource:
    def __init__(self, source: ERPNextHistoricalSampleAdapter, known_names: set[str]) -> None:
        self._source = source
        self._known_names = known_names
        self.remote_window_count = 0
        self.skipped_known_count = 0
        self.new_observation_count = 0

    def discover(self) -> tuple[Observation, ...]:
        observations = self._source.discover()
        self.remote_window_count = len(observations)
        unseen: list[Observation] = []
        for observation in observations:
            name = observation.evidence.payload["record"]["name"]
            if name in self._known_names:
                self.skipped_known_count += 1
                continue
            unseen.append(observation)
        capped = tuple(unseen[:EXPANSION_MAX_NEW_OBSERVATIONS])
        self.new_observation_count = len(capped)
        return capped


def expand_erpnext_historical_sample(
    config: ERPNextHistoricalExpansionConfig,
    *,
    database_path: str | Path | None = None,
    first_expansion: bool = True,
    opener: Callable[..., Any] | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> ERPNextHistoricalExpansionSummary:
    """Persist up to 20 unseen observations from exactly one remote window."""

    if not isinstance(config, ERPNextHistoricalExpansionConfig):
        raise TypeError("config must be ERPNextHistoricalExpansionConfig")
    path = Path(database_path) if database_path is not None else default_historical_evidence_path(config.tenant_id)
    _reject_repository_destination(path)
    if database_path is None:
        _ensure_state_directory(path.parent)

    store = SQLiteHistoricalEvidenceStore(path)
    history = store.load_all(tenant_id=config.tenant_id, resource=PURCHASE_INVOICE_RESOURCE)
    # Existing history is part of the trust boundary: never append to a
    # database whose document, observation, or evidence identities already
    # overlap across batches.
    _validate_history_unique(history, config.tenant_id)
    if first_expansion and not _is_verified_first_state(history):
        raise HistoricalEvidenceError(
            "first historical expansion requires exactly one five-observation batch"
        )

    known_names = {
        observation.evidence.payload["record"]["name"]
        for batch in history
        for observation in batch.observations
    }
    sampler = ERPNextHistoricalSampleAdapter(
        base_url=config.base_url,
        tenant_id=config.tenant_id,
        api_key=config.api_key,
        api_secret=config.api_secret,
        resource=PURCHASE_INVOICE_RESOURCE,
        company=config.company,
        fields=FIRST_CAPTURE_FIELDS,
        sample_size=EXPANSION_WINDOW_SIZE,
        opener=opener,
    )
    source = _UnseenHistoricalSource(sampler, known_names)
    acknowledgement = persist_historical_sample(
        source,
        store,
        tenant_id=config.tenant_id,
        resource=PURCHASE_INVOICE_RESOURCE,
        clock=clock,
    )
    reloaded = store.load_all(tenant_id=config.tenant_id, resource=PURCHASE_INVOICE_RESOURCE)
    if len(reloaded) != len(history) + 1:
        raise HistoricalEvidenceError("historical expansion durable history length mismatch")
    _validate_history_unique(reloaded, config.tenant_id)

    return ERPNextHistoricalExpansionSummary(
        tenant_bound=True,
        resource=PURCHASE_INVOICE_RESOURCE,
        previous_batch_count=len(history),
        previous_observation_count=sum(len(batch.observations) for batch in history),
        remote_window_count=source.remote_window_count,
        skipped_known_count=source.skipped_known_count,
        new_observation_count=acknowledgement.observation_count,
        new_batch_sequence=acknowledgement.sequence,
        durable_batch_count_after=len(reloaded),
        durable_observation_count_after=sum(len(batch.observations) for batch in reloaded),
        persisted=True,
        overlap_rejected=True,
        duplicate_history=False,
        submitted_only=True,
        company_scope_valid=True,
        erp_writes=0,
        recommendation_allowed=False,
        promotion_allowed=False,
        execution_allowed=False,
    )


def _is_verified_first_state(history: tuple[HistoricalEvidenceBatch, ...]) -> bool:
    return len(history) == 1 and history[0].sequence == 1 and len(history[0].observations) == 5


def _validate_history_unique(history: tuple[HistoricalEvidenceBatch, ...], tenant_id: str) -> None:
    names: set[str] = set()
    observation_ids: set[UUID] = set()
    evidence_ids: set[UUID] = set()
    for expected_sequence, batch in enumerate(history, start=1):
        if batch.tenant_id != tenant_id or batch.resource != PURCHASE_INVOICE_RESOURCE or batch.sequence != expected_sequence:
            raise HistoricalEvidenceError("historical expansion history is not verified")
        for observation in batch.observations:
            name = observation.evidence.payload["record"]["name"]
            if name in names:
                raise HistoricalEvidenceError("historical expansion produced duplicate document identity")
            if observation.observation_id in observation_ids:
                raise HistoricalEvidenceError("historical expansion produced duplicate observation UUID")
            if observation.evidence.evidence_id in evidence_ids:
                raise HistoricalEvidenceError("historical expansion produced duplicate evidence UUID")
            names.add(name)
            observation_ids.add(observation.observation_id)
            evidence_ids.add(observation.evidence.evidence_id)
