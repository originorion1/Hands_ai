"""Governed neutral boundary for one bounded record-evidence study."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence

from ..contracts import EvidenceKind, Observation, ObservationMode
from ..understanding.metadata import MetadataUnderstanding
from .autonomous_loop import (
    AuthorizationEnvelope,
    AuthorizedStudyRequest,
    StudyOutcome,
    authorize_intent,
    is_missing_evidence,
)


def run_governed_record_evidence(
    request: AuthorizedStudyRequest,
    *,
    envelope: AuthorizationEnvelope,
    understanding: MetadataUnderstanding,
    reader: Callable[[], Sequence[Observation]],
) -> StudyOutcome:
    """Reauthorize, read, validate, and return evidence-only aggregates."""

    if not isinstance(request, AuthorizedStudyRequest):
        raise TypeError("request must be AuthorizedStudyRequest")
    reauthorized = authorize_intent(request.intent, envelope, understanding)
    if reauthorized != request:
        raise ValueError("request does not match current authorization")
    intent = request.intent
    if intent.study_kind != "record_evidence":
        raise ValueError("record_evidence request is required")
    if len(intent.fields) != 1:
        raise ValueError("evidence-only request requires exactly one field")
    if not callable(reader):
        raise TypeError("reader must be callable")

    observations = reader()
    if not isinstance(observations, Sequence):
        raise TypeError("reader must return a bounded observation sequence")
    if len(observations) > intent.requested_records:
        raise ValueError("reader exceeded requested record bound")

    field = intent.fields[0]
    valid_count = 0
    for observation in observations:
        if not isinstance(observation, Observation):
            raise TypeError("reader must return Observation values")
        evidence = observation.evidence
        if observation.mode is not ObservationMode.READ_ONLY:
            raise ValueError("reader observation must be READ_ONLY")
        if evidence.kind is not EvidenceKind.API:
            raise ValueError("reader evidence must be API evidence")
        if evidence.tenant_id != request.tenant_id:
            raise ValueError("reader evidence crosses tenant boundary")
        payload = evidence.payload
        if not isinstance(payload, Mapping):
            raise TypeError("reader evidence payload must be a mapping")
        if payload.get("resource") != intent.entity:
            raise ValueError("reader evidence resource does not match request")
        record = payload.get("record")
        if not isinstance(record, Mapping):
            raise TypeError("reader record payload must be a mapping")
        if field not in record:
            raise ValueError("reader record is missing selected field")
        if not is_missing_evidence(record[field]):
            valid_count += 1

    return StudyOutcome(
        entity=intent.entity,
        fields=intent.fields,
        observations_acquired=len(observations),
        valid_count=valid_count,
        coverage_change=0.0,
        uncertainty_reduction=0.0,
        information_gain="none",
        hypothesis_state="INCONCLUSIVE",
        prediction_evaluated=False,
    )
