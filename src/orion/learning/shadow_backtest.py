"""Vendor-neutral chronological shadow evaluation over verified evidence."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import date
from statistics import median
from typing import Any
from uuid import UUID

from ..contracts import EvidenceKind, ObservationMode
from ..history.evidence import HistoricalEvidenceBatch, HistoricalEvidenceError


@dataclass(frozen=True, slots=True)
class ShadowBacktestResult:
    """Immutable, customer-scoped evaluation facts; never an authority grant."""

    tenant_id: str
    resource: str
    observation_count: int
    batch_count: int
    evaluated_target_count: int
    chronological_target_count: int
    currency_prediction_count: int
    currency_abstention_count: int
    currency_correct_count: int
    currency_accuracy: float | None
    due_interval_prediction_count: int
    due_interval_abstention_count: int
    due_interval_absolute_errors: tuple[float, ...]
    due_interval_mean_absolute_error: float | None
    due_interval_median_absolute_error: float | None
    due_interval_exact_count: int
    due_interval_within_3_days_count: int
    due_interval_within_7_days_count: int
    invalid_field_counts: tuple[tuple[str, int], ...]
    target_observation_ids: tuple[UUID, ...]


def run_shadow_backtest(
    history: tuple[HistoricalEvidenceBatch, ...],
    *,
    tenant_id: str,
    resource: str,
) -> ShadowBacktestResult:
    """Evaluate currency and due intervals using strictly earlier dates only."""

    _validate_history(history, tenant_id=tenant_id, resource=resource)
    observations = tuple(item for batch in history for item in batch.observations)
    invalid: Counter[str] = Counter()
    parsed: list[tuple[Any, date | None, dict[str, Any]]] = []
    for observation in observations:
        record = observation.evidence.payload["record"]
        posting = _parse_date(record.get("posting_date"))
        due = _parse_date(record.get("due_date"))
        if posting is None:
            invalid["posting_date"] += 1
        if due is None:
            invalid["due_date"] += 1
        if not _valid_text(record.get("supplier")):
            invalid["supplier"] += 1
        if not _valid_text(record.get("currency")):
            invalid["currency"] += 1
        if posting is not None and due is not None and due < posting:
            invalid["due_interval"] += 1
        parsed.append((observation, posting, record))

    chronological = sorted(
        (item for item in parsed if item[1] is not None),
        key=lambda item: (item[1], str(item[0].observation_id)),
    )
    currency_predictions = currency_abstentions = currency_correct = 0
    due_predictions = due_abstentions = due_exact = due_3 = due_7 = 0
    errors: list[float] = []
    target_ids: list[UUID] = []

    for target, target_date, target_record in chronological:
        assert target_date is not None
        target_ids.append(target.observation_id)
        prior = [
            (observation, posting, record)
            for observation, posting, record in chronological
            if posting < target_date
            and _valid_text(record.get("supplier"))
            and record.get("supplier") == target_record.get("supplier")
        ]
        currency_values = [record["currency"] for _, _, record in prior if _valid_text(record.get("currency"))]
        if len(currency_values) < 2:
            currency_abstentions += 1
        else:
            counts = Counter(currency_values)
            top = counts.most_common()
            if len(top) == 1 or top[0][1] > top[1][1]:
                currency_predictions += 1
                currency_correct += int(top[0][0] == target_record.get("currency"))
            else:
                currency_abstentions += 1

        prior_intervals = [
            (due_date, posting)
            for _, posting, record in prior
            if posting is not None
            and (due_date := _parse_date(record.get("due_date"))) is not None
            and due_date >= posting
        ]
        if len(prior_intervals) < 2:
            due_abstentions += 1
        else:
            target_due = _parse_date(target_record.get("due_date"))
            if target_due is None or target_due < target_date:
                due_abstentions += 1
            else:
                prediction = float(median((due - posting).days for due, posting in prior_intervals))
                error = abs((target_due - target_date).days - prediction)
                errors.append(error)
                due_predictions += 1
                due_exact += int(error == 0)
                due_3 += int(error <= 3)
                due_7 += int(error <= 7)

    return ShadowBacktestResult(
        tenant_id=tenant_id,
        resource=resource,
        observation_count=len(observations),
        batch_count=len(history),
        evaluated_target_count=len(observations),
        chronological_target_count=len(chronological),
        currency_prediction_count=currency_predictions,
        currency_abstention_count=currency_abstentions,
        currency_correct_count=currency_correct,
        currency_accuracy=(currency_correct / currency_predictions if currency_predictions else None),
        due_interval_prediction_count=due_predictions,
        due_interval_abstention_count=due_abstentions,
        due_interval_absolute_errors=tuple(errors),
        due_interval_mean_absolute_error=(sum(errors) / len(errors) if errors else None),
        due_interval_median_absolute_error=(float(median(errors)) if errors else None),
        due_interval_exact_count=due_exact,
        due_interval_within_3_days_count=due_3,
        due_interval_within_7_days_count=due_7,
        invalid_field_counts=tuple(sorted(invalid.items())),
        target_observation_ids=tuple(target_ids),
    )


def format_safe_shadow_summary(result: ShadowBacktestResult) -> dict[str, Any]:
    """Expose aggregate evaluation only; omit all customer values and IDs."""

    return {
        "resource": result.resource,
        "observation_count": result.observation_count,
        "batch_count": result.batch_count,
        "currency_prediction_count": result.currency_prediction_count,
        "currency_abstention_count": result.currency_abstention_count,
        "currency_correct_count": result.currency_correct_count,
        "currency_accuracy": result.currency_accuracy,
        "due_interval_prediction_count": result.due_interval_prediction_count,
        "due_interval_abstention_count": result.due_interval_abstention_count,
        "due_interval_mean_absolute_error": result.due_interval_mean_absolute_error,
        "due_interval_median_absolute_error": result.due_interval_median_absolute_error,
        "due_interval_exact_count": result.due_interval_exact_count,
        "due_interval_within_3_days_count": result.due_interval_within_3_days_count,
        "due_interval_within_7_days_count": result.due_interval_within_7_days_count,
        "invalid_field_counts": result.invalid_field_counts,
        "shadow_only": True,
        "recommendation_allowed": False,
        "promotion_allowed": False,
        "execution_allowed": False,
    }


def _validate_history(history: object, *, tenant_id: str, resource: str) -> None:
    if not isinstance(history, tuple) or not tenant_id.strip() or tenant_id != tenant_id.strip():
        raise HistoricalEvidenceError("invalid shadow backtest scope")
    names: set[str] = set()
    observation_ids: set[UUID] = set()
    evidence_ids: set[UUID] = set()
    for expected, batch in enumerate(history, start=1):
        if not isinstance(batch, HistoricalEvidenceBatch) or batch.tenant_id != tenant_id or batch.resource != resource or batch.sequence != expected:
            raise HistoricalEvidenceError("shadow backtest history scope or sequence is invalid")
        for observation in batch.observations:
            if observation.mode is not ObservationMode.READ_ONLY or observation.evidence.kind is not EvidenceKind.API:
                raise HistoricalEvidenceError("shadow backtest requires read-only API evidence")
            if observation.evidence.tenant_id != tenant_id:
                raise HistoricalEvidenceError("shadow backtest evidence tenant is invalid")
            payload = observation.evidence.payload
            if set(payload) != {"resource", "record"} or payload["resource"] != resource or not isinstance(payload["record"], dict):
                raise HistoricalEvidenceError("shadow backtest payload contract is invalid")
            name = payload["record"].get("name")
            if not _valid_text(name) or name in names:
                raise HistoricalEvidenceError("duplicate or invalid shadow backtest document identity")
            if observation.observation_id in observation_ids or observation.evidence.evidence_id in evidence_ids:
                raise HistoricalEvidenceError("duplicate shadow backtest identity")
            names.add(name)
            observation_ids.add(observation.observation_id)
            evidence_ids.add(observation.evidence.evidence_id)


def _valid_text(value: Any) -> bool:
    return isinstance(value, str) and bool(value.strip()) and value == value.strip()


def _parse_date(value: Any) -> date | None:
    if not _valid_text(value):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None
