"""Resolve already validated offline outcomes when their bound event arrives.

The lookup is a trusted application port, not a validator or network adapter.
It must authenticate evidence and enforce tenant access before returning a
receipt. This module verifies binding and delegates measurement to the ledger.
"""

from collections.abc import Callable
from dataclasses import asdict, dataclass

from ..events import (
    EnterpriseEvent,
    EnterpriseEventKind,
    EventWorkDisposition,
    EventWorkResult,
    LearningTrigger,
    route_enterprise_event,
)
from .prediction_ledger import Outcome, PredictionLedger


@dataclass(frozen=True, slots=True)
class OutcomeReceipt:
    """Exact notification binding supplied by the trusted offline evidence store.

    Constructing this value does not itself authenticate its evidence.
    """

    event: EnterpriseEvent
    outcome: Outcome

    def __post_init__(self) -> None:
        if not isinstance(self.event, EnterpriseEvent) or not isinstance(self.outcome, Outcome):
            raise TypeError('receipt requires EnterpriseEvent and Outcome')


OutcomeLookup = Callable[[str, str], OutcomeReceipt | None]


class OutcomeEventHandler:
    """A bounded worker handler; no new evidence, prediction or authority."""

    def __init__(self, ledger: PredictionLedger, lookup: OutcomeLookup) -> None:
        if not isinstance(ledger, PredictionLedger) or not callable(lookup):
            raise TypeError('ledger and callable trusted lookup are required')
        self._ledger = ledger
        self._lookup = lookup

    def __call__(self, event: EnterpriseEvent, trigger: LearningTrigger) -> EventWorkResult:
        if not isinstance(event, EnterpriseEvent):
            raise TypeError('event must be EnterpriseEvent')
        if event.kind is not EnterpriseEventKind.PREDICTION_OUTCOME_OBSERVED:
            raise ValueError('handler accepts only outcome notifications')
        if trigger != route_enterprise_event(event):
            raise ValueError('trigger must match the deterministic event route')
        receipt = self._lookup(event.tenant_id, event.idempotency_key)
        if receipt is None:
            raise ValueError('trusted outcome receipt unavailable')
        if not isinstance(receipt, OutcomeReceipt):
            raise TypeError('lookup must return OutcomeReceipt or None')
        if receipt.event != event or receipt.outcome.tenant_id != event.tenant_id:
            raise ValueError('outcome receipt does not match event and tenant')
        # resolve() owns temporal checks, tenant prediction lookup, immutable
        # conflict rejection and exact replay. Reuse it after acknowledgement loss.
        self._ledger.resolve(receipt.outcome)
        return EventWorkResult(event.event_id, event.tenant_id,
                               EventWorkDisposition.OFFLINE_STATE_UPDATED)


def run_demo() -> dict[str, object]:
    """Exercise the real queue/handler/ledger with a temporary synthetic receipt."""
    from datetime import UTC, datetime, timedelta
    from pathlib import Path
    from tempfile import TemporaryDirectory
    from uuid import UUID

    from ..events import EventWorkerBounds, SQLiteEventQueue, run_event_worker
    from .prediction_ledger import Prediction

    start = datetime(2026, 1, 1, tzinfo=UTC)
    end = start + timedelta(days=1)
    refs = (UUID('00000000-0000-4000-8000-000000000001'),)
    outcome_refs = (UUID('00000000-0000-4000-8000-000000000002'),)
    tenant = 'synthetic-restaurant'
    event = EnterpriseEvent('event-1', tenant, 'fixture', 'receipt-1', end, end,
                            'correlation-1', EnterpriseEventKind.PREDICTION_OUTCOME_OBSERVED,
                            'synthetic-stockout-v1')
    receipt = OutcomeReceipt(event, Outcome(tenant, 'p1', end, True, outcome_refs))
    receipts = {(tenant, event.idempotency_key): receipt}
    with TemporaryDirectory(prefix='orion-outcome-initiation-') as directory:
        path = Path(directory)
        ledger = PredictionLedger(path / 'ledger.db', clock=lambda: start)
        ledger.record(Prediction(tenant, 'p1', event.subject, 'fixture-v1',
                                 start, start, end, 0.8, refs))
        ledger = PredictionLedger(path / 'ledger.db', clock=lambda: end)
        queue = SQLiteEventQueue(path / 'queue.db')
        queue.enqueue(event, route_enterprise_event(event))
        handler = OutcomeEventHandler(ledger, lambda tenant, key: receipts.get((tenant, key)))
        report = run_event_worker(queue, handler, EventWorkerBounds(1, 1), clock=lambda: end)
        return {'data_source': 'synthetic-fixture', 'worker': asdict(report),
                'ledger': ledger.score(tenant), 'execution_allowed': False}


if __name__ == '__main__':
    import json

    print(json.dumps(run_demo(), indent=2, sort_keys=True))
