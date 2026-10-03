"""Sanitized immutable activity events with no control authority."""

from __future__ import annotations

import math
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from uuid import UUID

_CODE_RE = re.compile(r"[a-z][a-z0-9_]*\Z")


class ActivityEventType(StrEnum):
    """Neutral semantic transitions suitable for an observation-only surface."""

    RUN_STARTED = "run_started"
    OBJECTIVE_LOADED = "objective_loaded"
    PROPOSAL_SELECTED = "proposal_selected"
    NEXT_PROPOSAL_SELECTED = "next_proposal_selected"
    PROPOSAL_REJECTED = "proposal_rejected"
    AUTHORIZATION_CHECKED = "authorization_checked"
    AUTHORIZATION_DENIED = "authorization_denied"
    READ_STARTED = "read_started"
    READ_COMPLETED = "read_completed"
    VALIDATION_COMPLETED = "validation_completed"
    EVIDENCE_SINK_STARTED = "evidence_sink_started"
    EVIDENCE_PERSISTED = "evidence_persisted"
    REASSESSMENT_STARTED = "reassessment_started"
    RUN_STOPPED = "run_stopped"
    SAFE_FAILURE = "safe_failure"


def _safe_label(value: object, name: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 128:
        raise ValueError(f"{name} must be a non-empty bounded string")
    if not value.isprintable() or any(character in value for character in "\r\n\x1b"):
        raise ValueError(f"{name} contains unsafe terminal characters")


def _optional_count(value: object, name: str) -> None:
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError(f"{name} must be a non-negative integer or None")


@dataclass(frozen=True, slots=True)
class ActivityEvent:
    """Aggregate structural activity safe for local observation by default."""

    event_type: ActivityEventType
    occurred_at: datetime
    run_id: UUID
    cycle: int | None = None
    study_kind: str | None = None
    entity: str | None = None
    fields: tuple[str, ...] = ()
    requested_records: int | None = None
    observations_acquired: int | None = None
    valid_count: int | None = None
    score: float | None = None
    score_components: tuple[tuple[str, float], ...] = ()
    reason: str | None = None
    erp_reads: int = 0
    evidence_batches_appended: int = 0
    persistence_verified: bool | None = None
    prediction_evaluated: bool | None = None
    erp_writes: int = field(default=0, init=False)
    recommendation_allowed: bool = field(default=False, init=False)
    promotion_allowed: bool = field(default=False, init=False)
    execution_allowed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        if not isinstance(self.event_type, ActivityEventType):
            raise TypeError("event_type must be ActivityEventType")
        if (
            not isinstance(self.occurred_at, datetime)
            or self.occurred_at.tzinfo is None
            or self.occurred_at.utcoffset() is None
        ):
            raise ValueError("occurred_at must be timezone-aware")
        if not isinstance(self.run_id, UUID):
            raise TypeError("run_id must be UUID")
        for name in (
            "cycle",
            "requested_records",
            "observations_acquired",
            "valid_count",
            "erp_reads",
            "evidence_batches_appended",
        ):
            _optional_count(getattr(self, name), name)
        for name in ("study_kind", "entity"):
            value = getattr(self, name)
            if value is not None:
                _safe_label(value, name)
        if not isinstance(self.fields, tuple) or len(self.fields) != len(set(self.fields)):
            raise ValueError("fields must be a unique tuple")
        for value in self.fields:
            _safe_label(value, "field")
        if self.score is not None and (
            not isinstance(self.score, (int, float))
            or isinstance(self.score, bool)
            or not math.isfinite(self.score)
        ):
            raise ValueError("score must be finite or None")
        if not isinstance(self.score_components, tuple):
            raise TypeError("score_components must be a tuple")
        names = []
        for name, value in self.score_components:
            _safe_label(name, "score component")
            if (
                not isinstance(value, (int, float))
                or isinstance(value, bool)
                or not math.isfinite(value)
            ):
                raise ValueError("score component must be finite")
            names.append(name)
        if len(names) != len(set(names)):
            raise ValueError("score components must be unique")
        if self.reason is not None and not _CODE_RE.fullmatch(self.reason):
            raise ValueError("reason must be a fixed safe category")
        for name in ("persistence_verified", "prediction_evaluated"):
            value = getattr(self, name)
            if value is not None and type(value) is not bool:
                raise TypeError(f"{name} must be bool or None")


ActivitySink = Callable[[ActivityEvent], None]

__all__ = ["ActivityEvent", "ActivityEventType", "ActivitySink"]
