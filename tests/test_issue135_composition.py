import json
from dataclasses import asdict, replace
from datetime import timedelta

import pytest
from semantic_lab import Organization
from test_pilot_read import NOW

from orion.pilot.journal import AttemptJournal, TransportLimits, grant_digest
from orion.shadow.semantic_review import review_semantic_study
from orion.understanding.role_checkpoint import checkpoint_sha256
from orion.understanding.semantic_checkpoint import checkpoint_semantic, restore_semantic


def review(lab, study=None):
    return review_semantic_study(study or lab.study, tenant_id=lab.tenant,
                                company=lab.company, source_id=lab.source)


def restore(lab, payload):
    return restore_semantic(payload, expected_sha256=checkpoint_sha256(payload),
        tenant_id=lab.tenant, company=lab.company, source_id=lab.source,
        instruments=lab.instruments, rules=lab.study.rules, evidence_lookup=lab.archive.get)


def test_conflicting_independent_sources_require_review_before_any_prior_validation():
    lab = Organization('B')
    lab.records()
    first = lab.anchors('monetary', observe=False)
    second = lab.anchors('monetary', override=[8, 11], observe=False)
    lab.study.observe(first[:2])
    lab.study.observe(second[2:])
    outcome = review(lab)
    assert outcome.decision is not None and outcome.contradicted_hypotheses
    assert outcome.execution_allowed is False and outcome.execution_status == 'not_attempted'
    for key in outcome.contradicted_hypotheses:
        c = next(c for c in outcome.evaluated_claims if c.hypothesis.hypothesis_id == key)
        assert c.supporting and c.contradicting and c.hypothesis.status == 'invalidated'
    assert {o.evidence.evidence_id for o in (*first[:2], *second[2:])} <= set(outcome.evidence_ids)
    assert review(lab, restore(lab, checkpoint_semantic(lab.study))) == outcome


def test_review_preserves_both_original_and_corrected_evidence():
    lab = Organization('B')
    lab.records()
    original = lab.anchors('monetary')
    corrected = lab.correct('monetary', original, [8, 11])
    outcome = review(lab)
    assert {o.evidence.evidence_id for o in (*original, *corrected)} <= set(outcome.evidence_ids)
    assert outcome.evidence_origins and outcome.evaluator_version
    assert outcome.evidence_classes and outcome.authorization_ids
    restored = restore(lab, checkpoint_semantic(lab.study))
    assert review(lab, restored) == outcome


def test_unknown_audit_retains_scope_evidence_and_explicit_state():
    lab = Organization('C').populate()
    result = review(lab)
    assert result.decision is None and result.evidence_ids and result.authorization_ids
    assert result.evidence_classes and result.evaluator_version
    assert all(c.hypothesis.status == 'unknown' for c in result.evaluated_claims)
    with pytest.raises(ValueError):
        replace(result, execution_allowed=True)


@pytest.mark.parametrize('change', ['format', 'evaluator', 'runtime_evaluator'])
def test_checkpoint_rejects_evaluator_and_format_changes(change, monkeypatch):
    from orion.understanding import semantic_checkpoint
    lab = Organization().populate()
    payload = checkpoint_semantic(lab.study)
    data = json.loads(payload)
    if change == 'format':
        data['version'] = 1
    elif change == 'evaluator':
        data['evaluator_version'] = 'unreviewed'
    else:
        monkeypatch.setattr(semantic_checkpoint, 'SEMANTIC_EVALUATOR_VERSION', 'future-evaluator')
    payload = json.dumps(data)
    with pytest.raises(ValueError, match='contract mismatch'):
        restore(lab, payload)


def test_budgeted_metadata_to_semantics_to_shadow_review_and_stop(tmp_path):
    # Reuse #131's real journal and budgeted transport with exclusively local responses.
    # This key protects only synthetic test artifacts and is generated per test.
    import secrets
    key = secrets.token_bytes(32)
    journals = {}
    settings = TransportLimits(20, 8192, 65536, 1310720, 1, 2, NOW + timedelta(hours=1))

    def factory(grant):
        binding = grant_digest(grant)
        if binding not in journals:
            journals[binding] = AttemptJournal(tmp_path / (binding + '.db'), key=key,
                                               binding=binding, limits=settings)
        return journals[binding]

    lab = Organization('B', journal_factory=factory)
    lab.records()
    original = lab.anchors('monetary')
    assert any(c.hypothesis.status == 'validated' for c in lab.study.claims())
    lab.correct('monetary', original, [8, 11])
    result = review(lab)
    assert result.decision and not result.execution_allowed
    assert sum(j.inspect()['attempts'] for j in journals.values()) == len(lab.calls)
    assert all(not j.inspect()['pending'] for j in journals.values())
    calls = tuple(lab.calls)
    for binding, old in tuple(journals.items()):
        restored = AttemptJournal(tmp_path / (binding + '.db'), key=key, binding=binding,
                                  limits=settings, expected_head=old.head)
        assert restored.inspect() == old.inspect()
        restored.stop()
        journals[binding] = restored
    before = checkpoint_semantic(lab.study)
    with pytest.raises(ValueError):
        lab.anchors('monetary')
    assert tuple(lab.calls) == calls and checkpoint_semantic(lab.study) == before
    assert review(lab, restore(lab, before)) == result
    # No record contents or credentials are needed in the review's serialized audit.
    serialized = json.dumps(asdict(result), default=str)
    assert 'component_a' not in serialized and 'component_b' not in serialized


def test_shadow_review_cannot_be_used_as_a_pilot_request_or_grant():
    from orion.discovery.pilot_read import launch_pilot_read
    lab = Organization('B')
    lab.records()
    initial = lab.anchors('monetary')
    lab.correct('monetary', initial, [8, 11])
    artifact = review(lab)

    class NoReader:
        source_id = lab.source

        def read(self, permit):
            raise AssertionError('review artifact crossed authorization boundary')

    with pytest.raises(TypeError, match='explicit pilot request'):
        launch_pilot_read(artifact.decision, authorization_id='proposal',
                         lookup=lambda _: None, adapter=NoReader())
    request = lab.base.evidence_snapshot()[0][1]
    with pytest.raises(ValueError, match='authorization'):
        launch_pilot_read(request, authorization_id='proposal',
                         lookup=lambda _: artifact, adapter=NoReader())


def test_unknown_audit_identifies_every_evaluated_record():
    lab = Organization('C').populate()
    result = review(lab)
    assert {o.evidence.evidence_id for o, _ in lab.base.evidence_snapshot()} <= set(result.evidence_ids)
    assert not result.execution_allowed and result.decision is None


def test_metadata_budget_failure_stops_before_record_collection(tmp_path):
    import secrets
    key = secrets.token_bytes(32)
    budgets = []

    def factory(grant):
        journal = AttemptJournal(tmp_path / 'budget.db', key=key, binding=grant_digest(grant),
            limits=TransportLimits(1, 8192, 65536, 65536, 1, 1, NOW + timedelta(hours=1)))
        budgets.append(journal)
        return journal

    with pytest.raises(ValueError):
        Organization('B', journal_factory=factory)
    assert len(budgets) == 1 and budgets[0].inspect()['attempts'] == 1
    assert not budgets[0].inspect()['pending']
