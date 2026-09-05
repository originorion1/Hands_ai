"""Bounded autonomous shadow-soak composition for durable record evidence."""

from __future__ import annotations

import math
from collections import Counter
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from ..contracts import Observation, utc_now
from ..history.evidence import (
    HistoricalEvidenceBatch,
    HistoricalEvidenceError,
)
from ..history.sampling import persist_historical_sample
from ..understanding.metadata import MetadataUnderstanding
from .autonomous_loop import (
    AuthorizationEnvelope,
    AuthorizedStudyRequest,
    LearningObjective,
    StudyOpportunity,
    authorize_intent,
    discover_opportunities,
    generate_intent,
)
from .governed_record_evidence import GovernedEvidenceScopeError
from .offline_proposal import project_historical_coverage
from .study_capability import (
    RecordReader,
    StudyCapability,
    derive_study_capability,
    run_routed_governed_record_evidence,
)


class ShadowSoakStopReason(StrEnum):
    """Safe terminal categories for one bounded soak session."""

    DURATION_LIMIT = "duration_limit"
    CYCLE_LIMIT = "cycle_limit"
    READ_LIMIT = "read_limit"
    OBSERVATION_LIMIT = "observation_limit"
    NO_CANDIDATE = "no_candidate"
    NO_AUTHORIZED_CANDIDATE = "no_authorized_candidate"
    NON_PROGRESS_LIMIT = "non_progress_limit"
    PERSISTENCE_FAILURE = "persistence_failure"
    TENANT_SCOPE_MISMATCH = "tenant_scope_mismatch"
    ERP_CONTRACT_FAILURE = "erp_contract_failure"
    USER_TERMINATION = "user_termination"


@dataclass(frozen=True, slots=True)
class ShadowSoakSessionEnvelope:
    """Fixed read-only authorization and hard budgets for a soak session."""

    authorization: AuthorizationEnvelope
    max_wall_clock_seconds: float
    max_study_cycles: int
    max_erp_reads: int
    max_observations_per_study: int
    max_cumulative_observations: int
    max_consecutive_non_progress: int
    observation_mode: str = "READ_ONLY"

    def __post_init__(self) -> None:
        if not isinstance(self.authorization, AuthorizationEnvelope):
            raise TypeError("authorization must be AuthorizationEnvelope")
        if (
            not isinstance(self.max_wall_clock_seconds, (int, float))
            or isinstance(self.max_wall_clock_seconds, bool)
            or not math.isfinite(self.max_wall_clock_seconds)
            or self.max_wall_clock_seconds <= 0
        ):
            raise ValueError("max_wall_clock_seconds must be finite and positive")
        for name in (
            "max_study_cycles",
            "max_erp_reads",
            "max_observations_per_study",
            "max_cumulative_observations",
            "max_consecutive_non_progress",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if self.observation_mode != "READ_ONLY":
            raise ValueError("shadow soak supports READ_ONLY only")
        if self.max_study_cycles > self.authorization.max_cycles:
            raise ValueError("session cycle budget exceeds authorization")
        if self.max_observations_per_study > self.authorization.max_records_per_proposal:
            raise ValueError("per-study budget exceeds authorization")
        if self.max_cumulative_observations > self.authorization.max_cumulative_records:
            raise ValueError("cumulative observation budget exceeds authorization")

    @classmethod
    def six_hour(cls, authorization: AuthorizationEnvelope) -> ShadowSoakSessionEnvelope:
        """Build the preferred limits for the first separately authorized soak."""

        return cls(
            authorization=authorization,
            max_wall_clock_seconds=6 * 60 * 60,
            max_study_cycles=100,
            max_erp_reads=100,
            max_observations_per_study=5,
            max_cumulative_observations=500,
            max_consecutive_non_progress=5,
        )


@dataclass(frozen=True, slots=True)
class ShadowSoakReport:
    """Aggregate-only session result containing no customer record values."""

    session_started_at: datetime
    session_ended_at: datetime
    elapsed_seconds: float
    cycles_attempted: int
    cycles_completed: int
    erp_reads: int
    erp_writes: int
    evidence_batches_appended: int
    observations_persisted: int
    supported_proposal_count: int
    unsupported_proposal_count: int
    failure_category_counts: tuple[tuple[str, int], ...]
    distinct_entities_studied: int
    first_selected_target_type: str | None
    final_selected_target_type: str | None
    stop_reason: ShadowSoakStopReason
    prediction_evaluated_outcomes: int
    evidence_only_outcomes: int
    recommendation_allowed: bool = False
    promotion_allowed: bool = False
    execution_allowed: bool = False

    def __post_init__(self) -> None:
        if any(
            flag
            for flag in (
                self.recommendation_allowed,
                self.promotion_allowed,
                self.execution_allowed,
            )
        ):
            raise ValueError("shadow soak cannot grant downstream authority")
        if self.erp_writes != 0:
            raise ValueError("shadow soak cannot perform ERP writes")


class ShadowSoakEvidenceStore(Protocol):
    """Existing append-only store plus deterministic resource enumeration."""

    def append(self, batch: HistoricalEvidenceBatch) -> None: ...

    def load_all(self, *, tenant_id: str, resource: str) -> tuple[HistoricalEvidenceBatch, ...]: ...

    def list_resources(self, *, tenant_id: str) -> tuple[str, ...]: ...


class _ValidatedObservationSource:
    def __init__(self, observations: tuple[Observation, ...]) -> None:
        self._observations = observations

    def discover(self) -> tuple[Observation, ...]:
        return self._observations


class _PersistenceFailure(RuntimeError):
    pass


class _VerifiedEvidenceSink:
    def __init__(
        self,
        *,
        request: AuthorizedStudyRequest,
        store: ShadowSoakEvidenceStore,
        clock: Callable[[], datetime],
    ) -> None:
        self._request = request
        self._store = store
        self._clock = clock
        self.acknowledgements = []

    def __call__(
        self,
        governed_request: AuthorizedStudyRequest,
        observations: tuple[Observation, ...],
    ) -> None:
        if governed_request != self._request:
            raise _PersistenceFailure("governed request changed before append")
        if not observations:
            return
        try:
            acknowledgement = persist_historical_sample(
                _ValidatedObservationSource(observations),
                self._store,
                tenant_id=self._request.tenant_id,
                resource=self._request.intent.entity,
                clock=self._clock,
            )
        except Exception as exc:
            raise _PersistenceFailure from exc
        self.acknowledgements.append(acknowledgement)


def run_autonomous_shadow_soak(
    objective: LearningObjective,
    understanding: MetadataUnderstanding,
    session: ShadowSoakSessionEnvelope,
    *,
    store: ShadowSoakEvidenceStore,
    record_readers: Mapping[StudyCapability, RecordReader],
    clock: Callable[[], datetime] = utc_now,
    termination_requested: Callable[[], bool] | None = None,
) -> ShadowSoakReport:
    """Repeatedly select, authorize, read, validate, append, and reassess.

    Network readers and the durable store are injected capabilities. This
    runtime grants no recommendation, promotion, prediction, or execution
    authority and performs no retries around an append.
    """

    _validate_runtime_inputs(objective, understanding, session, store, clock)
    authorization = session.authorization
    started_at = _read_clock(clock)
    last_time = started_at
    cycles_attempted = 0
    cycles_completed = 0
    erp_reads = 0
    batches_appended = 0
    observations_persisted = 0
    supported = 0
    unsupported = 0
    consecutive_non_progress = 0
    prediction_outcomes = 0
    evidence_only_outcomes = 0
    failures: Counter[str] = Counter()
    studied_entities: set[str] = set()
    first_target_type: str | None = None
    final_target_type: str | None = None

    def finish(reason: ShadowSoakStopReason) -> ShadowSoakReport:
        nonlocal last_time
        ended_at = _read_clock(clock)
        if ended_at < last_time:
            raise ValueError("clock must not move backwards")
        last_time = ended_at
        return ShadowSoakReport(
            session_started_at=started_at,
            session_ended_at=ended_at,
            elapsed_seconds=(ended_at - started_at).total_seconds(),
            cycles_attempted=cycles_attempted,
            cycles_completed=cycles_completed,
            erp_reads=erp_reads,
            erp_writes=0,
            evidence_batches_appended=batches_appended,
            observations_persisted=observations_persisted,
            supported_proposal_count=supported,
            unsupported_proposal_count=unsupported,
            failure_category_counts=tuple(sorted(failures.items())),
            distinct_entities_studied=len(studied_entities),
            first_selected_target_type=first_target_type,
            final_selected_target_type=final_target_type,
            stop_reason=reason,
            prediction_evaluated_outcomes=prediction_outcomes,
            evidence_only_outcomes=evidence_only_outcomes,
        )

    while True:
        try:
            if termination_requested is not None and termination_requested():
                return finish(ShadowSoakStopReason.USER_TERMINATION)
            now = _read_clock(clock)
            if now < last_time:
                raise ValueError("clock must not move backwards")
            last_time = now
            if (now - started_at).total_seconds() >= session.max_wall_clock_seconds:
                return finish(ShadowSoakStopReason.DURATION_LIMIT)
            if cycles_attempted >= session.max_study_cycles:
                return finish(ShadowSoakStopReason.CYCLE_LIMIT)
            if erp_reads >= session.max_erp_reads:
                return finish(ShadowSoakStopReason.READ_LIMIT)
            if (
                observations_persisted + session.max_observations_per_study
                > session.max_cumulative_observations
            ):
                return finish(ShadowSoakStopReason.OBSERVATION_LIMIT)

            try:
                coverage = _load_current_coverage(store, understanding)
            except Exception:  # noqa: BLE001 - durable-state boundary fails closed
                failures["persistence_integrity_failure"] += 1
                return finish(ShadowSoakStopReason.PERSISTENCE_FAILURE)

            opportunities = discover_opportunities(
                objective,
                understanding,
                coverage,
            )
            if not opportunities:
                return finish(ShadowSoakStopReason.NO_CANDIDATE)
            selected = _select_authorized_opportunity(
                opportunities,
                session,
                understanding,
            )
            if selected is None:
                return finish(ShadowSoakStopReason.NO_AUTHORIZED_CANDIDATE)
            opportunity, request = selected
            capability = derive_study_capability(request.intent, understanding)
            target_type = capability.value
            first_target_type = first_target_type or target_type
            final_target_type = target_type
            cycles_attempted += 1

            reader = record_readers.get(capability)
            if capability not in {
                StudyCapability.ORDINARY_RECORD,
                StudyCapability.SUBMITTED_DOCUMENT,
            } or not callable(reader):
                unsupported += 1
                failures["unsupported_capability"] += 1
                consecutive_non_progress += 1
                if consecutive_non_progress >= session.max_consecutive_non_progress:
                    return finish(ShadowSoakStopReason.NON_PROGRESS_LIMIT)
                continue

            supported += 1
            erp_reads += 1
            evidence_sink = _VerifiedEvidenceSink(
                request=request,
                store=store,
                clock=clock,
            )

            try:
                outcome = run_routed_governed_record_evidence(
                    request,
                    envelope=authorization,
                    understanding=understanding,
                    readers={capability: reader},
                    evidence_sink=evidence_sink,
                )
            except _PersistenceFailure:
                failures["persistence_failure"] += 1
                return finish(ShadowSoakStopReason.PERSISTENCE_FAILURE)
            except GovernedEvidenceScopeError:
                failures["tenant_scope_mismatch"] += 1
                return finish(ShadowSoakStopReason.TENANT_SCOPE_MISMATCH)
            except (KeyboardInterrupt, SystemExit):
                return finish(ShadowSoakStopReason.USER_TERMINATION)
            except Exception:  # noqa: BLE001 - reader boundary is failure-counted
                failures["erp_contract_failure"] += 1
                consecutive_non_progress += 1
                if consecutive_non_progress >= session.max_consecutive_non_progress:
                    return finish(ShadowSoakStopReason.ERP_CONTRACT_FAILURE)
                continue

            if outcome.prediction_evaluated:
                prediction_outcomes += 1
            else:
                evidence_only_outcomes += 1
            if outcome.observations_acquired == 0:
                failures["no_progress"] += 1
                consecutive_non_progress += 1
                if consecutive_non_progress >= session.max_consecutive_non_progress:
                    return finish(ShadowSoakStopReason.NON_PROGRESS_LIMIT)
                continue
            if (
                len(evidence_sink.acknowledgements) != 1
                or evidence_sink.acknowledgements[0].observation_count
                != outcome.observations_acquired
            ):
                failures["persistence_failure"] += 1
                return finish(ShadowSoakStopReason.PERSISTENCE_FAILURE)

            cycles_completed += 1
            batches_appended += 1
            observations_persisted += outcome.observations_acquired
            studied_entities.add(opportunity.entity)
            consecutive_non_progress = 0
        except (KeyboardInterrupt, SystemExit):
            return finish(ShadowSoakStopReason.USER_TERMINATION)


def _select_authorized_opportunity(
    opportunities: Sequence[StudyOpportunity],
    session: ShadowSoakSessionEnvelope,
    understanding: MetadataUnderstanding,
):
    for opportunity in opportunities:
        try:
            request = authorize_intent(
                generate_intent(
                    opportunity,
                    session.authorization.tenant_id,
                    session.max_observations_per_study,
                ),
                session.authorization,
                understanding,
            )
        except ValueError:
            continue
        return opportunity, request
    return None


def _load_current_coverage(
    store: ShadowSoakEvidenceStore,
    understanding: MetadataUnderstanding,
):
    resources = store.list_resources(tenant_id=understanding.tenant_id)
    if not isinstance(resources, tuple) or len(resources) != len(set(resources)):
        raise HistoricalEvidenceError("stored resources must be a unique tuple")
    batches: list[HistoricalEvidenceBatch] = []
    for resource in resources:
        history = store.load_all(
            tenant_id=understanding.tenant_id,
            resource=resource,
        )
        if not isinstance(history, tuple):
            raise HistoricalEvidenceError("historical evidence history must be a tuple")
        for batch in history:
            if (
                not isinstance(batch, HistoricalEvidenceBatch)
                or batch.tenant_id != understanding.tenant_id
                or batch.resource != resource
            ):
                raise HistoricalEvidenceError("historical evidence crosses durable scope")
        batches.extend(history)
    return project_historical_coverage(understanding, tuple(batches))


def _validate_runtime_inputs(
    objective: LearningObjective,
    understanding: MetadataUnderstanding,
    session: ShadowSoakSessionEnvelope,
    store: object,
    clock: object,
) -> None:
    if not isinstance(objective, LearningObjective):
        raise TypeError("objective must be LearningObjective")
    if not isinstance(understanding, MetadataUnderstanding):
        raise TypeError("understanding must be MetadataUnderstanding")
    if not isinstance(session, ShadowSoakSessionEnvelope):
        raise TypeError("session must be ShadowSoakSessionEnvelope")
    if understanding.tenant_id != session.authorization.tenant_id:
        raise ValueError("understanding crosses session tenant boundary")
    if objective.objective_id != session.authorization.objective_id:
        raise ValueError("objective crosses session authorization boundary")
    for method in ("append", "load_all", "list_resources"):
        if not callable(getattr(store, method, None)):
            raise TypeError("store must implement append-only evidence contract")
    if not callable(clock):
        raise TypeError("clock must be callable")


def _read_clock(clock: Callable[[], datetime]) -> datetime:
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return value


__all__ = [
    "ShadowSoakEvidenceStore",
    "ShadowSoakReport",
    "ShadowSoakSessionEnvelope",
    "ShadowSoakStopReason",
    "run_autonomous_shadow_soak",
]
