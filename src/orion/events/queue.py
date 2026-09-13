"""Small durable SQLite queue for neutral enterprise events."""

from __future__ import annotations

import hashlib
import hmac
import json
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from pathlib import Path
from uuid import uuid4

from .contracts import (
    EnterpriseEvent,
    EnterpriseEventKind,
    LearningTrigger,
    TriggerKind,
    route_enterprise_event,
)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS orion_enterprise_events (
    queue_id INTEGER PRIMARY KEY AUTOINCREMENT,
    idempotency_key TEXT NOT NULL UNIQUE,
    tenant_id TEXT NOT NULL,
    event_json TEXT NOT NULL,
    trigger_json TEXT NOT NULL,
    content_sha256 TEXT NOT NULL,
    priority INTEGER NOT NULL CHECK (priority BETWEEN 0 AND 100),
    occurred_at TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('pending', 'leased', 'completed', 'dead')),
    attempts INTEGER NOT NULL DEFAULT 0 CHECK (attempts >= 0),
    available_at TEXT NOT NULL,
    lease_token TEXT,
    leased_until TEXT
);
CREATE INDEX IF NOT EXISTS orion_event_claim_order
ON orion_enterprise_events(state, available_at, priority DESC, occurred_at, queue_id);
CREATE INDEX IF NOT EXISTS orion_event_tenant_state
ON orion_enterprise_events(tenant_id, state);
"""


class EventQueueConflictError(ValueError):
    """Raised when a stable event identity is replayed with different content."""


class EventQueueIntegrityError(ValueError):
    """Raised when durable event content fails its integrity check."""


class EnqueueDisposition(StrEnum):
    ACCEPTED = "accepted"
    DUPLICATE = "duplicate"
    TENANT_BACKPRESSURE = "tenant_backpressure"
    GLOBAL_BACKPRESSURE = "global_backpressure"


@dataclass(frozen=True, slots=True)
class EnqueueResult:
    disposition: EnqueueDisposition
    queue_id: int | None
    idempotency_key: str


@dataclass(frozen=True, slots=True)
class EventClaim:
    queue_id: int
    event: EnterpriseEvent
    trigger: LearningTrigger
    lease_token: str
    attempt: int


def _aware(value: object, label: str) -> datetime:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{label} must be timezone-aware")
    return value


def _timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat()


def _event_dict(event: EnterpriseEvent) -> dict[str, object]:
    return {
        "schema_version": event.schema_version,
        "event_id": event.event_id,
        "tenant_id": event.tenant_id,
        "source": event.source,
        "provenance_id": event.provenance_id,
        "occurred_at": _timestamp(event.occurred_at),
        "received_at": _timestamp(event.received_at),
        "correlation_id": event.correlation_id,
        "kind": event.kind.value,
        "subject": event.subject,
    }


def _trigger_dict(trigger: LearningTrigger) -> dict[str, object]:
    return {
        "event_id": trigger.event_id,
        "tenant_id": trigger.tenant_id,
        "correlation_id": trigger.correlation_id,
        "kind": trigger.kind.value,
        "priority": trigger.priority,
        "reasons": list(trigger.reasons),
        "authorization_required": trigger.authorization_required,
    }


def _canonical(value: dict[str, object]) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def _content_digest(event_json: str, trigger_json: str) -> str:
    return hashlib.sha256(f"{event_json}\n{trigger_json}".encode()).hexdigest()


def _restore_event(payload: str) -> EnterpriseEvent:
    value = json.loads(payload)
    return EnterpriseEvent(
        event_id=value["event_id"],
        tenant_id=value["tenant_id"],
        source=value["source"],
        provenance_id=value["provenance_id"],
        occurred_at=datetime.fromisoformat(value["occurred_at"]),
        received_at=datetime.fromisoformat(value["received_at"]),
        correlation_id=value["correlation_id"],
        kind=EnterpriseEventKind(value["kind"]),
        subject=value["subject"],
        schema_version=value["schema_version"],
    )


def _restore_trigger(payload: str) -> LearningTrigger:
    value = json.loads(payload)
    return LearningTrigger(
        event_id=value["event_id"],
        tenant_id=value["tenant_id"],
        correlation_id=value["correlation_id"],
        kind=TriggerKind(value["kind"]),
        priority=value["priority"],
        reasons=tuple(value["reasons"]),
        authorization_required=value["authorization_required"],
    )


class SQLiteEventQueue:
    """Transactional queue with durable dedupe and bounded active work."""

    def __init__(
        self,
        database_path: str | Path,
        *,
        max_pending_global: int = 1000,
        max_pending_per_tenant: int = 100,
    ) -> None:
        self._path = Path(database_path)
        for label, value in (
            ("max_pending_global", max_pending_global),
            ("max_pending_per_tenant", max_pending_per_tenant),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f"{label} must be a positive integer")
        if max_pending_per_tenant > max_pending_global:
            raise ValueError("per-tenant capacity cannot exceed global capacity")
        if not self._path.parent.exists() or self._path.is_dir():
            raise ValueError("event queue database path is invalid")
        self._max_pending_global = max_pending_global
        self._max_pending_per_tenant = max_pending_per_tenant
        connection = self._connect()
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            connection.executescript(_SCHEMA)
            connection.commit()
        finally:
            connection.close()

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._path, timeout=5.0)
        connection.execute("PRAGMA busy_timeout=5000")
        connection.execute("PRAGMA synchronous=FULL")
        return connection

    def enqueue(
        self,
        event: EnterpriseEvent,
        trigger: LearningTrigger,
    ) -> EnqueueResult:
        """Enqueue once, returning a neutral backpressure disposition when full."""

        if not isinstance(event, EnterpriseEvent):
            raise TypeError("event must be EnterpriseEvent")
        if not isinstance(trigger, LearningTrigger):
            raise TypeError("trigger must be LearningTrigger")
        if (
            trigger.event_id,
            trigger.tenant_id,
            trigger.correlation_id,
        ) != (event.event_id, event.tenant_id, event.correlation_id):
            raise ValueError("trigger identity does not match event")
        if trigger != route_enterprise_event(event):
            raise ValueError("trigger does not match deterministic event route")
        event_json = _canonical(_event_dict(event))
        trigger_json = _canonical(_trigger_dict(trigger))
        digest = _content_digest(event_json, trigger_json)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            existing = connection.execute(
                """
                SELECT queue_id, content_sha256
                FROM orion_enterprise_events
                WHERE idempotency_key = ?
                """,
                (event.idempotency_key,),
            ).fetchone()
            if existing is not None:
                queue_id, stored_digest = existing
                if not hmac.compare_digest(stored_digest, digest):
                    raise EventQueueConflictError(
                        "idempotency key already exists with different content"
                    )
                connection.commit()
                return EnqueueResult(
                    EnqueueDisposition.DUPLICATE,
                    queue_id,
                    event.idempotency_key,
                )
            global_active = connection.execute(
                "SELECT COUNT(*) FROM orion_enterprise_events "
                "WHERE state IN ('pending', 'leased')"
            ).fetchone()[0]
            if global_active >= self._max_pending_global:
                connection.commit()
                return EnqueueResult(
                    EnqueueDisposition.GLOBAL_BACKPRESSURE,
                    None,
                    event.idempotency_key,
                )
            tenant_active = connection.execute(
                "SELECT COUNT(*) FROM orion_enterprise_events "
                "WHERE tenant_id = ? AND state IN ('pending', 'leased')",
                (event.tenant_id,),
            ).fetchone()[0]
            if tenant_active >= self._max_pending_per_tenant:
                connection.commit()
                return EnqueueResult(
                    EnqueueDisposition.TENANT_BACKPRESSURE,
                    None,
                    event.idempotency_key,
                )
            cursor = connection.execute(
                """
                INSERT INTO orion_enterprise_events (
                    idempotency_key, tenant_id, event_json, trigger_json,
                    content_sha256, priority, occurred_at, state, available_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'pending', ?)
                """,
                (
                    event.idempotency_key,
                    event.tenant_id,
                    event_json,
                    trigger_json,
                    digest,
                    trigger.priority,
                    _timestamp(event.occurred_at),
                    _timestamp(event.received_at),
                ),
            )
            connection.commit()
            return EnqueueResult(
                EnqueueDisposition.ACCEPTED,
                cursor.lastrowid,
                event.idempotency_key,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def claim_next(
        self,
        *,
        now: datetime,
        lease_seconds: int,
        tenant_id: str | None = None,
    ) -> EventClaim | None:
        """Lease the highest-priority available event, recovering stale leases."""

        now = _aware(now, "now")
        if type(lease_seconds) is not int or lease_seconds < 1:
            raise ValueError("lease_seconds must be a positive integer")
        if tenant_id is not None and (not isinstance(tenant_id, str) or not tenant_id):
            raise ValueError("tenant_id must be non-empty or None")
        now_text = _timestamp(now)
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if tenant_id is None:
                connection.execute(
                    """
                    UPDATE orion_enterprise_events
                    SET state = 'pending', lease_token = NULL, leased_until = NULL
                    WHERE state = 'leased' AND leased_until <= ?
                    """,
                    (now_text,),
                )
            else:
                connection.execute(
                    """
                    UPDATE orion_enterprise_events
                    SET state = 'pending', lease_token = NULL, leased_until = NULL
                    WHERE state = 'leased' AND leased_until <= ? AND tenant_id = ?
                    """,
                    (now_text, tenant_id),
                )
            tenant_clause = "" if tenant_id is None else "AND tenant_id = ?"
            parameters = (now_text,) if tenant_id is None else (now_text, tenant_id)
            row = connection.execute(
                f"""
                SELECT queue_id, idempotency_key, tenant_id, event_json, trigger_json,
                       content_sha256, priority, occurred_at, attempts
                FROM orion_enterprise_events
                WHERE state = 'pending' AND available_at <= ? {tenant_clause}
                ORDER BY priority DESC, occurred_at ASC, queue_id ASC
                LIMIT 1
                """,
                parameters,
            ).fetchone()
            if row is None:
                connection.commit()
                return None
            (
                queue_id,
                stored_key,
                stored_tenant,
                event_json,
                trigger_json,
                stored_digest,
                stored_priority,
                stored_occurred_at,
                attempts,
            ) = row
            actual_digest = _content_digest(event_json, trigger_json)
            if not hmac.compare_digest(stored_digest, actual_digest):
                raise EventQueueIntegrityError("event queue integrity check failed")
            event = _restore_event(event_json)
            trigger = _restore_trigger(trigger_json)
            if (
                stored_key != event.idempotency_key
                or stored_tenant != event.tenant_id
                or stored_priority != trigger.priority
                or stored_occurred_at != _timestamp(event.occurred_at)
                or trigger != route_enterprise_event(event)
            ):
                raise EventQueueIntegrityError("event queue envelope integrity check failed")
            lease_token = str(uuid4())
            leased_until = _timestamp(now + timedelta(seconds=lease_seconds))
            connection.execute(
                """
                UPDATE orion_enterprise_events
                SET state = 'leased', attempts = attempts + 1,
                    lease_token = ?, leased_until = ?
                WHERE queue_id = ? AND state = 'pending'
                """,
                (lease_token, leased_until, queue_id),
            )
            connection.commit()
            return EventClaim(
                queue_id=queue_id,
                event=event,
                trigger=trigger,
                lease_token=lease_token,
                attempt=attempts + 1,
            )
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def acknowledge(self, claim: EventClaim) -> None:
        """Complete exactly the currently leased claim."""

        self._transition(claim, state="completed", available_at=None)

    def release(
        self,
        claim: EventClaim,
        *,
        available_at: datetime,
        dead: bool = False,
    ) -> None:
        """Release a failed claim for bounded retry or mark it dead."""

        available_at = _aware(available_at, "available_at")
        self._transition(
            claim,
            state="dead" if dead else "pending",
            available_at=_timestamp(available_at),
        )

    def _transition(
        self,
        claim: EventClaim,
        *,
        state: str,
        available_at: str | None,
    ) -> None:
        if not isinstance(claim, EventClaim):
            raise TypeError("claim must be EventClaim")
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            if available_at is None:
                cursor = connection.execute(
                    """
                    UPDATE orion_enterprise_events
                    SET state = ?, lease_token = NULL, leased_until = NULL
                    WHERE queue_id = ? AND state = 'leased' AND lease_token = ?
                    """,
                    (state, claim.queue_id, claim.lease_token),
                )
            else:
                cursor = connection.execute(
                    """
                    UPDATE orion_enterprise_events
                    SET state = ?, available_at = ?, lease_token = NULL, leased_until = NULL
                    WHERE queue_id = ? AND state = 'leased' AND lease_token = ?
                    """,
                    (state, available_at, claim.queue_id, claim.lease_token),
                )
            if cursor.rowcount != 1:
                raise EventQueueConflictError("event lease is stale or already resolved")
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def state_counts(self, *, tenant_id: str | None = None) -> dict[str, int]:
        """Return aggregate queue state only, optionally scoped to one tenant."""

        if tenant_id is not None and (not isinstance(tenant_id, str) or not tenant_id):
            raise ValueError("tenant_id must be non-empty or None")
        connection = self._connect()
        try:
            if tenant_id is None:
                rows = connection.execute(
                    "SELECT state, COUNT(*) FROM orion_enterprise_events GROUP BY state"
                ).fetchall()
            else:
                rows = connection.execute(
                    "SELECT state, COUNT(*) FROM orion_enterprise_events "
                    "WHERE tenant_id = ? GROUP BY state",
                    (tenant_id,),
                ).fetchall()
        finally:
            connection.close()
        counts = {state: 0 for state in ("pending", "leased", "completed", "dead")}
        counts.update(rows)
        return counts


__all__ = [
    "EnqueueDisposition",
    "EnqueueResult",
    "EventClaim",
    "EventQueueConflictError",
    "EventQueueIntegrityError",
    "SQLiteEventQueue",
]
