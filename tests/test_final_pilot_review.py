from dataclasses import replace
from types import MappingProxyType
from uuid import uuid4

import pytest
from semantic_lab import Organization

from orion.demo import run_mock_erpnext_shadow_demo
from orion.shadow.semantic_review import review_semantic_study
from orion.understanding.hypotheses import Hypothesis, generate_hypotheses
from orion.understanding.role_checkpoint import checkpoint_sha256
from orion.understanding.semantic_checkpoint import checkpoint_semantic, restore_semantic
from orion.validation.claims import Assurance, validate_hypothesis


def review(lab):
    return review_semantic_study(lab.study, tenant_id=lab.tenant,
                                company=lab.company, source_id=lab.source)


def test_restaurant_settlement_discrepancy_through_actual_governed_pipeline():
    # Synthetic restaurant settlement records: opaque business schema, two amounts
    # (37 and 50) and independently recorded reconciliation components. Labels in
    # this test explain the scenario; the engine receives no business field mapping.
    lab = Organization('B')
    assert len(lab.calls) == 3  # Actual metadata launcher and ERP normalization.
    lab.records()  # Separate grants and actual record admission, never direct store injection.
    previous = lab.anchors('monetary')
    original = checkpoint_semantic(lab.study)
    initial = review(lab)
    assert any(c.hypothesis.status == 'validated' for c in lab.study.claims())
    assert initial.execution_status == 'not_attempted'
    assert initial.decision is None  # Rejected alternatives are not false-positive anomalies.
    # Corrected independent settlement evidence now disagrees with the first field.
    lab.correct('monetary', previous, [8, 11])
    final = review(lab)
    assert final.checkpoint_sha256 != initial.checkpoint_sha256
    assert final.contradicted_hypotheses and final.evidence_ids and final.authorization_ids
    assert final.decision is not None and final.decision.execution_allowed is False
    assert final.execution_status == 'not_attempted'
    assert final.decision.knowledge_ids == ()  # No counterfeit validated business knowledge.
    assert all(k in lab.archive for k in final.evidence_ids)
    assert 'Counterexamples' in final.decision.rationale
    # Analysis neither creates source transactions nor performs customer-system actions.
    calls, evidence_count = tuple(lab.calls), len(lab.archive)
    assert review(lab) == final
    assert tuple(lab.calls) == calls and len(lab.archive) == evidence_count
    restored = restore_semantic(original, expected_sha256=checkpoint_sha256(original),
        tenant_id=lab.tenant, company=lab.company, source_id=lab.source,
        instruments=lab.instruments, rules=lab.study.rules, evidence_lookup=lab.archive.get)
    assert review_semantic_study(restored, tenant_id=lab.tenant, company=lab.company,
                                 source_id=lab.source) == initial


def test_unknown_does_not_manufacture_risk_or_recommendation():
    lab = Organization('C').populate()
    result = review(lab)
    assert result.decision is None and not result.evidence_ids
    assert result.execution_status == 'not_attempted'


@pytest.mark.parametrize('dimension', ['tenant_id', 'company', 'source_id'])
def test_review_rejects_scope_escape(dimension):
    lab = Organization().populate()
    args = {'tenant_id': lab.tenant, 'company': lab.company, 'source_id': lab.source}
    args[dimension] = 'other'
    with pytest.raises(ValueError):
        review_semantic_study(lab.study, **args)


def test_review_rejects_missing_evidence():
    lab = Organization().populate()
    del lab.archive[lab.study.evidence_snapshot()[0].evidence.evidence_id]
    with pytest.raises(ValueError):
        review(lab)


@pytest.mark.parametrize('count', [True, 1.0, -1, 2])
def test_validation_rejects_malformed_or_inflated_support(count):
    h = Hypothesis(uuid4(), 't', 'claim', (uuid4(),))
    with pytest.raises(ValueError):
        validate_hypothesis(h, independent_evidence_count=count)


def test_no_evidence_no_validation_and_no_silent_default_promotion():
    h = Hypothesis(uuid4(), 't', 'model assertion', ())
    assert validate_hypothesis(h).status == 'unvalidated'
    h = replace(h, supporting_evidence=(uuid4(),))
    assert validate_hypothesis(h).status == 'unvalidated'
    assert validate_hypothesis(h, independent_evidence_count=1).status == 'validated'
    assert validate_hypothesis(replace(h, status='invalidated'),
                               independent_evidence_count=1).status == 'unvalidated'
    assert validate_hypothesis(h, assurance=Assurance.CRITICAL,
                               independent_evidence_count=1).status == 'escalate'


def test_duplicate_ids_do_not_create_independence():
    key = uuid4()
    with pytest.raises(ValueError):
        validate_hypothesis(Hypothesis(uuid4(), 't', 'claim', (key, key)),
                            independent_evidence_count=2)


def test_demo_ingests_the_same_evidence_it_uses_for_knowledge(monkeypatch):
    from orion import demo
    store = demo.InMemoryEvidenceStore()
    knowledge = demo.KnowledgeStore()
    monkeypatch.setattr(demo, 'InMemoryEvidenceStore', lambda: store)
    monkeypatch.setattr(demo, 'KnowledgeStore', lambda: knowledge)
    run_mock_erpnext_shadow_demo()
    ids = {e.evidence_id for e in store.query(tenant_id='demo-tenant')}
    assert all(set(k.evidence_ids) <= ids for k in knowledge.list(
        tenant_id='demo-tenant', scope='customer'))


def test_immutable_admitted_record_can_generate_structural_hypothesis():
    from orion.contracts import Evidence, EvidenceKind, Observation
    ev = Evidence(EvidenceKind.EXPERIMENT, 'local', MappingProxyType({
        'resource': 'r_01', 'record': MappingProxyType({'name': 'x_01'})}), tenant_id='t')
    hypotheses = generate_hypotheses((Observation(ev),))
    assert len(hypotheses) == 1 and hypotheses[0].supporting_evidence == (ev.evidence_id,)
    assert hypotheses[0].status == 'unvalidated'


def test_immutable_observation_projects_its_actual_record_not_outer_envelope():
    from orion.contracts import Evidence, EvidenceKind, Observation
    from orion.understanding.graph import GraphStore, project_observations
    record = MappingProxyType({'name': 'x_01', 'f_01': 17})
    ev = Evidence(EvidenceKind.EXPERIMENT, 'local', MappingProxyType({
        'resource': 'r_01', 'record': record}), tenant_id='t')
    graph = GraphStore()
    assert project_observations(graph, (Observation(ev),)) == 1
    node = next(iter(graph._nodes.values()))
    assert node.attributes == record and node.provenance_ids == (ev.evidence_id,)


@pytest.mark.parametrize('tenant', [None, '', ' '])
def test_knowledge_promotion_requires_explicit_tenant(tenant):
    from orion.knowledge.promotion import KnowledgeStore
    from orion.validation.claims import ValidationDecision
    h = Hypothesis(uuid4(), tenant, 'claim', (uuid4(),), 'validated')
    with pytest.raises(ValueError):
        KnowledgeStore().promote(h, validation=ValidationDecision(
            h.hypothesis_id, Assurance.LOW, 'validated', 'supplied'), scope='customer')
