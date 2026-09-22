"""Read-only review of semantic contradictions. No grant or execution interface."""
from dataclasses import dataclass, field
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from ..understanding.role_checkpoint import _json
from ..understanding.semantic_checkpoint import checkpoint_semantic
from ..understanding.semantic_study import (
    SEMANTIC_EVALUATOR_VERSION,
    SemanticClaim,
    SemanticStudy,
)
from .decision import ShadowDecision


@dataclass(frozen=True, slots=True)
class SemanticReview:
    tenant_id: str
    company: str
    source_id: str
    checkpoint_sha256: str
    contradicted_hypotheses: tuple[UUID, ...]
    evidence_ids: tuple[UUID, ...]
    authorization_ids: tuple[str, ...]
    decision: ShadowDecision | None
    audit_id: str
    evaluated_claims: tuple[SemanticClaim, ...]
    evidence_classes: tuple[tuple[UUID, str], ...]
    evidence_origins: tuple[tuple[UUID, tuple[tuple[str, str], ...]], ...]
    evaluator_version: str = field(default=SEMANTIC_EVALUATOR_VERSION, init=False)
    execution_allowed: bool = field(default=False, init=False)
    execution_status: str = field(default='not_attempted', init=False)


def review_semantic_study(study, *, tenant_id, company, source_id):
    """Return a reproducible decision/audit record, not a durable audit service.

    A contradiction warrants review, not a declaration of fraud, financial loss
    or business truth. No KnowledgeEntry is fabricated for the recommendation.
    """
    if type(study) is not SemanticStudy or (tenant_id, company, source_id) != (
            study.base.tenant, study.base.company, study.base.source):
        raise ValueError('review scope mismatch')
    fingerprint = sha256(checkpoint_semantic(study).encode()).hexdigest()
    previously_validated = {c.hypothesis.hypothesis_id for revision in study.history
                            for c in revision.claims if c.hypothesis.status == 'validated'}
    # Eliminating an unsupported candidate is ordinary discovery, not an anomaly.
    evaluated = study.claims()
    anchor_origins = {o.evidence.evidence_id: study._origin(o.evidence.evidence_id).roots
                      for o in study.evidence_snapshot()}

    def independent_disagreement(claim):
        support = [{root[0] for root in anchor_origins.get(k, ())}
                   for k in claim.independent]
        against = [{root[0] for root in anchor_origins.get(k, ())}
                   for k in claim.contradicting]
        # One shared collector must not hide a different independent witness.
        # Compare witnesses, not the union of every collector on each side.
        return any(a and b and a.isdisjoint(b) for a in support for b in against)

    claims = tuple(c for c in evaluated if c.hypothesis.status == 'invalidated'
                   and c.contradicting and (c.hypothesis.hypothesis_id in previously_validated
                                           or independent_disagreement(c)))
    ids = tuple(sorted(c.hypothesis.hypothesis_id for c in claims))
    # Audit all evaluated states, including UNKNOWN. Keep original validated
    # support when later corrections supersede it in the current evaluation.
    historical = tuple(c for revision in study.history for c in revision.claims
                       if c.hypothesis.hypothesis_id in ids)
    evidence = tuple(sorted({k for c in (*evaluated, *historical) for k in
                            (*c.supporting, *c.contradicting, *c.hypothesis.supporting_evidence)}))
    observations = (study.base.schema, *(o for o, _ in study.base.evidence_snapshot()),
                    *study.evidence_snapshot())
    archived = {o.evidence.evidence_id: o for o in observations}
    # UNKNOWN may have inspected records without a matching independent anchor.
    # Retain all bounded evaluation inputs, not just successful matching evidence.
    evidence = tuple(sorted(set(evidence) | set(archived)))
    if any(k not in archived for k in evidence):
        raise ValueError('review evidence missing')
    authorizations = tuple(sorted({str(o.evidence.payload.get('authorization_id') or
        o.evidence.payload.get('provenance', {}).get('authorization_id'))
        for k, o in archived.items() if k in evidence}))
    classes = tuple((k, str(archived[k].evidence.payload.get('record', {}).get(
        'evidence_class', archived[k].evidence.kind.value))) for k in evidence)
    anchors = {o.evidence.evidence_id for o in study.evidence_snapshot()}
    origins = tuple((k, study._origin(k).roots) for k in evidence if k in anchors)
    audit_id = sha256(_json((tenant_id, company, source_id, fingerprint, ids, evidence,
        authorizations, classes, origins, SEMANTIC_EVALUATOR_VERSION,
        tuple((c.hypothesis.hypothesis_id, c.hypothesis.status) for c in evaluated),
        'not_attempted')).encode()).hexdigest()
    decision = None
    if claims:
        decision = ShadowDecision(uuid5(NAMESPACE_URL, audit_id), tenant_id,
            'request human review of contradictory source evidence',
            'Counterexamples invalidate sample role interpretations. Review the cited '
            'evidence and source corrections; no financial or operational action is authorized.', ())
    # A final snapshot comparison prevents ordinary concurrent archive changes from
    # creating a recommendation attributed to a different reviewed state.
    if sha256(checkpoint_semantic(study).encode()).hexdigest() != fingerprint:
        raise ValueError('study changed during review')
    return SemanticReview(tenant_id, company, source_id, fingerprint, ids, evidence,
                         authorizations, decision, audit_id, evaluated, classes, origins)
