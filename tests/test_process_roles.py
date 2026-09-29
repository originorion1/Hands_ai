"""Opaque business schema; meaning comes only from a separate local trace protocol."""
from dataclasses import replace
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from test_erpnext_metadata_adapter import FakeResponse
from test_pilot_metadata import NOW
from test_role_study import Experiment, rows
from test_unfamiliar_schema import UnfamiliarEnvironment, discover

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.discovery.pilot_read import PilotAuthorization, PilotRequest, launch_pilot_read
from orion.discovery.read_window import ReviewedReadWindow
from orion.understanding.process_roles import (
    RELATIONAL,
    TEMPORAL,
    ProcessRoleStudy,
    TraceProtocol,
)
from orion.understanding.role_study import RoleStudy


def setup(reverse=False):
    x=Experiment()
    other=UnfamiliarEnvironment(72)
    main=x.environment

    class Environment:
        def open(self, req, timeout):
            if urlsplit(req.full_url).path=='/api/resource/DocType':
                return FakeResponse({'data':[{'name':r} for r in sorted((main.resource,other.resource))]},
                                    url=req.full_url)
            name=parse_qs(urlsplit(req.full_url).query)['doctype'][0]
            return (main if name==main.resource else other).open(req,timeout)
    discovered=discover(Environment())
    x.archive.update({o.evidence.evidence_id:o for o in discovered.observations})
    x.study=RoleStudy(discovered.observations[0],evidence_lookup=x.archive.get)
    x.observe(rows(reverse))
    protocol=TraceProtocol('https://trace.example.test','p_87ac','independent-local-trace-v1')
    study=ProcessRoleStudy(x.study,protocol=protocol,evidence_lookup=x.archive.get)
    return x,other,protocol,study


def acquire(x,other,protocol,study, *, temporal=True, changed=False, lookup=None, clock=lambda:NOW,
            subject_resource=None, affected_resource=None, same_time=False):
    fields=TEMPORAL if temporal else RELATIONAL
    req=PilotRequest(x.study.tenant,x.study.company,protocol.source_id,protocol.resource,fields,
        'recorded_on',date(2024,6,1),date(2024,6,7),2)
    grant=PilotAuthorization('trace-grant',protocol.source_id,
        ReviewedReadWindow(req.tenant_id,req.company,req.resource,req.fields,req.date_field,
                           req.start,req.end,NOW+timedelta(hours=1)),
        'trace_id','partition',protocol.provenance_source,EvidenceKind.EXPERIMENT,2)
    calls=[]

    class Reader:
        source_id=protocol.source_id

        def read(self,permit):
            permit.check(self.source_id)
            permit.claim_io(self.source_id)
            calls.append(1)
            result=[]
            for i in range(2):
                # These event times and affected objects come from the independent
                # fixture trace stream, not from inspecting business field values.
                record={'trace_id':f't_{i}_{changed}_{temporal}','partition':req.company,
                    'recorded_on':'2024-06-03','subject_source':x.study.source,
                    'subject_resource':subject_resource or x.resource,'subject_id':str(i)}
                if temporal:
                    record['occurred_on']='2024-06-03' if same_time else (
                        '2024-06-02' if changed else '2024-06-01')
                else:
                    record['affected_resource']=affected_resource or other.resource
                    record['affected_id']='q_999' if changed else 'q_731'
                result.append(Observation(Evidence(EvidenceKind.EXPERIMENT,protocol.provenance_source,
                    {'resource':protocol.resource,'record':record},observed_at=clock(),tenant_id=req.tenant_id)))
            return tuple(result)
    observations=launch_pilot_read(req,authorization_id='trace-grant',lookup=lookup or (lambda k:grant),
                                  adapter=Reader(),clock=clock)
    for obs in observations:
        x.archive[obs.evidence.evidence_id]=obs
        x.archive[('scope',obs.evidence.evidence_id)]=req
    study.observe_traces(observations,request=req)
    return observations,req,calls


def test_independent_traces_validate_operational_dates_without_name_mapping():
    x,other,protocol,study=setup()
    assert all(c.hypothesis.status=='unknown' for c in study.claims())
    sensitivity={(protocol.resource,f):'public' for f in (*TEMPORAL,*RELATIONAL)}
    plan=study.next_observation(start=date(2024,6,1),end=date(2024,6,7),sensitivity=sensitivity)
    assert plan.fields==TEMPORAL and not plan.execution_allowed and plan.max_records==2
    obs,req,calls=acquire(x,other,protocol,study)
    assert (req.fields,req.start,req.end,req.max_records)==(plan.fields,plan.start,plan.end,plan.max_records)
    claims=study.claims()
    validated=[c for c in claims if c.hypothesis.status=='validated']
    assert {(c.fields[0],c.predicate) for c in validated}=={
        (x.fields[1],'event_date'),(x.fields[2],'recording_date')}
    assert all('commercial_meaning_unknown' in c.unknowns for c in claims)
    for c in validated:
        assert {o.evidence.evidence_id for o in obs}<=set(c.supporting)
        assert len(c.supporting)==4  # Two record and two trace observations.
        node=study.world_model().get_node(c.hypothesis.hypothesis_id,tenant_id=x.study.tenant)
        assert node and node.attributes['sample_only']
    assert calls==[1]


def test_changing_business_evidence_changes_date_role_without_changing_names():
    a,other,p,s=setup()
    b,other2,p2,s2=setup(reverse=True)
    acquire(a,other,p,s)
    acquire(b,other2,p2,s2)
    def selected(study):
        return {c.fields[0] for c in study.claims() if c.predicate=='event_date'
                and c.hypothesis.status=='validated'}
    assert selected(s)=={a.fields[1]}
    assert selected(s2)=={b.fields[2]}


def test_later_contradictory_trace_invalidates_prior_interpretation():
    x,other,p,s=setup()
    acquire(x,other,p,s)
    first=next(c for c in s.claims() if c.hypothesis.status=='validated' and c.predicate=='event_date')
    acquire(x,other,p,s,changed=True)
    revised=next(c for c in s.claims() if c.hypothesis.hypothesis_id==first.hypothesis.hypothesis_id)
    assert revised.hypothesis.status=='invalidated' and revised.supporting and revised.contradicting


def test_reference_role_requires_independent_affected_object_evidence():
    x,other,p,s=setup()
    assert all(c.hypothesis.status=='unknown' for c in s.claims())
    acquire(x,other,p,s,temporal=False)
    c=next(c for c in s.claims() if c.fields==(x.fields[3],))
    assert c.hypothesis.status=='validated' and c.predicate=='affected_object_reference'
    assert 'causation_unproven' in c.unknowns
    acquire(x,other,p,s,temporal=False,changed=True)
    revised=next(c for c in s.claims() if c.fields==(x.fields[3],))
    assert revised.hypothesis.status=='invalidated'


def test_equal_candidate_dates_remain_ambiguous():
    x,other,p,s=setup()
    # Use a new study with only the indistinguishable version, not mixed revisions.
    x.study=RoleStudy(x.study.schema,evidence_lookup=x.archive.get)
    x.observe([(4,'2024-06-03','2024-06-03','q_731'),(7,'2024-06-03','2024-06-03','q_731')])
    s=ProcessRoleStudy(x.study,protocol=p,evidence_lookup=x.archive.get)
    acquire(x,other,p,s)
    matches=[c for c in s.claims() if c.predicate=='recording_date' and c.resource==x.resource]
    assert len(matches)==2 and all(c.hypothesis.status=='unknown' for c in matches)
    assert all('multiple_matching_fields' in c.unknowns for c in matches)


def test_changing_subject_revisions_requires_version_anchor():
    x,other,p,s=setup()
    x.observe(rows(reverse=True))
    acquire(x,other,p,s)
    assert not any(c.hypothesis.status=='validated' for c in s.claims())
    assert any('subject_revision_ambiguous' in c.unknowns for c in s.claims())


@pytest.mark.parametrize('part',['tenant_id','company','source_id','resource'])
def test_wrong_trace_scope_rejected(part):
    x,other,p,s=setup()
    obs,req,_=acquire(x,other,p,s)
    with pytest.raises(ValueError):
        s.observe_traces(obs,request=replace(req,**{part:'other'}))


@pytest.mark.parametrize('change',['provenance','scope','remove'])
def test_archive_tampering_invalidates_claims(change):
    x,other,p,s=setup()
    obs,req,_=acquire(x,other,p,s)
    key=obs[0].evidence.evidence_id
    if change=='scope':
        x.archive[('scope',key)]=replace(req,company='other')
    elif change=='remove':
        del x.archive[key]
    else:
        x.archive[key]=replace(obs[0],evidence=replace(obs[0].evidence,source='forged'))
    with pytest.raises(ValueError):
        s.claims()


@pytest.mark.parametrize('argument',['subject_resource','affected_resource'])
def test_undiscovered_resources_cannot_supply_roles(argument):
    x,other,p,s=setup()
    with pytest.raises(ValueError):
        acquire(x,other,p,s,temporal=False,**{argument:'not-discovered'})
    assert all(c.hypothesis.status=='unknown' for c in s.claims())


def test_sensitive_and_missing_classifications_deny_proposals():
    _x,_other,p,s=setup()
    for sensitivity in ({},{(p.resource,f):'sensitive' for f in (*TEMPORAL,*RELATIONAL)}):
        assert s.next_observation(start=date(2024,6,1),end=date(2024,6,7),sensitivity=sensitivity) is None
    with pytest.raises(ValueError):
        s.next_observation(start=date(2024,1,1),end=date(2024,12,31),sensitivity={})


def test_expired_missing_and_metadata_grants_cannot_collect_trace_evidence():
    from test_pilot_metadata import grant
    x,other,p,s=setup()
    for lookup in (lambda k:None,lambda k:grant()):
        with pytest.raises(ValueError):
            acquire(x,other,p,s,lookup=lookup)
    with pytest.raises(ValueError):
        acquire(x,other,p,s,clock=lambda:NOW+timedelta(hours=1))
    assert all(c.hypothesis.status=='unknown' for c in s.claims())


def test_replaying_same_trace_does_not_change_claims():
    x,other,p,s=setup()
    obs,req,_=acquire(x,other,p,s)
    before=s.claims()
    s.observe_traces(obs,request=req)
    assert s.claims()==before
    assert all(not hasattr(s,name) for name in ('execute','connect','authorize','write'))


def test_same_reference_id_in_multiple_resources_does_not_choose_target():
    x,other,p,s=setup()
    acquire(x,other,p,s,temporal=False)
    acquire(x,other,p,s,temporal=False,affected_resource=x.resource)
    c=next(c for c in s.claims() if c.fields==(x.fields[3],))
    assert c.hypothesis.status=='unknown'
    assert 'affected_resource_ambiguous' in c.unknowns
    assert 'subject_revision_ambiguous' not in c.unknowns


def test_unmatched_subjects_and_single_subject_cannot_validate():
    x,other,p,s=setup()
    observations,req,_=acquire(x,other,p,s)
    s=ProcessRoleStudy(x.study,protocol=p,evidence_lookup=x.archive.get)
    s.observe_traces(observations[:1],request=req)
    assert not any(c.hypothesis.status=='validated' for c in s.claims())
    c=ProcessRoleStudy(x.study,protocol=p,evidence_lookup=x.archive.get)
    acquire(x,other,p,c,subject_resource=other.resource)
    assert all(claim.hypothesis.status=='unknown' for claim in c.claims())


def test_post_response_revocation_cannot_admit_trace():
    x,_other,p,s=setup()
    fields=TEMPORAL
    req=PilotRequest(x.study.tenant,x.study.company,p.source_id,p.resource,fields,
                    'recorded_on',date(2024,6,1),date(2024,6,7),2)
    grant=PilotAuthorization('trace-grant',p.source_id,
        ReviewedReadWindow(req.tenant_id,req.company,req.resource,req.fields,req.date_field,
                           req.start,req.end,NOW+timedelta(hours=1)),
        'trace_id','partition',p.provenance_source,EvidenceKind.EXPERIMENT,2)
    state={'grant':grant,'calls':0}
    class Reader:
        source_id=p.source_id
        def read(self,permit):
            permit.check(self.source_id)
            permit.claim_io(self.source_id)
            state['calls']+=1
            state['grant']=None
            return ()
    with pytest.raises(ValueError):
        launch_pilot_read(req,authorization_id='trace-grant',lookup=lambda k:state['grant'],
                         adapter=Reader(),clock=lambda:NOW)
    assert state['calls']==1 and not any(c.supporting for c in s.claims())


def test_trace_planner_requires_exact_budget_and_skips_observed_window():
    x,other,p,s=setup()
    sensitivity={(p.resource,f):'public' for f in TEMPORAL}
    scope={'start':date(2024,6,1),'end':date(2024,6,7),'sensitivity':sensitivity}
    assert s.next_observation(**scope,budget=13) is None
    assert s.next_observation(**scope,budget=14).fields==TEMPORAL
    acquire(x,other,p,s)
    assert s.next_observation(**scope,budget=14) is None


def test_identical_event_and_recording_times_do_not_disambiguate_role():
    x,other,p,s=setup()
    acquire(x,other,p,s,same_time=True)
    matches=[c for c in s.claims() if c.fields==(x.fields[2],)]
    assert len(matches)==2 and all(c.hypothesis.status=='unknown' for c in matches)
    assert all('indistinguishable_trace_roles' in c.unknowns for c in matches)
