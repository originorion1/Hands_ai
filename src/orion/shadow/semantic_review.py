"""Read-only review of semantic contradictions. No grant or execution interface."""
from dataclasses import dataclass
from hashlib import sha256
from uuid import NAMESPACE_URL, UUID, uuid5

from ..understanding.role_checkpoint import _json
from ..understanding.semantic_checkpoint import checkpoint_semantic
from ..understanding.semantic_study import SemanticStudy
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
    execution_status: str = 'not_attempted'


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
    claims = tuple(c for c in study.claims() if c.hypothesis.status == 'invalidated'
                   and c.contradicting and c.hypothesis.hypothesis_id in previously_validated)
    ids = tuple(sorted(c.hypothesis.hypothesis_id for c in claims))
    evidence = tuple(sorted({k for c in claims for k in
                            (*c.supporting, *c.contradicting, *c.hypothesis.supporting_evidence)}))
    observations = (study.base.schema, *(o for o, _ in study.base.evidence_snapshot()),
                    *study.evidence_snapshot())
    archived = {o.evidence.evidence_id: o for o in observations}
    if any(k not in archived for k in evidence):
        raise ValueError('review evidence missing')
    authorizations = tuple(sorted({str(o.evidence.payload.get('authorization_id') or
        o.evidence.payload.get('provenance', {}).get('authorization_id'))
        for k, o in archived.items() if k in evidence}))
    audit_id = sha256(_json((tenant_id, company, source_id, fingerprint, ids, evidence,
                            authorizations, 'not_attempted')).encode()).hexdigest()
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
                         authorizations, decision, audit_id)
