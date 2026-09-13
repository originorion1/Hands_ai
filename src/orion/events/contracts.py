"""Immutable neutral contracts for waking ORION from enterprise events."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

_IDENTITY_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/-]{0,254}\Z")


class EnterpriseEventKind(StrEnum):
    RECORD_CHANGED = "record_changed"
    RECORD_SUBMITTED = "record_submitted"
    RECORD_CANCELLED = "record_cancelled"
    LINKED_ENTITY_CHANGED = "linked_entity_changed"
    DOCUMENT_ARRIVED = "document_arrived"
    MESSAGE_ARRIVED = "message_arrived"
    WORKFLOW_TRANSITIONED = "workflow_transitioned"
    OPERATIONAL_SIGNAL = "operational_signal"
    METADATA_CHANGED = "metadata_changed"
    CONFIGURATION_CHANGED = "configuration_changed"
    KNOWLEDGE_UPDATED = "knowledge_updated"
    PREDICTION_OUTCOME_OBSERVED = "prediction_outcome_observed"
    ANOMALY_DETECTED = "anomaly_detected"
    DRIFT_THRESHOLD_CROSSED = "drift_threshold_crossed"
    AUTHORITY_CHANGED = "authority_changed"


class TriggerKind(StrEnum):
    OFFLINE_LEARNING = "offline_learning"
    STRUCTURAL_REASSESSMENT = "structural_reassessment"
    POLICY_REASSESSMENT = "policy_reassessment"
    GOVERNED_STUDY_CANDIDATE = "governed_study_candidate"


def _identity(value: object, label: str) -> None:
    if not isinstance(value, str) or _IDENTITY_RE.fullmatch(value) is None:
        raise ValueError(f"{label} must be a bounded stable identity")


def _aware(value: object, label: str) -> None:
    if (
        not isinstance(value, datetime)
        or value.tzinfo is None
        or value.utcoffset() is None
    ):
        raise ValueError(f"{label} must be timezone-aware")


@dataclass(frozen=True, slots=True)
class EnterpriseEvent:
    """A structural event notification; it is neither truth nor authorization."""

    event_id: str
    tenant_id: str
    source: str
    provenance_id: str
    occurred_at: datetime
    received_at: datetime
    correlation_id: str
    kind: EnterpriseEventKind
    subject: str
    schema_version: int = 1
    recommendation_allowed: bool = field(default=False, init=False)
    promotion_allowed: bool = field(default=False, init=False)
    execution_allowed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        for label in (
            "event_id",
            "tenant_id",
            "source",
            "provenance_id",
            "correlation_id",
            "subject",
        ):
            _identity(getattr(self, label), label)
        _aware(self.occurred_at, "occurred_at")
        _aware(self.received_at, "received_at")
        if self.received_at < self.occurred_at:
            raise ValueError("received_at cannot precede occurred_at")
        if not isinstance(self.kind, EnterpriseEventKind):
            raise TypeError("kind must be EnterpriseEventKind")
        if self.schema_version != 1:
            raise ValueError("unsupported event schema version")

    @property
    def idempotency_key(self) -> str:
        """Return the source-scoped stable key used for durable deduplication."""

        canonical_identity = json.dumps(
            [self.tenant_id, self.source, self.event_id],
            ensure_ascii=False,
            separators=(",", ":"),
        )
        return "v1:" + hashlib.sha256(canonical_identity.encode()).hexdigest()


@dataclass(frozen=True, slots=True)
class LearningTrigger:
    """Deterministic work routing without credentials or authority expansion."""

    event_id: str
    tenant_id: str
    correlation_id: str
    kind: TriggerKind
    priority: int
    reasons: tuple[str, ...]
    authorization_required: bool
    authorization_granted: bool = field(default=False, init=False)
    recommendation_allowed: bool = field(default=False, init=False)
    promotion_allowed: bool = field(default=False, init=False)
    execution_allowed: bool = field(default=False, init=False)

    def __post_init__(self) -> None:
        for label in ("event_id", "tenant_id", "correlation_id"):
            _identity(getattr(self, label), label)
        if not isinstance(self.kind, TriggerKind):
            raise TypeError("kind must be TriggerKind")
        if type(self.priority) is not int or not 0 <= self.priority <= 100:
            raise ValueError("priority must be an integer in [0, 100]")
        if not self.reasons or len(self.reasons) != len(set(self.reasons)):
            raise ValueError("reasons must be a non-empty unique tuple")
        for reason in self.reasons:
            _identity(reason, "reason")
        if type(self.authorization_required) is not bool:
            raise TypeError("authorization_required must be bool")


_ROUTES = {
    EnterpriseEventKind.AUTHORITY_CHANGED: (
        TriggerKind.POLICY_REASSESSMENT,
        100,
        ("authority_boundary_changed",),
    ),
    EnterpriseEventKind.METADATA_CHANGED: (
        TriggerKind.STRUCTURAL_REASSESSMENT,
        95,
        ("structural_contract_changed",),
    ),
    EnterpriseEventKind.CONFIGURATION_CHANGED: (
        TriggerKind.STRUCTURAL_REASSESSMENT,
        90,
        ("configuration_changed",),
    ),
    EnterpriseEventKind.PREDICTION_OUTCOME_OBSERVED: (
        TriggerKind.OFFLINE_LEARNING,
        85,
        ("prediction_outcome_available",),
    ),
    EnterpriseEventKind.DRIFT_THRESHOLD_CROSSED: (
        TriggerKind.GOVERNED_STUDY_CANDIDATE,
        80,
        ("drift_requires_evidence",),
    ),
    EnterpriseEventKind.ANOMALY_DETECTED: (
        TriggerKind.GOVERNED_STUDY_CANDIDATE,
        75,
        ("anomaly_requires_evidence",),
    ),
    EnterpriseEventKind.KNOWLEDGE_UPDATED: (
        TriggerKind.OFFLINE_LEARNING,
        70,
        ("certified_knowledge_changed",),
    ),
}


def route_enterprise_event(event: EnterpriseEvent) -> LearningTrigger:
    """Classify an event deterministically without inspecting a customer payload."""

    if not isinstance(event, EnterpriseEvent):
        raise TypeError("event must be EnterpriseEvent")
    kind, priority, reasons = _ROUTES.get(
        event.kind,
        (
            TriggerKind.OFFLINE_LEARNING,
            60,
            ("new_governed_evidence_signal",),
        ),
    )
    return LearningTrigger(
        event_id=event.event_id,
        tenant_id=event.tenant_id,
        correlation_id=event.correlation_id,
        kind=kind,
        priority=priority,
        reasons=reasons,
        authorization_required=kind is TriggerKind.GOVERNED_STUDY_CANDIDATE,
    )


__all__ = [
    "EnterpriseEvent",
    "EnterpriseEventKind",
    "LearningTrigger",
    "TriggerKind",
    "route_enterprise_event",
]
