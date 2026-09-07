"""Pure descriptive investigation of retained, explicitly scoped evidence.

Callers retain responsibility for validating the persisted authorization envelope
and metadata provenance. This function performs no acquisition or persistence.
Latest means highest resource-local batch sequence, not business effective time.
A required flag alone cannot establish applicability or a business defect.
"""
from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from ..history.evidence import (
    HistoricalEvidenceBatch,
    historical_evidence_from_json,
    historical_evidence_to_json,
)
from ..understanding.metadata import MetadataUnderstanding, StructuralField
from .autonomous_loop import AuthorizationEnvelope, is_missing_evidence
from .offline_proposal import (
    _NON_STUDY_RECORD_FIELDS,
    canonical_historical_value,
    project_historical_learning_memory,
)


@dataclass(frozen=True, slots=True)
class RetainedFieldEvidence:
    """Private provenance; no raw values are copied into findings."""

    identity: str
    sequence: int
    evidence_id: UUID
    missing: bool
    prior_different_value: bool


@dataclass(frozen=True, slots=True)
class MissingValueFinding:
    """A descriptive question requiring applicability review, never a verdict."""

    tenant_id: str
    company: str
    entity: str
    field: str
    required_in_current_metadata: bool
    absent_identity_count: int
    evidence: tuple[RetainedFieldEvidence, ...]
    metadata_provenance_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class RetainedInvestigation:
    finding: MissingValueFinding | None
    examined_fields: int

    def aggregate(self) -> dict[str, str | int | bool]:
        """Explicit safe projection: never serialize the private finding itself."""
        finding = self.finding
        evidence = finding.evidence if finding else ()
        return {
            "status": "review_candidate" if finding else "no_candidate",
            "examined_fields": self.examined_fields,
            "observed_identities": len(evidence),
            "missing_identities": sum(item.missing for item in evidence),
            "valid_identities": sum(not item.missing for item in evidence),
            "absent_identities": finding.absent_identity_count if finding else 0,
            "changed_identities": sum(item.prior_different_value for item in evidence),
            "required_in_current_metadata": bool(finding and finding.required_in_current_metadata),
            "applicability": "unknown",
            "business_defect_validated": False,
            "prediction_validated": False,
            "recommendation_allowed": False,
            "promotion_allowed": False,
            "execution_allowed": False,
        }


def _scoped_fields(understanding, authorization):
    project_historical_learning_memory(understanding, ())
    if understanding.tenant_id != authorization.tenant_id:
        raise ValueError("metadata tenant mismatch")
    scopes = dict(authorization.allowed_record_fields)
    if set(scopes) != set(authorization.allowed_record_entities):
        raise ValueError("explicit record entity and field scopes must match")
    entities = {item.doctype: item for item in understanding.entities}
    result = {}
    for entity, names in scopes.items():
        if entity not in entities:
            raise ValueError("authorized entity absent from metadata")
        structure = entities[entity]
        if structure.is_child_table or structure.is_single:
            raise ValueError("unsupported entity structure")
        fields = {item.fieldname: item for item in structure.fields}
        for name in names:
            field = fields.get(name)
            if name in _NON_STUDY_RECORD_FIELDS or not isinstance(field, StructuralField):
                raise ValueError("unsupported investigation target")
            if field.doctype != entity or type(field.required) is not bool:
                raise ValueError("malformed structural field")
            if field.hidden or field.read_only or field.fieldtype in {
                "Password", "Table", "Table MultiSelect", "Section Break", "Column Break",
                "Tab Break", "HTML", "Button",
            }:
                raise ValueError("unsafe investigation target")
            result[(entity, name)] = field
    return entities, scopes, result


def investigate_retained_missing_values(
    understanding: MetadataUnderstanding,
    batches: tuple[HistoricalEvidenceBatch, ...],
    *,
    authorization: AuthorizationEnvelope,
    company: str,
) -> RetainedInvestigation:
    """Select one missing-value question, deduplicated by entity/identity/field.

    A field absent from a later projection does not erase its last observation.
    Repetitions add provenance history but never independent support. Inputs must
    be a consecutive retained prefix per resource; no cross-resource clock order
    is assumed. Required targets rank first, then missing independent identities,
    then lexical scope. No samples or only absent keys produce no candidate.
    """
    if not isinstance(authorization, AuthorizationEnvelope):
        raise TypeError("authorization must be AuthorizationEnvelope")
    authorization.__post_init__()
    if type(batches) is not tuple:
        raise TypeError("batches must be a tuple")
    if not isinstance(company, str) or not company.strip() or company != company.strip():
        raise ValueError("exact company required")
    entities, scopes, fields = _scoped_fields(understanding, authorization)
    validated = tuple(historical_evidence_from_json(historical_evidence_to_json(b)) for b in batches)
    latest = {}
    seen_values = {}
    identities = {entity: set() for entity in scopes}
    sequences = {}
    for batch in sorted(validated, key=lambda item: (item.resource, item.sequence)):
        if batch.tenant_id != authorization.tenant_id or batch.resource not in scopes:
            raise ValueError("retained evidence crosses authorization scope")
        expected = sequences.get(batch.resource, 0) + 1
        if batch.sequence != expected:
            raise ValueError("retained resource sequence must be consecutive and unique")
        sequences[batch.resource] = expected
        allowed = set(scopes[batch.resource]) | _NON_STUDY_RECORD_FIELDS
        for observation in batch.observations:
            record = observation.evidence.payload["record"]
            if record.get("company") != company or set(record) - allowed:
                raise ValueError("retained record crosses company or field scope")
            identity = record["name"]
            identities[batch.resource].add(identity)
            for name in scopes[batch.resource]:
                if name not in record:
                    continue
                value = record[name]
                if isinstance(value, (dict, list)):
                    raise TypeError("investigation requires scalar evidence")
                key = (batch.resource, name, identity)
                canonical = canonical_historical_value(value)
                previous = seen_values.setdefault(key, set())
                previous.add(canonical)
                latest[key] = RetainedFieldEvidence(
                    identity, batch.sequence, observation.evidence.evidence_id,
                    is_missing_evidence(value), len(previous) > 1,
                )
    candidates = []
    for (entity, name), field in sorted(fields.items()):
        evidence = tuple(latest[(entity, name, identity)] for identity in sorted(identities[entity])
                         if (entity, name, identity) in latest)
        missing = sum(item.missing for item in evidence)
        if missing:
            finding = MissingValueFinding(
                authorization.tenant_id, company, entity, name, field.required,
                len(identities[entity]) - len(evidence), evidence, entities[entity].provenance_ids,
            )
            candidates.append(((-int(field.required), -missing, entity, name), finding))
    selected = min(candidates, key=lambda item: item[0])[1] if candidates else None
    return RetainedInvestigation(selected, len(fields))
