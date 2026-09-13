import sqlite3
from dataclasses import FrozenInstanceError, replace
from datetime import UTC, datetime, timedelta

import pytest

from orion.events import (
    EnqueueDisposition,
    EnterpriseEvent,
    EnterpriseEventKind,
    EventQueueConflictError,
    EventQueueIntegrityError,
    EventWorkDisposition,
    EventWorkerBounds,
    EventWorkResult,
    SQLiteEventQueue,
    TriggerKind,
    route_enterprise_event,
    run_event_worker,
)
from orion.events.demo import run_demo

NOW = datetime(2026, 1, 1, tzinfo=UTC)


def event(
    event_id="event-1",
    *,
    tenant="tenant-a",
    source="source-a",
    kind=EnterpriseEventKind.RECORD_CHANGED,
    occurred_at=NOW,
    correlation="correlation-1",
):
    return EnterpriseEvent(
        event_id=event_id,
        tenant_id=tenant,
        source=source,
        provenance_id=f"provenance-{event_id}",
        occurred_at=occurred_at,
        received_at=max(NOW, occurred_at),
        correlation_id=correlation,
        kind=kind,
        subject="Entity.field",
    )


def queue(tmp_path, *, global_capacity=100, tenant_capacity=10):
    return SQLiteEventQueue(
        tmp_path / "events.sqlite3",
        max_pending_global=global_capacity,
        max_pending_per_tenant=tenant_capacity,
    )


def enqueue(target, item):
    return target.enqueue(item, route_enterprise_event(item))


def test_event_is_immutable_stably_identified_and_has_no_authority():
    item = event()
    assert item.idempotency_key.startswith("v1:")
    assert len(item.idempotency_key) == 67
    assert item.idempotency_key == event().idempotency_key
    assert item.recommendation_allowed is False
    assert item.promotion_allowed is False
    assert item.execution_allowed is False
    assert "payload" not in EnterpriseEvent.__dataclass_fields__
    assert "authorization" not in EnterpriseEvent.__dataclass_fields__
    with pytest.raises(FrozenInstanceError):
        item.execution_allowed = True


def test_event_requires_complete_aware_structural_identity():
    with pytest.raises(ValueError, match="timezone-aware"):
        EnterpriseEvent(
            event_id="event-1",
            tenant_id="tenant-a",
            source="source-a",
            provenance_id="provenance-1",
            occurred_at=NOW.replace(tzinfo=None),
            received_at=NOW,
            correlation_id="correlation-1",
            kind=EnterpriseEventKind.RECORD_CHANGED,
            subject="Entity",
        )
    with pytest.raises(ValueError, match="bounded stable identity"):
        event(tenant="unsafe tenant")


@pytest.mark.parametrize(
    ("kind", "trigger_kind", "priority", "authorization_required"),
    (
        (EnterpriseEventKind.AUTHORITY_CHANGED, TriggerKind.POLICY_REASSESSMENT, 100, False),
        (
            EnterpriseEventKind.METADATA_CHANGED,
            TriggerKind.STRUCTURAL_REASSESSMENT,
            95,
            False,
        ),
        (
            EnterpriseEventKind.PREDICTION_OUTCOME_OBSERVED,
            TriggerKind.OFFLINE_LEARNING,
            85,
            False,
        ),
        (
            EnterpriseEventKind.DRIFT_THRESHOLD_CROSSED,
            TriggerKind.GOVERNED_STUDY_CANDIDATE,
            80,
            True,
        ),
        (EnterpriseEventKind.RECORD_CHANGED, TriggerKind.OFFLINE_LEARNING, 60, False),
    ),
)
def test_routing_is_deterministic_and_never_grants_authority(
    kind, trigger_kind, priority, authorization_required
):
    item = event(kind=kind)
    first = route_enterprise_event(item)
    assert first == route_enterprise_event(item)
    assert first.kind is trigger_kind
    assert first.priority == priority
    assert first.authorization_required is authorization_required
    assert first.authorization_granted is False
    assert first.execution_allowed is False


def test_exact_replay_is_deduplicated_even_after_completion(tmp_path):
    target = queue(tmp_path)
    item = event()
    accepted = enqueue(target, item)
    duplicate = enqueue(target, item)
    assert accepted.disposition is EnqueueDisposition.ACCEPTED
    assert duplicate == type(duplicate)(
        EnqueueDisposition.DUPLICATE,
        accepted.queue_id,
        item.idempotency_key,
    )
    claim = target.claim_next(now=NOW, lease_seconds=10)
    target.acknowledge(claim)
    assert enqueue(target, item).disposition is EnqueueDisposition.DUPLICATE
    assert target.state_counts()["completed"] == 1


def test_changed_replay_with_same_stable_identity_fails_closed(tmp_path):
    target = queue(tmp_path)
    enqueue(target, event(correlation="correlation-1"))
    with pytest.raises(EventQueueConflictError, match="different content"):
        enqueue(target, event(correlation="correlation-2"))


def test_queue_rejects_adapter_supplied_priority_or_route_override(tmp_path):
    target = queue(tmp_path)
    item = event()
    canonical = route_enterprise_event(item)
    with pytest.raises(ValueError, match="deterministic"):
        target.enqueue(item, replace(canonical, priority=99))


def test_queue_prioritizes_information_class_then_event_time(tmp_path):
    target = queue(tmp_path)
    enqueue(target, event("record", occurred_at=NOW - timedelta(minutes=1)))
    enqueue(
        target,
        event("drift", kind=EnterpriseEventKind.DRIFT_THRESHOLD_CROSSED),
    )
    enqueue(
        target,
        event(
            "authority",
            kind=EnterpriseEventKind.AUTHORITY_CHANGED,
            occurred_at=NOW + timedelta(minutes=1),
        ),
    )
    assert target.claim_next(now=NOW + timedelta(minutes=2), lease_seconds=10).event.event_id == (
        "authority"
    )
    assert target.claim_next(now=NOW + timedelta(minutes=2), lease_seconds=10).event.event_id == (
        "drift"
    )


def test_per_tenant_and_global_backpressure_bound_event_storms(tmp_path):
    target = queue(tmp_path, global_capacity=3, tenant_capacity=2)
    assert enqueue(target, event("a-1")).disposition is EnqueueDisposition.ACCEPTED
    assert enqueue(target, event("a-2")).disposition is EnqueueDisposition.ACCEPTED
    assert (
        enqueue(target, event("a-3")).disposition
        is EnqueueDisposition.TENANT_BACKPRESSURE
    )
    assert enqueue(target, event("b-1", tenant="tenant-b")).disposition is (
        EnqueueDisposition.ACCEPTED
    )
    assert enqueue(target, event("c-1", tenant="tenant-c")).disposition is (
        EnqueueDisposition.GLOBAL_BACKPRESSURE
    )
    assert target.state_counts()["pending"] == 3


def test_tenant_scoped_claim_cannot_take_another_tenants_event(tmp_path):
    target = queue(tmp_path)
    enqueue(target, event("a-1"))
    enqueue(target, event("b-1", tenant="tenant-b"))
    claimed = target.claim_next(now=NOW, lease_seconds=10, tenant_id="tenant-b")
    assert claimed.event.tenant_id == "tenant-b"
    assert target.state_counts(tenant_id="tenant-a")["pending"] == 1


def test_expired_lease_is_recovered_and_stale_claim_cannot_acknowledge(tmp_path):
    target = queue(tmp_path)
    enqueue(target, event())
    stale = target.claim_next(now=NOW, lease_seconds=10)
    recovered = target.claim_next(now=NOW + timedelta(seconds=11), lease_seconds=10)
    assert recovered.event == stale.event
    assert recovered.attempt == 2
    with pytest.raises(EventQueueConflictError, match="stale"):
        target.acknowledge(stale)
    target.acknowledge(recovered)


def test_tenant_worker_recovers_only_its_own_stale_leases(tmp_path):
    target = queue(tmp_path)
    enqueue(target, event("a-1"))
    enqueue(target, event("b-1", tenant="tenant-b"))
    target.claim_next(now=NOW, lease_seconds=10, tenant_id="tenant-a")
    target.claim_next(now=NOW, lease_seconds=10, tenant_id="tenant-b")
    recovered = target.claim_next(
        now=NOW + timedelta(seconds=11),
        lease_seconds=10,
        tenant_id="tenant-b",
    )
    assert recovered.event.tenant_id == "tenant-b"
    assert target.state_counts(tenant_id="tenant-a")["leased"] == 1


def test_queue_detects_durable_content_tampering(tmp_path):
    database = tmp_path / "events.sqlite3"
    target = SQLiteEventQueue(database)
    enqueue(target, event())
    connection = sqlite3.connect(database)
    connection.execute(
        "UPDATE orion_enterprise_events SET event_json = ?",
        ('{"event_id":"tampered"}',),
    )
    connection.commit()
    connection.close()
    with pytest.raises(EventQueueIntegrityError, match="integrity"):
        target.claim_next(now=NOW, lease_seconds=10)


def test_queue_detects_durable_envelope_tampering(tmp_path):
    database = tmp_path / "events.sqlite3"
    target = SQLiteEventQueue(database)
    enqueue(target, event())
    connection = sqlite3.connect(database)
    connection.execute("UPDATE orion_enterprise_events SET priority = 100")
    connection.commit()
    connection.close()
    with pytest.raises(EventQueueIntegrityError, match="envelope"):
        target.claim_next(now=NOW, lease_seconds=10)


def test_worker_processes_bounded_events_and_only_proposes_governed_study(tmp_path):
    target = queue(tmp_path)
    enqueue(target, event("record"))
    enqueue(target, event("drift", kind=EnterpriseEventKind.DRIFT_THRESHOLD_CROSSED))
    seen = []

    def handler(item, trigger):
        seen.append((item.event_id, trigger.authorization_granted))
        disposition = (
            EventWorkDisposition.GOVERNED_STUDY_PROPOSED
            if trigger.authorization_required
            else EventWorkDisposition.OFFLINE_STATE_UPDATED
        )
        return EventWorkResult(item.event_id, item.tenant_id, disposition, 1)

    report = run_event_worker(
        target,
        handler,
        EventWorkerBounds(max_claims=2, max_attempts_per_event=1),
        clock=lambda: NOW,
    )
    assert seen == [("drift", False), ("record", False)]
    assert report.completed == 2
    assert report.offline_updates == report.governed_study_candidates == 1
    assert report.evidence_updates == 2
    assert report.external_reads == report.external_writes == 0
    assert report.authorization_granted is report.execution_allowed is False


def test_worker_retries_then_dead_letters_without_exceeding_bounds(tmp_path):
    target = queue(tmp_path)
    enqueue(target, event())
    attempts = []

    def broken(item, trigger):
        attempts.append((item.event_id, trigger.event_id))
        raise RuntimeError("synthetic failure")

    report = run_event_worker(
        target,
        broken,
        EventWorkerBounds(
            max_claims=5,
            max_attempts_per_event=2,
            retry_delay_seconds=0,
        ),
        clock=lambda: NOW,
    )
    assert len(attempts) == 2
    assert report.claims == report.failed_attempts == 2
    assert report.requeued == report.dead_lettered == 1
    assert report.queue_exhausted is True
    assert target.state_counts()["dead"] == 1


def test_worker_claim_limit_leaves_remaining_work_durable(tmp_path):
    target = queue(tmp_path)
    for index in range(3):
        enqueue(target, event(f"event-{index}"))

    def handler(item, _trigger):
        return EventWorkResult(
            item.event_id,
            item.tenant_id,
            EventWorkDisposition.OFFLINE_STATE_UPDATED,
        )

    report = run_event_worker(
        target,
        handler,
        EventWorkerBounds(max_claims=2, max_attempts_per_event=1),
        clock=lambda: NOW,
    )
    assert report.claims == report.completed == 2
    assert report.queue_exhausted is False
    assert target.state_counts() == {
        "pending": 1,
        "leased": 0,
        "completed": 2,
        "dead": 0,
    }


def test_offline_demo_completes_without_external_authority(tmp_path):
    result = run_demo(tmp_path / "demo.sqlite3")
    assert result["completed"] == 2
    assert result["external_reads"] == result["external_writes"] == 0
    assert result["authorization_granted"] is result["execution_allowed"] is False
    assert result["queue"]["completed"] == 2
