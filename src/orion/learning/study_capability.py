"""Vendor-neutral routing for structurally governed study intents."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from enum import StrEnum

from ..contracts import Observation
from ..understanding.metadata import (
    MetadataUnderstanding,
    StructuralEntity,
    is_collection_relationship,
)
from .autonomous_loop import (
    AuthorizationEnvelope,
    AuthorizedStudyRequest,
    EvidenceCoverage,
    LearningMemory,
    LearningObjective,
    StudyIntent,
    StudyOutcome,
    StudyRun,
    UnsupportedStudyCapabilityError,
    authorize_intent,
    run_autonomous_loop,
)
from .governed_record_evidence import run_governed_record_evidence


class StudyCapability(StrEnum):
    """A neutral way to acquire evidence for one governed study intent."""

    ORDINARY_RECORD = "ordinary_record"
    SUBMITTED_DOCUMENT = "submitted_document"
    COLLECTION_RELATIONSHIP = "collection_relationship"
    METADATA_STUDY = "metadata_study"
    UNSUPPORTED = "unsupported"


RecordReader = Callable[[str, tuple[str, ...], int], Sequence[Observation]]
MetadataRunner = Callable[[AuthorizedStudyRequest], StudyOutcome]


def _exact_entity(
    intent: StudyIntent,
    understanding: MetadataUnderstanding,
) -> StructuralEntity | None:
    matches = tuple(
        entity for entity in understanding.entities if entity.doctype == intent.entity
    )
    return matches[0] if len(matches) == 1 else None


def derive_study_capability(
    intent: StudyIntent,
    understanding: MetadataUnderstanding,
) -> StudyCapability:
    """Derive one capability from canonical structure, never from entity names."""

    if not isinstance(intent, StudyIntent):
        raise TypeError("intent must be StudyIntent")
    if not isinstance(understanding, MetadataUnderstanding):
        raise TypeError("understanding must be MetadataUnderstanding")
    if intent.tenant_id != understanding.tenant_id:
        raise ValueError("intent crosses governed understanding boundary")
    if intent.study_kind == "metadata_gap":
        return StudyCapability.METADATA_STUDY
    if intent.study_kind != "record_evidence" or len(intent.fields) != 1:
        return StudyCapability.UNSUPPORTED

    entity = _exact_entity(intent, understanding)
    if entity is None or entity.is_child_table or entity.is_single:
        return StudyCapability.UNSUPPORTED
    fields = {field.fieldname: field for field in entity.fields}
    selected = fields.get(intent.fields[0])
    if selected is None:
        return StudyCapability.UNSUPPORTED
    if is_collection_relationship(selected):
        return StudyCapability.COLLECTION_RELATIONSHIP
    if selected.hidden or selected.read_only:
        return StudyCapability.UNSUPPORTED
    if entity.is_submittable:
        return StudyCapability.SUBMITTED_DOCUMENT
    return StudyCapability.ORDINARY_RECORD


def run_routed_governed_record_evidence(
    request: AuthorizedStudyRequest,
    *,
    envelope: AuthorizationEnvelope,
    understanding: MetadataUnderstanding,
    readers: Mapping[StudyCapability, RecordReader],
    evidence_sink: (
        Callable[[AuthorizedStudyRequest, tuple[Observation, ...]], None] | None
    ) = None,
) -> StudyOutcome:
    """Reauthorize and route one scalar request to an exact bounded reader."""

    if not isinstance(request, AuthorizedStudyRequest):
        raise TypeError("request must be AuthorizedStudyRequest")
    reauthorized = authorize_intent(request.intent, envelope, understanding)
    if reauthorized != request:
        raise ValueError("request does not match current authorization")
    capability = derive_study_capability(reauthorized.intent, understanding)
    if capability not in {
        StudyCapability.ORDINARY_RECORD,
        StudyCapability.SUBMITTED_DOCUMENT,
    }:
        raise UnsupportedStudyCapabilityError(
            f"no scalar record reader for {capability.value}"
        )
    reader = readers.get(capability)
    if not callable(reader):
        raise UnsupportedStudyCapabilityError(
            f"no compatible reader for {capability.value}"
        )
    return run_governed_record_evidence(
        reauthorized,
        envelope=envelope,
        understanding=understanding,
        reader=reader,
        evidence_sink=evidence_sink,
    )


def run_governed_study_cycles(
    objective: LearningObjective,
    understanding: MetadataUnderstanding,
    coverage: tuple[EvidenceCoverage, ...],
    envelope: AuthorizationEnvelope,
    *,
    record_readers: Mapping[StudyCapability, RecordReader],
    metadata_runner: MetadataRunner | None = None,
    evidence_sink: (
        Callable[[AuthorizedStudyRequest, tuple[Observation, ...]], None] | None
    ) = None,
    memory: LearningMemory | None = None,
) -> StudyRun:
    """Run bounded discovery and learning cycles through capability routing.

    Readers are injected capabilities. This composition grants no network,
    persistence, promotion, recommendation, or execution authority.
    """

    def runner(request: AuthorizedStudyRequest) -> StudyOutcome:
        reauthorized = authorize_intent(request.intent, envelope, understanding)
        if reauthorized != request:
            raise ValueError("request does not match current authorization")
        capability = derive_study_capability(reauthorized.intent, understanding)
        if capability is StudyCapability.METADATA_STUDY:
            if not callable(metadata_runner):
                raise UnsupportedStudyCapabilityError(
                    "no compatible metadata study capability"
                )
            return metadata_runner(reauthorized)
        return run_routed_governed_record_evidence(
            reauthorized,
            envelope=envelope,
            understanding=understanding,
            readers=record_readers,
            evidence_sink=evidence_sink,
        )

    return run_autonomous_loop(
        objective,
        understanding,
        coverage,
        envelope,
        runner,
        memory=memory,
    )
