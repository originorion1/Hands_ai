"""Single-use, bounded Purchase Invoice corpus expansion."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from ..contracts import Observation, utc_now
from ..history.evidence import HistoricalEvidenceError
from ..history.sampling import persist_historical_sample
from ..stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore
from .erpnext_historical_capture import (
    FIRST_CAPTURE_FIELDS,
    PURCHASE_INVOICE_RESOURCE,
    _ensure_state_directory,
    _reject_repository_destination,
    default_historical_evidence_path,
)
from .erpnext_historical_expansion import (
    ERPNextHistoricalExpansionConfig,
    _validate_history_unique,
)
from .erpnext_historical_sample import ERPNextHistoricalSampleAdapter

CORPUS_WINDOW_SIZE = 100
CORPUS_MAX_NEW_OBSERVATIONS = 75


class _ERPNextPurchaseInvoiceCorpusAdapter(ERPNextHistoricalSampleAdapter):
    """Private, non-repurposable fixed Purchase Invoice transport profile."""

    def __init__(self, *, base_url: str, tenant_id: str, api_key: str, api_secret: str, company: str, opener: Callable[..., Any] | None) -> None:
        super().__init__(
            base_url=base_url,
            tenant_id=tenant_id,
            api_key=api_key,
            api_secret=api_secret,
            resource=PURCHASE_INVOICE_RESOURCE,
            company=company,
            fields=FIRST_CAPTURE_FIELDS,
            sample_size=25,
            order_by="posting_date desc, name desc",
            opener=opener,
        )
        self._sample_size = CORPUS_WINDOW_SIZE


@dataclass(frozen=True, slots=True)
class ERPNextHistoricalCorpusExpansionSummary:
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


class _UnseenCorpusSource:
    def __init__(self, source: _ERPNextPurchaseInvoiceCorpusAdapter, known_names: set[str]) -> None:
        self._source = source
        self._known_names = known_names
        self.remote_window_count = 0
        self.skipped_known_count = 0

    def discover(self) -> tuple[Observation, ...]:
        observations = self._source.discover()
        self.remote_window_count = len(observations)
        unseen: list[Observation] = []
        for observation in observations:
            name = observation.evidence.payload["record"]["name"]
            if name in self._known_names:
                self.skipped_known_count += 1
            else:
                unseen.append(observation)
        return tuple(unseen[:CORPUS_MAX_NEW_OBSERVATIONS])


def expand_erpnext_historical_corpus(
    config: ERPNextHistoricalExpansionConfig,
    *,
    database_path: str | Path | None = None,
    opener: Callable[..., Any] | None = None,
    clock: Callable[[], datetime] = utc_now,
) -> ERPNextHistoricalCorpusExpansionSummary:
    """Perform exactly one bounded expansion from the verified 25-row state."""

    if not isinstance(config, ERPNextHistoricalExpansionConfig):
        raise TypeError("config must be ERPNextHistoricalExpansionConfig")
    path = Path(database_path) if database_path is not None else default_historical_evidence_path(config.tenant_id)
    _reject_repository_destination(path)
    if database_path is None:
        _ensure_state_directory(path.parent)
    store = SQLiteHistoricalEvidenceStore(path)
    history = store.load_all(tenant_id=config.tenant_id, resource=PURCHASE_INVOICE_RESOURCE)
    _validate_history_unique(history, config.tenant_id)
    if len(history) != 2 or tuple(batch.sequence for batch in history) != (1, 2) or sum(len(batch.observations) for batch in history) != 25:
        raise HistoricalEvidenceError("corpus expansion requires exactly two batches and 25 observations")
    known_names = {observation.evidence.payload["record"]["name"] for batch in history for observation in batch.observations}
    sampler = _ERPNextPurchaseInvoiceCorpusAdapter(
        base_url=config.base_url,
        tenant_id=config.tenant_id,
        api_key=config.api_key,
        api_secret=config.api_secret,
        company=config.company,
        opener=opener,
    )
    source = _UnseenCorpusSource(sampler, known_names)
    acknowledgement = persist_historical_sample(
        source, store, tenant_id=config.tenant_id, resource=PURCHASE_INVOICE_RESOURCE, clock=clock
    )
    reloaded = store.load_all(tenant_id=config.tenant_id, resource=PURCHASE_INVOICE_RESOURCE)
    _validate_history_unique(reloaded, config.tenant_id)
    if len(reloaded) != 3 or acknowledgement.sequence != 3 or sum(len(batch.observations) for batch in reloaded) > 100:
        raise HistoricalEvidenceError("corpus expansion durable history verification failed")
    return ERPNextHistoricalCorpusExpansionSummary(
        tenant_bound=True,
        resource=PURCHASE_INVOICE_RESOURCE,
        previous_batch_count=2,
        previous_observation_count=25,
        remote_window_count=source.remote_window_count,
        skipped_known_count=source.skipped_known_count,
        new_observation_count=acknowledgement.observation_count,
        new_batch_sequence=3,
        durable_batch_count_after=3,
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
