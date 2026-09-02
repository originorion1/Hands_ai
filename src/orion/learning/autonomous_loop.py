"""Vendor-neutral autonomous study planning and authorization loop.

The planner chooses a study from structural evidence.  The authorization
envelope independently decides whether that study may run.  This module has
no knowledge of any vendor or business-specific concept.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..discovery.planner import validate_discovery_target
from ..understanding.metadata import MetadataUnderstanding, relationship_target


class StudyStopReason(StrEnum):
    EXHAUSTED = "EXHAUSTED"
    CYCLE_LIMIT = "CYCLE_LIMIT"
    EVIDENCE_BUDGET_LIMIT = "EVIDENCE_BUDGET_LIMIT"
    NO_AUTHORIZED_OPPORTUNITY = "NO_AUTHORIZED_OPPORTUNITY"
    NO_INFORMATION_GAIN = "NO_INFORMATION_GAIN"
    CONFLICT = "CONFLICT"


STUDY_KINDS = frozenset({"record_evidence", "metadata_gap"})
INFORMATION_GAINS = frozenset({"high", "medium", "low", "none"})
HYPOTHESIS_STATES = frozenset({"SUPPORTED", "NOT_SUPPORTED", "INCONCLUSIVE"})
USEFUL_GAIN_THRESHOLD = 1.0


def _target(value: object, label: str) -> None:
    if not isinstance(value, str):
        raise TypeError(f"{label} must be a string")
    validate_discovery_target(value)


def _nonnegative_int(value: object, label: str) -> None:
    if type(value) is not int or value < 0:
        raise ValueError(f"{label} must be a non-negative integer")


def _finite(value: object, label: str) -> None:
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value):
        raise ValueError(f"{label} must be finite")


@dataclass(frozen=True, slots=True)
class LearningObjective:
    objective_id: str
    description: str
    desired_effects: tuple[str, ...] = ()
    prohibited_effects: tuple[str, ...] = ()
    aim_weights: tuple[tuple[str, float], ...] = (
        ("reduce_human_input", 1.0),
        ("increase_evidence_coverage", 1.0),
        ("increase_predictability", 1.0),
        ("reduce_uncertainty_error", 1.0),
    )

    def __post_init__(self) -> None:
        _target(self.objective_id, "objective_id")
        if not isinstance(self.description, str) or not self.description.strip():
            raise ValueError("objective description is required")
        names = [name for name, _ in self.aim_weights]
        if not names or len(names) != len(set(names)):
            raise ValueError("objective aims must be unique")
        for name, weight in self.aim_weights:
            if not isinstance(name, str) or not name.strip():
                raise ValueError("objective aim must be non-empty")
            _finite(weight, "objective weight")
            if weight < 0:
                raise ValueError("objective weight must be non-negative")


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

    def __post_init__(self) -> None:
        _target(self.entity, "coverage entity")
        _target(self.field, "coverage field")
        for name, value in (
            ("observations_seen", self.observations_seen),
            ("valid_observations", self.valid_observations),
            ("distinct_value_count", self.distinct_value_count),
            ("missing_count", self.missing_count),
            ("prior_prediction_attempts", self.prior_prediction_attempts),
            ("study_count", self.study_count),
        ):
            _nonnegative_int(value, name)
        if self.valid_observations > self.observations_seen:
            raise ValueError("valid observations exceed observations")
        _finite(self.prior_prediction_coverage, "prediction coverage")
        if not 0 <= self.prior_prediction_coverage <= 1:
            raise ValueError("prediction coverage outside [0, 1]")
        if self.prior_error is not None:
            _finite(self.prior_error, "prior error")


@dataclass(frozen=True, slots=True)
class AuthorizationEnvelope:
    tenant_id: str
    objective_id: str
    allowed_metadata_entities: frozenset[str] = frozenset()
    allowed_record_entities: frozenset[str] = frozenset()
    allowed_record_fields: tuple[tuple[str, tuple[str, ...]], ...] = ()
    max_fields_per_proposal: int = 3
    max_records_per_proposal: int = 100
    max_cycles: int = 10
    max_cumulative_records: int = 1000
    max_metadata_targets: int = 10
    allowed_observation_modes: frozenset[str] = frozenset({"READ_ONLY"})

    def __post_init__(self) -> None:
        _target(self.tenant_id, "tenant_id")
        _target(self.objective_id, "objective_id")
        for entity in self.allowed_metadata_entities | self.allowed_record_entities:
            _target(entity, "authorized entity")
        if len(dict(self.allowed_record_fields)) != len(self.allowed_record_fields):
            raise ValueError("duplicate field authorization scope")
        for entity, fields in self.allowed_record_fields:
            _target(entity, "field authorization entity")
            if not fields or len(fields) != len(set(fields)):
                raise ValueError("field authorization must be unique and non-empty")
            for field in fields:
                _target(field, "authorized field")
        for name, value in (
            ("max_fields_per_proposal", self.max_fields_per_proposal),
            ("max_records_per_proposal", self.max_records_per_proposal),
            ("max_cycles", self.max_cycles),
            ("max_cumulative_records", self.max_cumulative_records),
            ("max_metadata_targets", self.max_metadata_targets),
        ):
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be positive")
        if self.allowed_observation_modes != frozenset({"READ_ONLY"}):
            raise ValueError("only READ_ONLY is supported")


@dataclass(frozen=True, slots=True)
class StudyOpportunity:
    entity: str
    fields: tuple[str, ...]
    score: float
    score_components: tuple[tuple[str, float], ...]
    rationale: str
    study_kind: str = "record_evidence"

    def __post_init__(self) -> None:
        _target(self.entity, "opportunity entity")
        if self.study_kind not in STUDY_KINDS:
            raise ValueError("unknown study kind")
        if self.study_kind == "metadata_gap" and self.fields:
            raise ValueError("metadata opportunity cannot request fields")
        if self.study_kind == "record_evidence" and not self.fields:
            raise ValueError("record opportunity requires fields")
        if len(self.fields) != len(set(self.fields)):
            raise ValueError("opportunity fields must be unique")
        for field in self.fields:
            _target(field, "opportunity field")
        _finite(self.score, "opportunity score")


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

    def __post_init__(self) -> None:
        _target(self.tenant_id, "intent tenant")
        _target(self.entity, "intent entity")
        if self.study_kind not in STUDY_KINDS or self.mode != "READ_ONLY":
            raise ValueError("invalid intent mode or study kind")
        if len(self.fields) != len(set(self.fields)):
            raise ValueError("intent fields must be unique")
        for field in self.fields:
            _target(field, "intent field")
        _nonnegative_int(self.requested_records, "requested records")
        if self.study_kind == "metadata_gap" and (self.fields or self.requested_records != 0):
            raise ValueError("metadata intent must not request records")
        if self.study_kind == "record_evidence" and (not self.fields or self.requested_records < 1):
            raise ValueError("record intent requires fields and records")
        for label, value in (("hypothesis", self.hypothesis), ("expected evidence", self.expected_evidence), ("rationale", self.rationale)):
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{label} is required")


@dataclass(frozen=True, slots=True)
class AuthorizedStudyRequest:
    intent: StudyIntent
    tenant_id: str

    def __post_init__(self) -> None:
        if self.tenant_id != self.intent.tenant_id:
            raise ValueError("authorized request tenant mismatch")


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
    study_kind: str = "record_evidence"

    def __post_init__(self) -> None:
        _target(self.entity, "outcome entity")
        if self.study_kind not in STUDY_KINDS or len(self.fields) != len(set(self.fields)):
            raise ValueError("invalid outcome scope")
        for field in self.fields:
            _target(field, "outcome field")
        for name, value in (("observations", self.observations_acquired), ("valid count", self.valid_count)):
            _nonnegative_int(value, name)
        if self.valid_count > self.observations_acquired:
            raise ValueError("valid count exceeds observations")
        _finite(self.coverage_change, "coverage change")
        _finite(self.uncertainty_reduction, "uncertainty reduction")
        if not 0 <= self.coverage_change <= 1 or not -1 <= self.uncertainty_reduction <= 1:
            raise ValueError("outcome metrics outside permitted range")
        if self.information_gain not in INFORMATION_GAINS or self.hypothesis_state not in HYPOTHESIS_STATES:
            raise ValueError("invalid outcome state")
        if any((type(flag) is not bool or flag) for flag in (self.recommendation_allowed, self.promotion_allowed, self.execution_allowed)):
            raise ValueError("outcome authority flags must remain false")


@dataclass(frozen=True, slots=True)
class MetadataStudyState:
    target: str
    study_count: int = 0
    last_information_gain: str = "none"
    resolved: bool = False

    def __post_init__(self) -> None:
        _target(self.target, "metadata target")
        _nonnegative_int(self.study_count, "metadata study count")
        if self.last_information_gain not in INFORMATION_GAINS:
            raise ValueError("invalid metadata information gain")


@dataclass(frozen=True, slots=True)
class LearningMemory:
    attempted: tuple[tuple[str, str], ...] = ()
    outcomes: tuple[StudyOutcome, ...] = ()
    coverage: tuple[EvidenceCoverage, ...] = ()
    metadata: tuple[MetadataStudyState, ...] = ()

    def __post_init__(self) -> None:
        keys = [(item.entity, item.field) for item in self.coverage]
        if len(keys) != len(set(keys)):
            raise ValueError("coverage scopes must be unique")
        targets = [item.target for item in self.metadata]
        if len(targets) != len(set(targets)):
            raise ValueError("metadata targets must be unique")
        for entity, field in self.attempted:
            _target(entity, "attempted entity")
            _target(field, "attempted field")


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

    def __post_init__(self) -> None:
        if self.version != 1 or type(self.sequence) is not int or self.sequence < 1:
            raise ValueError("invalid checkpoint version or sequence")
        _target(self.tenant_id, "checkpoint tenant")
        _target(self.objective_id, "checkpoint objective")


def _understanding(value: object) -> MetadataUnderstanding:
    if not isinstance(value, MetadataUnderstanding):
        raise TypeError("governed understanding must be MetadataUnderstanding")
    return value


def discover_opportunities(
    objective: LearningObjective,
    understanding: MetadataUnderstanding,
    coverage: tuple[EvidenceCoverage, ...],
    memory: LearningMemory | None = None,
) -> tuple[StudyOpportunity, ...]:
    """Rank structural evidence gaps from one canonical current state."""
    understanding = _understanding(understanding)
    memory = memory or LearningMemory()
    current = {(item.entity, item.field): item for item in coverage}
    metadata = {item.target: item for item in memory.metadata}
    known_entities = {entity.doctype for entity in understanding.entities}
    weights = dict(objective.aim_weights)
    opportunities: list[StudyOpportunity] = []
    for entity in understanding.entities:
        for field in entity.fields:
            if field.read_only or field.hidden:
                continue
            state = current.get((entity.doctype, field.fieldname), EvidenceCoverage(entity.doctype, field.fieldname))
            gap = 3.0 if state.observations_seen == 0 else max(0.0, 2.0 - state.prior_prediction_coverage) + state.missing_count * 0.1
            importance = 2.0 if field.required else 0.5
            penalty = min(2.0, state.study_count * 0.5)
            if (entity.doctype, field.fieldname) in memory.attempted:
                penalty += 1.5
            score = (
                weights.get("reduce_human_input", 0.0)
                + gap * weights.get("increase_evidence_coverage", 0.0)
                + importance * weights.get("increase_predictability", 0.0)
                + (1.0 if state.prior_error is not None else 0.0) * weights.get("reduce_uncertainty_error", 0.0)
                - penalty
            )
            opportunities.append(StudyOpportunity(entity.doctype, (field.fieldname,), score, (("gap", gap), ("importance", importance), ("penalty", -penalty)), "generic structural evidence gap"))
            target = relationship_target(field)
            if target and target not in known_entities:
                state_meta = metadata.get(target, MetadataStudyState(target))
                meta_penalty = min(2.0, state_meta.study_count * 0.5)
                if state_meta.resolved:
                    continue
                opportunities.append(StudyOpportunity(target, (), weights.get("increase_evidence_coverage", 0.0) - meta_penalty, (("metadata_gap", 1.0), ("penalty", -meta_penalty)), "unresolved structural relationship", "metadata_gap"))
    return tuple(sorted(opportunities, key=lambda item: (-item.score, item.entity, item.fields)))


def generate_intent(opportunity: StudyOpportunity, tenant_id: str, max_records: int = 100) -> StudyIntent:
    _target(tenant_id, "intent tenant")
    return StudyIntent(tenant_id, opportunity.entity, opportunity.fields, opportunity.study_kind, 0 if opportunity.study_kind == "metadata_gap" else min(max_records, 100), "observed evidence will reduce uncertainty", "aggregate observations", opportunity.rationale)


def authorize_intent(intent: StudyIntent, envelope: AuthorizationEnvelope, understanding: MetadataUnderstanding | None = None) -> AuthorizedStudyRequest:
    if intent.tenant_id != envelope.tenant_id or intent.mode != "READ_ONLY":
        raise ValueError("intent crosses authorization boundary")
    validate_discovery_target(intent.entity)
    if intent.study_kind == "metadata_gap":
        if intent.entity not in envelope.allowed_metadata_entities or intent.fields or intent.requested_records != 0:
            raise ValueError("metadata target is not authorized")
    else:
        if intent.entity not in envelope.allowed_record_entities:
            raise ValueError("record entity is not authorized")
        fields = dict(envelope.allowed_record_fields).get(intent.entity)
        if fields is None or not set(intent.fields).issubset(fields):
            raise ValueError("record fields are not explicitly authorized")
        if len(intent.fields) > envelope.max_fields_per_proposal or not 1 <= intent.requested_records <= envelope.max_records_per_proposal:
            raise ValueError("record budget exceeded")
        if understanding is None or not isinstance(understanding, MetadataUnderstanding) or understanding.tenant_id != envelope.tenant_id:
            raise ValueError("record study requires matching governed understanding")
        governed = {entity.doctype: {field.fieldname for field in entity.fields} for entity in understanding.entities}
        if intent.entity not in governed or not set(intent.fields).issubset(governed[intent.entity]):
            raise ValueError("record field is not governed")
        for field in intent.fields:
            validate_discovery_target(field)
    return AuthorizedStudyRequest(intent, envelope.tenant_id)


def _canonical_coverage(baseline: tuple[EvidenceCoverage, ...], memory: LearningMemory) -> tuple[EvidenceCoverage, ...]:
    """Combine a caller baseline with loop-produced incremental coverage.

    Memory coverage is incremental when it is below the baseline and is
    already canonical when it is above it. Equal overlapping snapshots are
    ambiguous and fail closed.
    """
    result = {(item.entity, item.field): item for item in baseline}
    for delta in memory.coverage:
        old = result.get((delta.entity, delta.field))
        if old is None or delta.observations_seen > old.observations_seen:
            result[(delta.entity, delta.field)] = delta
        elif delta.observations_seen < old.observations_seen:
            result[(delta.entity, delta.field)] = EvidenceCoverage(delta.entity, delta.field, old.observations_seen + delta.observations_seen, old.valid_observations + delta.valid_observations, max(old.distinct_value_count, delta.distinct_value_count), old.missing_count + delta.missing_count, old.prior_prediction_attempts + delta.prior_prediction_attempts, min(1.0, old.prior_prediction_coverage + delta.prior_prediction_coverage * (1.0 - old.prior_prediction_coverage)), old.prior_error if old.prior_error is not None else delta.prior_error, old.study_count + delta.study_count)
        else:
            raise ValueError("ambiguous duplicate coverage snapshot")
    return tuple(result.values())


def run_autonomous_loop(objective: LearningObjective, understanding: MetadataUnderstanding, coverage: tuple[EvidenceCoverage, ...], envelope: AuthorizationEnvelope, runner: Any, *, memory: LearningMemory | None = None) -> StudyRun:
    understanding = _understanding(understanding)
    if understanding.tenant_id != envelope.tenant_id or envelope.objective_id != objective.objective_id:
        raise ValueError("tenant or objective boundary mismatch")
    memory = memory or LearningMemory()
    current = LearningMemory(memory.attempted, memory.outcomes, _canonical_coverage(coverage, memory), memory.metadata)
    intents: list[StudyIntent] = []
    outcomes: list[StudyOutcome] = []
    records = 0
    metadata_count = 0
    for _ in range(envelope.max_cycles):
        opportunities = discover_opportunities(objective, understanding, current.coverage, current)
        authorized = None
        for opportunity in opportunities:
            if opportunity.study_kind == "metadata_gap" and metadata_count >= envelope.max_metadata_targets:
                continue
            try:
                candidate = authorize_intent(generate_intent(opportunity, envelope.tenant_id, envelope.max_records_per_proposal), envelope, understanding)
            except ValueError:
                continue
            authorized = candidate
            break
        if authorized is None:
            reason = StudyStopReason.NO_AUTHORIZED_OPPORTUNITY if opportunities else StudyStopReason.EXHAUSTED
            return StudyRun(tuple(intents), tuple(outcomes), current, reason)
        intent = authorized.intent
        if intent.study_kind == "record_evidence" and records + intent.requested_records > envelope.max_cumulative_records:
            return StudyRun(tuple(intents), tuple(outcomes), current, StudyStopReason.EVIDENCE_BUDGET_LIMIT)
        outcome = runner(authorized)
        _validate_outcome(outcome, authorized, envelope.max_cumulative_records - records)
        intents.append(intent)
        outcomes.append(outcome)
        if intent.study_kind == "metadata_gap":
            metadata_count += 1
        records += outcome.observations_acquired
        current = _learn(current, authorized, outcome)
        if outcome.conflict:
            return StudyRun(tuple(intents), tuple(outcomes), current, StudyStopReason.CONFLICT)
        if outcome.information_gain in {"none", "low"}:
            remaining = discover_opportunities(objective, understanding, current.coverage, current)
            useful = False
            for opportunity in remaining:
                try:
                    authorize_intent(generate_intent(opportunity, envelope.tenant_id, envelope.max_records_per_proposal), envelope, understanding)
                except ValueError:
                    continue
                if opportunity.score > USEFUL_GAIN_THRESHOLD:
                    useful = True
                    break
            if not useful:
                return StudyRun(tuple(intents), tuple(outcomes), current, StudyStopReason.NO_INFORMATION_GAIN)
    return StudyRun(tuple(intents), tuple(outcomes), current, StudyStopReason.CYCLE_LIMIT)


def _learn(memory: LearningMemory, request: AuthorizedStudyRequest, outcome: StudyOutcome) -> LearningMemory:
    coverage = list(memory.coverage)
    if outcome.study_kind == "metadata_gap":
        states = list(memory.metadata)
        prior = next((item for item in states if item.target == outcome.entity), MetadataStudyState(outcome.entity))
        states = [item for item in states if item.target != outcome.entity]
        states.append(MetadataStudyState(outcome.entity, prior.study_count + 1, outcome.information_gain, outcome.information_gain in {"high", "medium"} and not outcome.conflict))
        return LearningMemory(memory.attempted, memory.outcomes + (outcome,), tuple(coverage), tuple(states))
    for index, item in enumerate(coverage):
        if item.entity == outcome.entity and item.field in outcome.fields:
            updated = min(1.0, item.prior_prediction_coverage + outcome.coverage_change * (1.0 - item.prior_prediction_coverage))
            coverage[index] = EvidenceCoverage(item.entity, item.field, item.observations_seen + outcome.observations_acquired, item.valid_observations + outcome.valid_count, item.distinct_value_count, item.missing_count, item.prior_prediction_attempts + 1, updated, item.prior_error, item.study_count + 1)
    for field in outcome.fields:
        if not any(item.entity == outcome.entity and item.field == field for item in coverage):
            coverage.append(EvidenceCoverage(outcome.entity, field, outcome.observations_acquired, outcome.valid_count, prior_prediction_attempts=1, prior_prediction_coverage=outcome.coverage_change, study_count=1))
    return LearningMemory(memory.attempted + tuple((outcome.entity, field) for field in outcome.fields), memory.outcomes + (outcome,), tuple(coverage), memory.metadata)


def _validate_outcome(outcome: StudyOutcome, request: AuthorizedStudyRequest, remaining_budget: int) -> None:
    if not isinstance(outcome, StudyOutcome):
        raise TypeError("runner returned invalid outcome")
    intent = request.intent
    if outcome.study_kind != intent.study_kind or outcome.entity != intent.entity or outcome.fields != intent.fields:
        raise ValueError("runner outcome does not match authorized request")
    if outcome.observations_acquired > intent.requested_records or outcome.observations_acquired > remaining_budget:
        raise ValueError("runner exceeded evidence budget")


def resume_checkpoint(checkpoint: LearningCheckpoint, envelope: AuthorizationEnvelope) -> LearningMemory:
    if checkpoint.tenant_id != envelope.tenant_id or checkpoint.objective_id != envelope.objective_id:
        raise ValueError("checkpoint tenant or objective mismatch")
    record_scopes = dict(envelope.allowed_record_fields)
    for entity, field in checkpoint.memory.attempted:
        if entity not in envelope.allowed_record_entities or field not in record_scopes.get(entity, ()):
            raise ValueError("checkpoint record scope exceeds fresh authorization")
    for state in checkpoint.memory.metadata:
        if state.target not in envelope.allowed_metadata_entities:
            raise ValueError("checkpoint metadata scope exceeds fresh authorization")
    for item in checkpoint.memory.coverage:
        if item.entity not in envelope.allowed_record_entities or item.field not in record_scopes.get(item.entity, ()):
            raise ValueError("checkpoint coverage exceeds fresh authorization")
    return checkpoint.memory
