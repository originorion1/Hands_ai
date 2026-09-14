"""No business names or role labels are supplied by the fake organization."""
from dataclasses import replace
from datetime import date, timedelta
from types import MappingProxyType

import pytest
from test_pilot_metadata import NOW
from test_pilot_metadata import grant as metadata_grant
from test_unfamiliar_schema import UnfamiliarEnvironment, discover

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.discovery.pilot_read import PilotAuthorization, PilotRequest, launch_pilot_read
from orion.discovery.read_window import ReviewedReadWindow
from orion.understanding.graph import GraphStatus
from orion.understanding.role_study import RoleStudy


class Experiment:
    def __init__(self, seed=991):
        self.environment = UnfamiliarEnvironment(seed)
        self.discovery = discover(self.environment)
        self.archive = {o.evidence.evidence_id: o for o in self.discovery.observations}
        self.study = RoleStudy(self.discovery.observations[0], evidence_lookup=self.archive.get)
        self.resource = self.discovery.catalog[0]
        self.fields = tuple(c.declaration.name for c in self.discovery.proposals[0].interpretation.candidates)
        self.sensitivity = {(self.resource, f): 'public' for f in self.fields}
        self.reads = 0

    def acquire(self, values, *, lookup=None, tenant=None, resource=None, clock=lambda: NOW, selected=None, window=None, limit=10):
        # A separate, fixture-owned control plane supplies technical partition and
        # identity bindings. These are NOT inferred business roles or metadata grants.
        all_fields = (*self.fields, 'x_81', 'x_92')
        fields = all_fields if selected is None else tuple(dict.fromkeys(
            (*selected, self.fields[1], 'x_81', 'x_92')))
        start, end = window or (date(2024, 1, 1), date(2024, 12, 31))
        req = PilotRequest(tenant or self.study.tenant, self.study.company, self.study.source,
            resource or self.resource, fields, self.fields[1], start, end, limit)
        grant = PilotAuthorization('local-sample', req.source_id,
            ReviewedReadWindow(req.tenant_id, req.company, req.resource, fields,
                req.date_field, req.start, req.end, NOW + timedelta(hours=1)),
            'x_81', 'x_92', 'local-fixture', EvidenceKind.EXPERIMENT, limit)
        parent = self

        class LocalReader:
            source_id = req.source_id

            def read(self, permit):
                permit.check(self.source_id)
                permit.claim_io(self.source_id)
                parent.reads += 1
                return tuple(Observation(Evidence(EvidenceKind.EXPERIMENT, 'local-fixture',
                    {'resource': req.resource, 'record':{key:value for key,value in zip(all_fields,
                        (*row, str(i), req.company), strict=True) if key in fields}},
                    tenant_id=req.tenant_id, observed_at=clock())) for i, row in enumerate(values))

        observations = launch_pilot_read(req, authorization_id=grant.authorization_id,
            lookup=lookup or (lambda key: grant), adapter=LocalReader(), clock=clock)
        for observation in observations:
            self.archive[observation.evidence.evidence_id] = observation
            self.archive[('scope', observation.evidence.evidence_id)] = req
        return observations, req

    def observe(self, values):
        observations, req = self.acquire(values)
        self.study.observe(observations, request=req)
        return observations, req

    def plan(self, **kwargs):
        return self.study.next_observation(start=date(2024, 6, 1), end=date(2024, 6, 7),
            sensitivity=self.sensitivity, **kwargs)


def rows(reverse=False):
    a, b = ('2024-06-01','2024-06-03') if not reverse else ('2024-06-03','2024-06-01')
    return [(4, a, b, 'q_731'), (7, a, b, 'q_731')]


@pytest.mark.parametrize('seed',[11,91,671])
def test_opaque_discovery_to_competing_claims_and_minimum_proposal(seed):
    x = Experiment(seed)
    assert len(x.environment.calls) == 2  # Schema discovery precedes reasoning.
    assert all(c.hypothesis.status == 'unknown' for c in x.study.claims())
    assert len([c for c in x.study.claims() if c.predicate == 'precedes']) == 2
    assert len([c for c in x.study.claims() if 'reference' in c.predicate]) == 2
    assert all(c.hypothesis.supporting_evidence for c in x.study.claims())
    plan = x.plan()
    assert len(plan.fields) == 1 and plan.cost == 2
    assert plan.resource == x.resource and set(plan.fields) <= set(x.fields)
    assert not plan.execution_allowed and plan.authorization_required == 'separate_record_grant'
    assert plan.blockers and x.reads == 0


def test_sample_validation_and_date_evidence_reversal():
    x, y = Experiment(), Experiment()
    x.observe(rows())
    y.observe(rows(reverse=True))
    first = [c for c in x.study.claims() if c.predicate == 'precedes']
    second = [c for c in y.study.claims() if c.predicate == 'precedes']
    assert [c.hypothesis.status for c in first] == ['validated','invalidated']
    assert [c.hypothesis.status for c in second] == ['invalidated','validated']
    assert first[0].fields == second[0].fields  # Only evidence changed.
    assert all('business_meaning_unidentified' in c.unknowns for c in x.study.claims())
    for c in x.study.claims():
        assert c.supporting or c.contradicting
        assert all(key in x.archive for key in (*c.supporting, *c.contradicting))


def test_contradiction_revises_graph_and_preserves_history():
    x = Experiment()
    x.observe(rows())
    claim = next(c for c in x.study.claims() if c.predicate == 'precedes')
    old = x.study.world_model()
    assert old.get_node(claim.hypothesis.hypothesis_id, tenant_id=x.study.tenant).status == GraphStatus.VALIDATED
    x.observe(rows(reverse=True))
    revised = next(c for c in x.study.claims() if c.hypothesis.hypothesis_id == claim.hypothesis.hypothesis_id)
    assert revised.hypothesis.status == 'invalidated'
    assert revised.supporting and revised.contradicting
    new = x.study.world_model().get_node(claim.hypothesis.hypothesis_id, tenant_id=x.study.tenant)
    assert new.status == GraphStatus.CONTRADICTED and new.attributes['sample_only']
    assert new.provenance_ids and old.get_node(new.node_id, tenant_id=x.study.tenant).status == GraphStatus.VALIDATED
    assert x.study.world_model().get_node(new.node_id, tenant_id='other') is None


def test_equal_dates_and_misleading_types_do_not_validate():
    x = Experiment()
    x.observe([('not a number','2024-06-01','2024-06-01',None)] * 2)
    assert all(c.hypothesis.status == 'unknown' for c in x.study.claims())


def test_repeated_reference_does_not_mean_customer_ownership_or_causation():
    x = Experiment()
    x.observe(rows())
    c = next(c for c in x.study.claims() if c.predicate == 'repeated_reference')
    assert c.hypothesis.status == 'validated'
    assert 'business_meaning_unidentified' in c.unknowns
    assert not any(word in c.hypothesis.statement for word in ('customer','supplier','ownership','causes'))


def test_single_record_and_repeated_acquisition_do_not_create_independent_support():
    x = Experiment()
    observations, req = x.observe(rows()[:1])
    before = x.study.claims()
    x.study.observe(observations, request=req)
    assert before == x.study.claims()
    # Fresh acquisition of the same identity also cannot supply a second witness.
    observations, req = x.acquire(rows()[:1], clock=lambda: NOW + timedelta(seconds=1))
    x.study.observe(observations, request=req)
    assert not any(c.hypothesis.status == 'validated' for c in x.study.claims())


def test_budget_sensitivity_and_unclassified_fields_fail_closed():
    x = Experiment()
    assert x.plan(budget=1) is None
    x.sensitivity = {}
    assert x.plan() is None
    x.sensitivity = {(x.resource, f):'sensitive' for f in x.fields}
    assert x.plan() is None
    with pytest.raises(ValueError):
        x.plan(max_records=100)


def test_existing_scope_not_requested_again_and_contradiction_new_window_prioritized():
    x = Experiment()
    x.observe(rows())
    x.observe(rows(reverse=True))
    assert x.plan() is None
    plan = x.study.next_observation(start=date(2025,1,1),end=date(2025,1,7),
        sensitivity=x.sensitivity)
    assert set(plan.fields) == set(x.fields[1:3]) and plan.cost == 4


@pytest.mark.parametrize('change', ['tenant','resource','fields','company','source'])
def test_cross_scope_evidence_rejected_atomically(change):
    x = Experiment()
    observations, req = x.acquire(rows())
    updates = {'tenant':{'tenant_id':'other'}, 'resource':{'resource':'other'},
               'fields':{'fields':req.fields[:-1]}, 'company':{'company':'other'},
               'source':{'source_id':'https://other.test'}}
    with pytest.raises(ValueError):
        x.study.observe(observations, request=replace(req, **updates[change]))
    assert all(c.hypothesis.status == 'unknown' for c in x.study.claims())


def test_provenance_tampering_or_unarchived_evidence_rejected():
    x = Experiment()
    observations, req = x.acquire(rows())
    original = observations[0]
    payload = dict(original.evidence.payload)
    payload['provenance'] = MappingProxyType({'authorization_id':'forged'})
    corrupt = replace(original, evidence=replace(original.evidence,payload=payload))
    with pytest.raises(ValueError):
        x.study.observe((corrupt,),request=req)
    with pytest.raises(ValueError):
        RoleStudy(replace(x.discovery.observations[0], observation_id=original.observation_id),
                  evidence_lookup=x.archive.get)


@pytest.mark.parametrize('lookup', [lambda key:None, lambda key:metadata_grant()])
def test_metadata_or_missing_grant_cannot_collect_record_evidence(lookup):
    x = Experiment()
    with pytest.raises(ValueError):
        x.acquire(rows(),lookup=lookup)
    assert x.reads == 0


def test_expiry_and_post_response_revocation_reject():
    x = Experiment()
    with pytest.raises(ValueError):
        x.acquire(rows(),clock=lambda: NOW + timedelta(hours=1))
    assert x.reads == 0
    # Existing launcher guard is reconsulted after local I/O.
    observations, req = x.acquire(rows())
    assert observations
    authorization = PilotAuthorization('local-sample',req.source_id,
        ReviewedReadWindow(req.tenant_id,req.company,req.resource,req.fields,req.date_field,
                          req.start,req.end,NOW+timedelta(hours=1)),
        'x_81','x_92','local-fixture',EvidenceKind.EXPERIMENT,10)
    before = x.reads
    with pytest.raises(ValueError):
        x.acquire(rows(),lookup=lambda key:authorization if x.reads == before else None)
    assert x.reads == before + 1


def test_killer_experiment_collects_only_proposed_fields_plus_required_read_bindings():
    x = Experiment()
    assert all(c.hypothesis.status == 'unknown' for c in x.study.claims())
    plan = x.plan()
    assert plan and x.reads == 0
    # The experiment's independent control plane explicitly approves the proposal
    # and resolves its technical blockers. The study has no grant-issuing method.
    observations, req = x.acquire(rows(), selected=plan.fields,
                                  window=(plan.start, plan.end), limit=plan.max_records)
    assert (req.start, req.end, req.max_records) == (plan.start, plan.end, plan.max_records)
    assert set(req.fields) == {*plan.fields, x.fields[1], 'x_81', 'x_92'}
    x.study.observe(observations, request=req)
    claims = x.study.claims()
    assert any(c.hypothesis.status != 'unknown' for c in claims if set(c.fields) <= set(plan.fields))
    assert all(c.hypothesis.status == 'unknown' for c in claims if not set(c.fields) <= set(req.fields))
    assert all('business_meaning_unidentified' in c.unknowns for c in claims)
    remaining = x.plan()
    assert remaining and remaining.fields != plan.fields and not remaining.execution_allowed
    assert all(name not in RoleStudy.__dict__ for name in ('execute','write','authorize','connect'))


def test_removed_archive_evidence_and_forged_acquisition_scope_fail_closed():
    x = Experiment()
    observations, req = x.acquire(rows())
    x.archive[('scope', observations[0].evidence.evidence_id)] = replace(req, company='other')
    with pytest.raises(ValueError):
        x.study.observe(observations, request=req)
    assert all(c.hypothesis.status == 'unknown' for c in x.study.claims())
    x.archive[('scope', observations[0].evidence.evidence_id)] = req
    x.study.observe(observations, request=req)
    del x.archive[observations[0].evidence.evidence_id]
    with pytest.raises(ValueError):
        x.study.claims()


def test_fresh_construction_replay_is_deterministic_and_binding_read_only():
    a, b = Experiment(), Experiment()
    a.observe(rows())
    b.observe(rows())
    assert a.study.facts == b.study.facts and a.study.claims() == b.study.claims()
    for attribute in ('tenant', 'company', 'source', 'schema', 'facts'):
        with pytest.raises(AttributeError):
            setattr(a.study, attribute, 'other')


def test_hypothesis_combinatorics_are_bounded():
    env = UnfamiliarEnvironment(144)
    env.names = tuple(f'z_{i:08x}' for i in range(22))
    env.kinds = ('Date',) * len(env.names)
    result = discover(env)
    archive = {o.evidence.evidence_id:o for o in result.observations}
    with pytest.raises(ValueError, match='hypothesis budget'):
        RoleStudy(result.observations[0],evidence_lookup=archive.get)


def test_expiry_after_local_response_cannot_become_evidence():
    x = Experiment()
    before = dict(x.archive)
    with pytest.raises(ValueError):
        x.acquire(rows(),clock=lambda: NOW if x.reads == 0 else NOW + timedelta(hours=1))
    assert x.reads == 1 and x.archive == before
