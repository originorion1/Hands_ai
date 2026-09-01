"""Deterministic, descriptive projections over verified customer evidence."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from enum import StrEnum
from statistics import median
from typing import Any
from uuid import UUID

from ..discovery.planner import DiscoveryPlanError, validate_discovery_target
from ..history.evidence import HistoricalEvidenceBatch, HistoricalEvidenceError


class SampleSufficiency(StrEnum):
    """Conservative evidence volume labels, never automation authority."""

    INSUFFICIENT = "INSUFFICIENT"
    PRELIMINARY = "PRELIMINARY"
    SUPPORTED = "SUPPORTED"


@dataclass(frozen=True, slots=True)
class CustomerPatternSnapshot:
    """Immutable customer-scoped descriptive facts and their provenance."""

    tenant_id: str
    resource: str
    batch_sequences: tuple[int, ...]
    observation_count: int
    sample_sufficiency: SampleSufficiency
    distinct_supplier_count: int
    distinct_currency_count: int
    supplier_frequencies: tuple[tuple[str, int], ...]
    currency_frequencies: tuple[tuple[str, int], ...]
    amount_count: int
    amount_min: float | None
    amount_max: float | None
    amount_mean: float | None
    amount_median: float | None
    due_interval_count: int
    due_interval_min_days: int | None
    due_interval_max_days: int | None
    due_interval_median_days: float | None
    posting_date_min: str | None
    posting_date_max: str | None
    invalid_field_counts: tuple[tuple[str, int], ...]
    observation_ids: tuple[UUID, ...]
    evidence_ids: tuple[UUID, ...]


def project_customer_patterns(
    history: tuple[HistoricalEvidenceBatch, ...],
    *,
    tenant_id: str,
    resource: str,
) -> CustomerPatternSnapshot:
    """Project verified history into deterministic descriptive statistics."""

    _validate_scope(history, tenant_id=tenant_id, resource=resource)
    batches = history
    observations = tuple(item for batch in batches for item in batch.observations)
    observation_ids = tuple(dict.fromkeys(item.observation_id for item in observations))
    evidence_ids = tuple(dict.fromkeys(item.evidence.evidence_id for item in observations))

    suppliers: Counter[str] = Counter()
    currencies: Counter[str] = Counter()
    amounts: list[float] = []
    due_intervals: list[int] = []
    posting_dates: list[date] = []
    invalid: Counter[str] = Counter()

    for observation in observations:
        record = observation.evidence.payload["record"]
        _collect_text(record, "supplier", suppliers, invalid)
        _collect_text(record, "currency", currencies, invalid)
        amount = record.get("grand_total")
        if type(amount) in {int, float} and not isinstance(amount, bool):
            try:
                finite_amount = math.isfinite(float(amount))
            except (OverflowError, ValueError):
                finite_amount = False
            if finite_amount:
                amounts.append(float(amount))
                amount_valid = True
            else:
                amount_valid = False
        else:
            amount_valid = False
        if not amount_valid:
            invalid["grand_total"] += 1

        posting = _parse_date(record.get("posting_date"))
        due = _parse_date(record.get("due_date"))
        if posting is None:
            invalid["posting_date"] += 1
        else:
            posting_dates.append(posting)
        if due is None:
            invalid["due_date"] += 1
        if posting is not None and due is not None:
            interval = (due - posting).days
            if interval < 0:
                invalid["due_interval"] += 1
            else:
                due_intervals.append(interval)

    ordered_suppliers = _ordered_counts(suppliers)
    ordered_currencies = _ordered_counts(currencies)
    amount_stats = _stats(amounts)
    return CustomerPatternSnapshot(
        tenant_id=tenant_id,
        resource=resource,
        batch_sequences=tuple(batch.sequence for batch in batches),
        observation_count=len(observations),
        sample_sufficiency=_sufficiency(len(observations)),
        distinct_supplier_count=len(suppliers),
        distinct_currency_count=len(currencies),
        supplier_frequencies=ordered_suppliers,
        currency_frequencies=ordered_currencies,
        amount_count=len(amounts),
        amount_min=amount_stats[0],
        amount_max=amount_stats[1],
        amount_mean=amount_stats[2],
        amount_median=amount_stats[3],
        due_interval_count=len(due_intervals),
        due_interval_min_days=min(due_intervals) if due_intervals else None,
        due_interval_max_days=max(due_intervals) if due_intervals else None,
        due_interval_median_days=float(median(due_intervals)) if due_intervals else None,
        posting_date_min=min(posting_dates).isoformat() if posting_dates else None,
        posting_date_max=max(posting_dates).isoformat() if posting_dates else None,
        invalid_field_counts=tuple(sorted(invalid.items())),
        observation_ids=observation_ids,
        evidence_ids=evidence_ids,
    )


def format_safe_pattern_summary(snapshot: CustomerPatternSnapshot) -> dict[str, Any]:
    """Return aggregate operator facts without raw customer values or provenance IDs."""

    return {
        "resource": snapshot.resource,
        "observation_count": snapshot.observation_count,
        "batch_count": len(snapshot.batch_sequences),
        "sufficiency_state": snapshot.sample_sufficiency.value,
        "distinct_supplier_count": snapshot.distinct_supplier_count,
        "distinct_currency_count": snapshot.distinct_currency_count,
        "valid_amount_count": snapshot.amount_count,
        "valid_due_interval_count": snapshot.due_interval_count,
        "invalid_field_counts": snapshot.invalid_field_counts,
        "execution_allowed": False,
        "recommendation_allowed": False,
        "promotion_allowed": False,
    }


def _validate_scope(
    history: object,
    *,
    tenant_id: str,
    resource: str,
) -> None:
    if not isinstance(tenant_id, str) or not tenant_id.strip():
        raise HistoricalEvidenceError("tenant_id must be non-empty")
    if tenant_id != tenant_id.strip():
        raise HistoricalEvidenceError("tenant_id must not contain surrounding whitespace")
    try:
        validate_discovery_target(resource)
    except (DiscoveryPlanError, ValueError) as exc:
        raise HistoricalEvidenceError(str(exc)) from exc
    if not isinstance(history, tuple):
        raise HistoricalEvidenceError("history must be a tuple")
    document_names: set[str] = set()
    observation_ids: set[UUID] = set()
    evidence_ids: set[UUID] = set()
    for expected, batch in enumerate(history, start=1):
        if not isinstance(batch, HistoricalEvidenceBatch):
            raise HistoricalEvidenceError("history contains an invalid batch")
        if batch.tenant_id != tenant_id or batch.resource != resource or batch.sequence != expected:
            raise HistoricalEvidenceError("history crosses tenant/resource or sequence boundary")
        for observation in batch.observations:
            if observation.observation_id in observation_ids:
                raise HistoricalEvidenceError("duplicate observation UUID across history")
            observation_ids.add(observation.observation_id)
            evidence = observation.evidence
            if evidence.evidence_id in evidence_ids:
                raise HistoricalEvidenceError("duplicate evidence UUID across history")
            evidence_ids.add(evidence.evidence_id)
            record = evidence.payload["record"]
            name = record["name"]
            if name in document_names:
                raise HistoricalEvidenceError("duplicate document identity across history")
            document_names.add(name)


def _collect_text(
    record: Mapping[str, Any],
    field: str,
    counts: Counter[str],
    invalid: Counter[str],
) -> None:
    value = record.get(field)
    if isinstance(value, str) and value.strip() and value == value.strip():
        counts[value] += 1
    else:
        invalid[field] += 1


def _parse_date(value: Any) -> date | None:
    if not isinstance(value, str) or not value.strip() or value != value.strip():
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _stats(values: list[float]) -> tuple[float | None, float | None, float | None, float | None]:
    if not values:
        return None, None, None, None
    return min(values), max(values), sum(values) / len(values), float(median(values))


def _ordered_counts(counts: Counter[str]) -> tuple[tuple[str, int], ...]:
    return tuple(sorted(counts.items(), key=lambda item: (-item[1], item[0])))


def _sufficiency(count: int) -> SampleSufficiency:
    if count < 5:
        return SampleSufficiency.INSUFFICIENT
    if count < 20:
        return SampleSufficiency.PRELIMINARY
    return SampleSufficiency.SUPPORTED
