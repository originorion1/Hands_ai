"""Finite worker loop for queued continuous-learning triggers."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum

from ..contracts import utc_now
from .contracts import EnterpriseEvent, LearningTrigger
from .queue import SQLiteEventQueue


class EventWorkDisposition(StrEnum):
    OFFLINE_STATE_UPDATED = "offline_state_updated"
    GOVERNED_STUDY_PROPOSED = "governed_study_proposed"
    IGNORED = "ignored"


@dataclass(frozen=True, slots=True)
class EventWorkResult:
    event_id: str
    tenant_id: str
    disposition: EventWorkDisposition
    evidence_updates: int = 0
    authorization_granted: bool = field(default=False, init=False)
    execution_allowed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.event_id, str) or not self.event_id:
            raise ValueError("event_id must be non-empty")
        if not isinstance(self.tenant_id, str) or not self.tenant_id:
            raise ValueError("tenant_id must be non-empty")
        if not isinstance(self.disposition, EventWorkDisposition):
            raise TypeError("disposition must be EventWorkDisposition")
        if type(self.evidence_updates) is not int or self.evidence_updates < 0:
            raise ValueError("evidence_updates must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class EventWorkerBounds:
    max_claims: int
    max_attempts_per_event: int
    lease_seconds: int = 60
    retry_delay_seconds: int = 1

    def __post_init__(self) -> None:
        for label in ("max_claims", "max_attempts_per_event", "lease_seconds"):
            value = getattr(self, label)
            if type(value) is not int or value < 1:
                raise ValueError(f"{label} must be a positive integer")
        if type(self.retry_delay_seconds) is not int or self.retry_delay_seconds < 0:
            raise ValueError("retry_delay_seconds must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class EventWorkerReport:
    claims: int
    completed: int
    failed_attempts: int
    requeued: int
    dead_lettered: int
    offline_updates: int
    governed_study_candidates: int
    ignored: int
    evidence_updates: int
    queue_exhausted: bool
    external_reads: int = field(default=0, init=False)
    external_writes: int = field(default=0, init=False)
    authorization_granted: bool = field(default=False, init=False)
    execution_allowed: bool = field(default=False, init=False)


EventHandler = Callable[[EnterpriseEvent, LearningTrigger], EventWorkResult]


def _clock_value(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value


def run_event_worker(
    queue: SQLiteEventQueue,
    handler: EventHandler,
    bounds: EventWorkerBounds,
    *,
    clock: Callable[[], datetime] = utc_now,
    tenant_id: str | None = None,
) -> EventWorkerReport:
    """Process finite queued work; event receipt never grants external authority."""

    if not isinstance(queue, SQLiteEventQueue):
        raise TypeError("queue must be SQLiteEventQueue")
    if not callable(handler):
        raise TypeError("handler must be callable")
    if not isinstance(bounds, EventWorkerBounds):
        raise TypeError("bounds must be EventWorkerBounds")
    if not callable(clock):
        raise TypeError("clock must be callable")
    claims = completed = failed = requeued = dead = 0
    offline = candidates = ignored = evidence_updates = 0
    exhausted = False
    while claims < bounds.max_claims:
        now = _clock_value(clock)
        claim = queue.claim_next(
            now=now,
            lease_seconds=bounds.lease_seconds,
            tenant_id=tenant_id,
        )
        if claim is None:
            exhausted = True
            break
        claims += 1
        # A crashed worker may consume the last attempt without releasing its
        # lease. Recovery must not invoke the handler beyond the current budget.
        if claim.attempt > bounds.max_attempts_per_event:
            queue.release(claim, available_at=now, dead=True)
            dead += 1
            continue
        try:
            result = handler(claim.event, claim.trigger)
            if not isinstance(result, EventWorkResult):
                raise TypeError("handler must return EventWorkResult")
            if (result.event_id, result.tenant_id) != (
                claim.event.event_id,
                claim.event.tenant_id,
            ):
                raise ValueError("handler result identity does not match event")
            queue.acknowledge(claim)
        except Exception:  # noqa: BLE001 - one event failure must not stop the worker
            failed += 1
            retry_at = _clock_value(clock) + timedelta(
                seconds=bounds.retry_delay_seconds
            )
            is_dead = claim.attempt >= bounds.max_attempts_per_event
            queue.release(claim, available_at=retry_at, dead=is_dead)
            if is_dead:
                dead += 1
            else:
                requeued += 1
            continue
        completed += 1
        evidence_updates += result.evidence_updates
        if result.disposition is EventWorkDisposition.OFFLINE_STATE_UPDATED:
            offline += 1
        elif result.disposition is EventWorkDisposition.GOVERNED_STUDY_PROPOSED:
            candidates += 1
        else:
            ignored += 1
    return EventWorkerReport(
        claims=claims,
        completed=completed,
        failed_attempts=failed,
        requeued=requeued,
        dead_lettered=dead,
        offline_updates=offline,
        governed_study_candidates=candidates,
        ignored=ignored,
        evidence_updates=evidence_updates,
        queue_exhausted=exhausted,
    )


__all__ = [
    "EventHandler",
    "EventWorkDisposition",
    "EventWorkResult",
    "EventWorkerBounds",
    "EventWorkerReport",
    "run_event_worker",
]
