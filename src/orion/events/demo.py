"""Offline demonstration of bounded event-driven learning intake."""

from __future__ import annotations

import json
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

from .contracts import EnterpriseEvent, EnterpriseEventKind, route_enterprise_event
from .queue import SQLiteEventQueue
from .worker import (
    EventWorkDisposition,
    EventWorkerBounds,
    EventWorkResult,
    run_event_worker,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _event(event_id: str, kind: EnterpriseEventKind) -> EnterpriseEvent:
    return EnterpriseEvent(
        event_id=event_id,
        tenant_id="synthetic-tenant",
        source="offline-demo",
        provenance_id=f"provenance-{event_id}",
        occurred_at=_NOW,
        received_at=_NOW,
        correlation_id="demo-correlation",
        kind=kind,
        subject="SyntheticEntity",
    )


def run_demo(database_path: Path) -> dict[str, object]:
    """Run two synthetic events without network, credentials, or external action."""

    queue = SQLiteEventQueue(database_path, max_pending_global=4, max_pending_per_tenant=4)
    for event in (
        _event("event-1", EnterpriseEventKind.RECORD_CHANGED),
        _event("event-2", EnterpriseEventKind.DRIFT_THRESHOLD_CROSSED),
    ):
        queue.enqueue(event, route_enterprise_event(event))

    def handler(event, trigger):
        disposition = (
            EventWorkDisposition.GOVERNED_STUDY_PROPOSED
            if trigger.authorization_required
            else EventWorkDisposition.OFFLINE_STATE_UPDATED
        )
        return EventWorkResult(event.event_id, event.tenant_id, disposition, 1)

    report = run_event_worker(
        queue,
        handler,
        EventWorkerBounds(max_claims=2, max_attempts_per_event=1),
        clock=lambda: _NOW,
    )
    result = asdict(report)
    result["queue"] = queue.state_counts()
    return result


def main() -> int:
    with TemporaryDirectory(prefix="orion-event-demo-") as directory:
        result = run_demo(Path(directory) / "events.sqlite3")
    if result["external_reads"] or result["external_writes"]:
        raise RuntimeError("offline event demo crossed its external boundary")
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
