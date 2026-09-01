"""Stable, ERP-neutral contracts used by the ORION kernel."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import StrEnum
from typing import Any, Mapping
from uuid import UUID, uuid4


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class EvidenceKind(StrEnum):
    SYSTEM_OBSERVATION = "system_observation"
    METADATA = "metadata"
    DOCUMENTATION = "documentation"
    SOURCE = "source"
    CONFIGURATION = "configuration"
    API = "api"
    UI = "ui"
    EVENT = "event"
    LOG = "log"
    RUNTIME = "runtime"
    TRANSACTION = "transaction"
    EXPERIMENT = "experiment"
    HUMAN_OBSERVATION = "human_observation"


class ObservationMode(StrEnum):
    READ_ONLY = "read_only"
    SHADOW = "shadow"


@dataclass(frozen=True, slots=True)
class Evidence:
    kind: EvidenceKind
    source: str
    payload: Mapping[str, Any]
    observed_at: datetime = field(default_factory=utc_now)
    evidence_id: UUID = field(default_factory=uuid4)
    tenant_id: str | None = None
    confidence: float | None = None


@dataclass(frozen=True, slots=True)
class Observation:
    evidence: Evidence
    mode: ObservationMode = ObservationMode.READ_ONLY
    observation_id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True, slots=True)
class ProposedAction:
    capability_id: str
    operation: str
    arguments: Mapping[str, Any]
    rationale: str
    action_id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True, slots=True)
class Outcome:
    status: str
    values: Mapping[str, Any]
    outcome_id: UUID = field(default_factory=uuid4)


@dataclass(frozen=True, slots=True)
class ShadowResult:
    proposed_actions: tuple[ProposedAction, ...]
    predicted: Outcome
    reference: Outcome | None
    classification: str
    evidence_ids: tuple[UUID, ...]
    run_id: UUID = field(default_factory=uuid4)
