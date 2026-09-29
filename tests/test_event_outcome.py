from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from orion.events import (
    EnterpriseEvent,
    EnterpriseEventKind,
    EventWorkerBounds,
    SQLiteEventQueue,
    route_enterprise_event,
    run_event_worker,
)
from orion.learning.event_outcome import OutcomeEventHandler, OutcomeReceipt, run_demo
from orion.learning.prediction_ledger import Outcome, Prediction, PredictionLedger

NOW = datetime(2026, 1, 1, tzinfo=UTC)
LATER = NOW + timedelta(days=1)
REF = (UUID('00000000-0000-4000-8000-000000000001'),)


def setup(tmp_path):
    ledger = PredictionLedger(tmp_path / 'ledger.db', clock=lambda: NOW)
    ledger.record(Prediction('tenant-a', 'p1', 'target-v1', 'model-v1', NOW, NOW,
                             LATER, 0.8, REF))
    ledger = PredictionLedger(tmp_path / 'ledger.db', clock=lambda: LATER)
    event = EnterpriseEvent('event-1', 'tenant-a', 'fixture', 'receipt-1', LATER,
                            LATER, 'correlation-1',
                            EnterpriseEventKind.PREDICTION_OUTCOME_OBSERVED, 'target-v1')
    outcome = Outcome('tenant-a', 'p1', LATER, True, REF)
    return ledger, event, outcome


def test_queue_resolution_survives_failure_after_commit_before_ack(tmp_path, monkeypatch):
    ledger, event, outcome = setup(tmp_path)
    receipt = OutcomeReceipt(event, outcome)
    calls = []

    def lookup(tenant, key):
        calls.append((tenant, key))
        return receipt

    handler = OutcomeEventHandler(ledger, lookup)
    queue = SQLiteEventQueue(tmp_path / 'queue.db')
    queue.enqueue(event, route_enterprise_event(event))
    ack = queue.acknowledge

    def interrupted(claim):
        raise RuntimeError('synthetic acknowledgement interruption')

    monkeypatch.setattr(queue, 'acknowledge', interrupted)
    bounds = EventWorkerBounds(1, 2, retry_delay_seconds=0)
    first = run_event_worker(queue, handler, bounds, clock=lambda: LATER)
    assert first.requeued == 1
    assert ledger.score('tenant-a')['resolved'] == 1
    monkeypatch.setattr(queue, 'acknowledge', ack)
    queue = SQLiteEventQueue(tmp_path / 'queue.db')
    second = run_event_worker(queue, handler, bounds, clock=lambda: LATER)
    assert second.completed == 1
    assert second.evidence_updates == 0
    assert not second.execution_allowed and not second.authorization_granted
    assert ledger.score('tenant-a')['resolved'] == 1
    assert ledger.score('tenant-a')['brier'] == pytest.approx(0.04)
    assert calls == [('tenant-a', event.idempotency_key)] * 2
    assert queue.state_counts()['completed'] == 1


@pytest.mark.parametrize('change', ['missing', 'tenant', 'envelope', 'early', 'future', 'unknown'])
def test_invalid_receipt_never_resolves_prediction(tmp_path, change):
    ledger, event, outcome = setup(tmp_path)
    receipt_event = event
    if change == 'tenant':
        outcome = replace(outcome, tenant_id='tenant-b')
    elif change == 'envelope':
        receipt_event = replace(event, provenance_id='different-receipt')
    elif change == 'early':
        outcome = replace(outcome, observed_at=NOW)
    elif change == 'future':
        outcome = replace(outcome, observed_at=LATER + timedelta(seconds=1))
    elif change == 'unknown':
        outcome = replace(outcome, prediction_id='missing-prediction')
    receipt = None if change == 'missing' else OutcomeReceipt(receipt_event, outcome)
    handler = OutcomeEventHandler(ledger, lambda tenant, key: receipt)
    with pytest.raises(ValueError):
        handler(event, route_enterprise_event(event))
    assert ledger.score('tenant-a')['pending'] == 1
    assert ledger.score('tenant-b')['resolved'] == 0


@pytest.mark.parametrize('change', ['kind', 'route', 'type'])
def test_bad_notification_rejected_before_lookup(tmp_path, change):
    ledger, event, _ = setup(tmp_path)
    trigger = route_enterprise_event(event)
    if change == 'kind':
        event = replace(event, kind=EnterpriseEventKind.RECORD_CHANGED)
        trigger = route_enterprise_event(event)
    elif change == 'route':
        trigger = replace(trigger, priority=1)
    else:
        event = object()

    def forbidden(*args):
        pytest.fail('invalid notification reached trusted lookup')

    with pytest.raises((ValueError, TypeError)):
        OutcomeEventHandler(ledger, forbidden)(event, trigger)
    assert ledger.score('tenant-a')['pending'] == 1


def test_conflicting_outcome_preserves_original_score(tmp_path):
    ledger, event, outcome = setup(tmp_path)
    receipt = OutcomeReceipt(event, outcome)
    handler = OutcomeEventHandler(ledger, lambda tenant, key: receipt)
    handler(event, route_enterprise_event(event))
    receipt = OutcomeReceipt(event, replace(outcome, actual=False))
    with pytest.raises(ValueError, match='conflict'):
        handler(event, route_enterprise_event(event))
    assert ledger.score('tenant-a')['brier'] == pytest.approx(0.04)


def test_initiation_demo_is_repeatable_and_explicitly_synthetic():
    report = run_demo()
    assert report == run_demo()
    assert report['data_source'] == 'synthetic-fixture'
    assert report['worker']['completed'] == 1
    assert report['worker']['evidence_updates'] == 0
    assert report['ledger']['resolved'] == 1
    assert report['ledger']['brier'] == pytest.approx(0.04)
    assert report['execution_allowed'] is False
