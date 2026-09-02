"""Vendor-neutral autonomous study planning, authorization, and memory loop."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any


class StudyStopReason(StrEnum):
    EXHAUSTED = "EXHAUSTED"
    CYCLE_LIMIT = "CYCLE_LIMIT"
    EVIDENCE_BUDGET_LIMIT = "EVIDENCE_BUDGET_LIMIT"
    NO_AUTHORIZED_OPPORTUNITY = "NO_AUTHORIZED_OPPORTUNITY"
    NO_INFORMATION_GAIN = "NO_INFORMATION_GAIN"
    CONFLICT = "CONFLICT"


@dataclass(frozen=True, slots=True)
class LearningObjective:
    objective_id: str
    description: str
    desired_effects: tuple[str, ...] = ()
    prohibited_effects: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.objective_id, str) or not self.objective_id.strip() or not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("objective requires non-empty identity and description")


@dataclass(frozen=True, slots=True)
class EvidenceCoverage:
    entity: str
    field: str
    observations_seen: int = 0
    valid_observations: int = 0
    distinct_value_count: int = 0
    missing_count: int = 0
    prior_prediction_attempts: int = 0
    prior_prediction_coverage: float = 0.0
    prior_error: float | None = None
    study_count: int = 0


@dataclass(frozen=True, slots=True)
class AuthorizationEnvelope:
    tenant_id: str
    objective_id: str | None = None
    allowed_metadata_entities: frozenset[str] = frozenset()
    allowed_record_entities: frozenset[str] = frozenset()
    max_entities_per_cycle: int = 1
    max_fields_per_proposal: int = 3
    max_records_per_proposal: int = 100
    max_cycles: int = 10
    max_cumulative_records: int = 1000
    allowed_observation_modes: frozenset[str] = frozenset({"READ_ONLY"})


@dataclass(frozen=True, slots=True)
class StudyOpportunity:
    entity: str
    fields: tuple[str, ...]
    score: float
    score_components: tuple[tuple[str, float], ...]
    rationale: str
    study_kind: str = "record_evidence"


@dataclass(frozen=True, slots=True)
class StudyIntent:
    tenant_id: str
    entity: str
    fields: tuple[str, ...]
    study_kind: str
    requested_records: int
    hypothesis: str
    expected_evidence: str
    rationale: str
    mode: str = "READ_ONLY"


@dataclass(frozen=True, slots=True)
class AuthorizedStudyRequest:
    intent: StudyIntent
    tenant_id: str


@dataclass(frozen=True, slots=True)
class StudyOutcome:
    entity: str
    fields: tuple[str, ...]
    observations_acquired: int
    valid_count: int
    coverage_change: float
    uncertainty_reduction: float
    information_gain: str
    hypothesis_state: str
    conflict: bool = False
    recommendation_allowed: bool = False
    promotion_allowed: bool = False
    execution_allowed: bool = False


@dataclass(frozen=True, slots=True)
class LearningMemory:
    attempted: tuple[tuple[str, str], ...] = ()
    outcomes: tuple[StudyOutcome, ...] = ()
    coverage: tuple[EvidenceCoverage, ...] = ()


@dataclass(frozen=True, slots=True)
class StudyRun:
    intents: tuple[StudyIntent, ...]
    outcomes: tuple[StudyOutcome, ...]
    memory: LearningMemory
    stop_reason: StudyStopReason


@dataclass(frozen=True, slots=True)
class LearningCheckpoint:
    version: int
    tenant_id: str
    objective_id: str
    sequence: int
    memory: LearningMemory


def discover_opportunities(objective: LearningObjective, understanding: Any, coverage: tuple[EvidenceCoverage, ...], memory: LearningMemory | None = None) -> tuple[StudyOpportunity, ...]:
    """Rank generic editable/visible structural gaps deterministically."""
    if memory is None:
        memory = LearningMemory()
    covered = {(item.entity, item.field): item for item in coverage}
    tried = set(memory.attempted)
    opportunities: list[StudyOpportunity] = []
    for entity in getattr(understanding, "entities", ()):
        fields = getattr(entity, "fields", ())
        for structural in fields:
            name = getattr(entity, "doctype", getattr(entity, "name", ""))
            field_name = getattr(structural, "fieldname", getattr(structural, "name", ""))
            if not name or not field_name or getattr(structural, "read_only", False) or getattr(structural, "hidden", False):
                continue
            state = covered.get((name, field_name), EvidenceCoverage(name, field_name))
            gap = 3.0 if state.observations_seen == 0 else max(0.0, 2.0 - state.prior_prediction_coverage) + state.missing_count * 0.1
            importance = 2.0 if getattr(structural, "required", False) else 0.5
            relation = 0.5 if getattr(structural, "options", None) else 0.0
            penalty = min(2.0, state.study_count * 0.5) + (1.5 if (name, field_name) in tried else 0.0)
            components = (("human_entry", 1.0), ("importance", importance), ("gap", gap), ("relationship", relation), ("diminishing_returns", -penalty))
            score = sum(value for _, value in components)
            kind = "metadata_gap" if state.observations_seen == 0 else "record_evidence"
            opportunities.append(StudyOpportunity(name, (field_name,), score, components, "generic structural and evidence gap", kind))
    return tuple(sorted(opportunities, key=lambda item: (-item.score, item.entity, item.fields)))


def generate_intent(opportunity: StudyOpportunity, tenant_id: str, max_records: int = 100) -> StudyIntent:
    return StudyIntent(tenant_id, opportunity.entity, opportunity.fields, opportunity.study_kind, min(max_records, 100), "observed evidence will reduce uncertainty for selected structural fields", "aggregate observations", opportunity.rationale)


def authorize_intent(intent: StudyIntent, envelope: AuthorizationEnvelope, understanding: Any | None = None) -> AuthorizedStudyRequest:
    allowed_entities = envelope.allowed_metadata_entities if intent.study_kind == "metadata_gap" else envelope.allowed_record_entities
    if intent.tenant_id != envelope.tenant_id or intent.mode not in envelope.allowed_observation_modes or intent.entity not in allowed_entities or len(intent.fields) > envelope.max_fields_per_proposal or intent.requested_records > envelope.max_records_per_proposal or intent.requested_records < 1 or intent.study_kind not in {"metadata_gap", "record_evidence"} or any(not isinstance(value, str) or not value.strip() or any(token in value for token in ("*", "/", "?", "=", "#")) for value in (intent.entity, *intent.fields)):
        raise ValueError("study intent is outside authorization envelope")
    if understanding is not None:
        entities = {getattr(entity, "doctype", getattr(entity, "name", "")): entity for entity in getattr(understanding, "entities", ())}
        entity = entities.get(intent.entity)
        fields = {getattr(item, "fieldname", getattr(item, "name", "")) for item in getattr(entity, "fields", ())} if entity is not None else set()
        if entity is None or not set(intent.fields).issubset(fields):
            raise ValueError("study target is not governed understanding")
    return AuthorizedStudyRequest(intent, envelope.tenant_id)


def run_autonomous_loop(objective: LearningObjective, understanding: Any, coverage: tuple[EvidenceCoverage, ...], envelope: AuthorizationEnvelope, runner: Callable[[AuthorizedStudyRequest], StudyOutcome], *, memory: LearningMemory | None = None) -> StudyRun:
    if memory is None:
        memory = LearningMemory()
    intents: list[StudyIntent] = []
    outcomes: list[StudyOutcome] = []
    current = memory
    records = 0
    for _ in range(envelope.max_cycles):
        opportunities = discover_opportunities(objective, understanding, coverage + current.coverage, current)
        if not opportunities:
            return StudyRun(tuple(intents), tuple(outcomes), current, StudyStopReason.EXHAUSTED)
        authorized = None
        for opportunity in opportunities:
            try:
                candidate = authorize_intent(generate_intent(opportunity, envelope.tenant_id, envelope.max_records_per_proposal), envelope, understanding)
            except ValueError:
                continue
            authorized = candidate
            break
        if authorized is None:
            return StudyRun(tuple(intents), tuple(outcomes), current, StudyStopReason.NO_AUTHORIZED_OPPORTUNITY)
        if records + authorized.intent.requested_records > envelope.max_cumulative_records:
            return StudyRun(tuple(intents), tuple(outcomes), current, StudyStopReason.EVIDENCE_BUDGET_LIMIT)
        outcome = runner(authorized)
        _validate_outcome(outcome, authorized, envelope.max_cumulative_records - records)
        intents.append(authorized.intent)
        outcomes.append(outcome)
        records += outcome.observations_acquired
        current = _learn(current, authorized, outcome)
        if outcome.conflict:
            return StudyRun(tuple(intents), tuple(outcomes), current, StudyStopReason.CONFLICT)
        if outcome.information_gain.lower() in {"none", "low"}:
            remaining = discover_opportunities(objective, understanding, coverage + current.coverage, current)
            if not any(item.score > 1.0 for item in remaining):
                return StudyRun(tuple(intents), tuple(outcomes), current, StudyStopReason.NO_INFORMATION_GAIN)
    return StudyRun(tuple(intents), tuple(outcomes), current, StudyStopReason.CYCLE_LIMIT)


def resume_checkpoint(checkpoint: LearningCheckpoint, envelope: AuthorizationEnvelope) -> LearningMemory:
    if checkpoint.version != 1 or checkpoint.sequence < 1 or checkpoint.tenant_id != envelope.tenant_id or envelope.objective_id != checkpoint.objective_id or any(entity not in envelope.allowed_record_entities and entity not in envelope.allowed_metadata_entities for entity, _ in checkpoint.memory.attempted):
        raise ValueError("checkpoint tenant mismatch")
    return checkpoint.memory


def _learn(memory: LearningMemory, request: AuthorizedStudyRequest, outcome: StudyOutcome) -> LearningMemory:
    updated = []
    found = False
    for item in memory.coverage:
        if item.entity == outcome.entity and item.field in outcome.fields:
            updated.append(EvidenceCoverage(item.entity, item.field, item.observations_seen + outcome.observations_acquired, item.valid_observations + outcome.valid_count, item.distinct_value_count, item.missing_count, item.prior_prediction_attempts + 1, min(1.0, max(0.0, item.prior_prediction_coverage + outcome.coverage_change)), item.prior_error, item.study_count + 1))
            found = True
        else:
            updated.append(item)
    if not found:
        for field in outcome.fields:
            updated.append(EvidenceCoverage(outcome.entity, field, outcome.observations_acquired, outcome.valid_count, 0, 0, 1, min(1.0, max(0.0, outcome.coverage_change)), study_count=1))
    return LearningMemory(memory.attempted + tuple((outcome.entity, field) for field in outcome.fields), memory.outcomes + (outcome,), tuple(updated))


def _validate_outcome(outcome: StudyOutcome, request: AuthorizedStudyRequest, remaining_budget: int) -> None:
    import math
    if not isinstance(outcome, StudyOutcome) or outcome.entity != request.intent.entity or outcome.fields != request.intent.fields or type(outcome.observations_acquired) is not int or outcome.observations_acquired < 0 or outcome.observations_acquired > request.intent.requested_records or outcome.observations_acquired > remaining_budget or type(outcome.valid_count) is not int or not 0 <= outcome.valid_count <= outcome.observations_acquired or outcome.information_gain not in {"high", "medium", "low", "none"} or outcome.hypothesis_state not in {"SUPPORTED", "NOT_SUPPORTED", "INCONCLUSIVE"} or not all(isinstance(value, (int, float)) and math.isfinite(value) for value in (outcome.coverage_change, outcome.uncertainty_reduction)) or outcome.recommendation_allowed or outcome.promotion_allowed or outcome.execution_allowed:
        raise ValueError("runner outcome violates study contract")
