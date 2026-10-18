"""Minimal local terminal rendering for sanitized ORION activity."""

from __future__ import annotations

from typing import TextIO

from .activity import ActivityEvent


def render_activity_event(event: ActivityEvent) -> str:
    """Render one structural event without customer payloads or control affordances."""

    if not isinstance(event, ActivityEvent):
        raise TypeError("event must be ActivityEvent")
    target = "-"
    if event.entity is not None:
        target = event.entity
        if event.fields:
            target += "." + ",".join(event.fields)
    details = [
        event.occurred_at.isoformat(),
        event.event_type.value,
        f"run={event.run_id}",
        f"cycle={event.cycle if event.cycle is not None else '-'}",
        f"target={target}",
        f"reads={event.erp_reads}",
        f"writes={event.erp_writes}",
        f"batches={event.evidence_batches_appended}",
        "execution_allowed=false",
    ]
    if event.reason is not None:
        details.append(f"reason={event.reason}")
    if event.observations_acquired is not None:
        details.append(f"observations={event.observations_acquired}")
    if event.valid_count is not None:
        details.append(f"valid={event.valid_count}")
    if event.persistence_verified is not None:
        details.append(f"persisted_verified={str(event.persistence_verified).lower()}")
    return " | ".join(details)


class TerminalActivityWatcher:
    """Write activity lines to a local stream; exposes no commands or callbacks."""

    def __init__(self, stream: TextIO) -> None:
        if not callable(getattr(stream, "write", None)):
            raise TypeError("stream must provide write")
        self._stream = stream

    def __call__(self, event: ActivityEvent) -> None:
        self._stream.write(render_activity_event(event) + "\n")
        flush = getattr(self._stream, "flush", None)
        if callable(flush):
            flush()


__all__ = ["TerminalActivityWatcher", "render_activity_event"]
