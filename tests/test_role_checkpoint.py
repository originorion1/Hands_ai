"""Offline restart must reconstruct beliefs, not accept persisted truth."""
import json
from dataclasses import replace

import pytest
from test_role_study import Experiment, rows

from orion.understanding.role_checkpoint import (
    checkpoint_sha256,
    checkpoint_study,
    restore_study,
)


def restore(x, payload, *, digest=None, **kwargs):
    scope = {'tenant_id':x.study.tenant,'company':x.study.company,'source_id':x.study.source}
    scope.update(kwargs)
    return restore_study(payload,expected_sha256=digest or checkpoint_sha256(payload),
                         evidence_lookup=x.archive.get,**scope)


def test_restart_from_file_preserves_beliefs_plan_and_provenance(tmp_path):
    x=Experiment()
    observations,req=x.acquire(rows(),selected=(x.fields[0],))
    x.study.observe(observations,request=req)
    payload=checkpoint_study(x.study)
    digest=checkpoint_sha256(payload)  # Trusted index retained separately from the file.
    path=tmp_path/'study.json'
    path.write_text(payload)
    restarted=restore(x,path.read_text(),digest=digest)
    assert restarted.claims()==x.study.claims()
    assert restarted.facts==x.study.facts
    assert checkpoint_study(restarted)==payload
    from datetime import date
    policy={'start':date(2025,1,1),'end':date(2025,1,7),'sensitivity':x.sensitivity}
    assert restarted.next_observation(**policy)==x.study.next_observation(**policy)
    assert x.reads==1


def test_contradiction_and_old_revision_survive_restart(tmp_path):
    x=Experiment()
    x.observe(rows())
    initial=checkpoint_study(x.study)
    x.observe(rows(reverse=True))
    revised=checkpoint_study(x.study)
    (tmp_path/'revision-1.json').write_text(initial)
    (tmp_path/'revision-2.json').write_text(revised)
    old=restore(x,(tmp_path/'revision-1.json').read_text())
    new=restore(x,(tmp_path/'revision-2.json').read_text())
    assert any(c.hypothesis.status=='validated' for c in old.claims() if c.predicate=='precedes')
    assert all(c.hypothesis.status=='invalidated' for c in new.claims() if c.predicate=='precedes')
    assert new.claims()==x.study.claims()
    with pytest.raises(ValueError):
        restore(x,initial,digest=checkpoint_sha256(revised))  # Rollback is not current state.


def test_checkpoint_contains_no_records_grants_or_saved_classifications():
    x=Experiment()
    x.observe(rows())
    payload=checkpoint_study(x.study)
    value=json.loads(payload)
    assert set(value)=={'version','tenant_id','company','source_id','schema','records'}
    assert all(set(r)=={'id','sha256','scope_sha256'} for r in value['records'])
    assert all(word not in payload for word in ('q_731','local-sample','validated','grant','2024-06'))


@pytest.mark.parametrize('scope',[{'tenant_id':'other'},{'company':'other'},
                                   {'source_id':'https://other.test'}])
def test_wrong_binding_rejected(scope):
    x=Experiment()
    with pytest.raises(ValueError):
        restore(x,checkpoint_study(x.study),**scope)


@pytest.mark.parametrize('mutation',['remove','value','scope','timestamp','provenance'])
def test_missing_or_changed_originals_cannot_restore(mutation):
    x=Experiment()
    observations,req=x.observe(rows())
    payload=checkpoint_study(x.study)
    original=observations[0]
    key=original.evidence.evidence_id
    if mutation=='remove':
        del x.archive[key]
    elif mutation=='scope':
        x.archive[('scope',key)]=replace(req,max_records=req.max_records+1)
    else:
        ev=original.evidence
        data=dict(ev.payload)
        if mutation=='value':
            data['record']={**data['record'],x.fields[0]:-9}
        elif mutation=='provenance':
            data['provenance']={**data['provenance'],'authorization_id':'fabricated'}
        else:
            from datetime import timedelta
            ev=replace(ev,observed_at=ev.observed_at+timedelta(seconds=1))
        x.archive[key]=replace(original,evidence=replace(ev,payload=data))
    with pytest.raises(ValueError):
        restore(x,payload)


@pytest.mark.parametrize('mutation',['duplicate','version','claim','schema','scope_hash','oversized'])
def test_malformed_even_trusted_checkpoint_rejected(mutation):
    x=Experiment()
    x.observe(rows())
    value=json.loads(checkpoint_study(x.study))
    if mutation=='duplicate':
        value['records'].append(value['records'][0])
    elif mutation=='version':
        value['version']=True
    elif mutation=='claim':
        value['claims']=['validated']
    elif mutation=='schema':
        value['schema']['sha256']='wrong'
    elif mutation=='scope_hash':
        value['records'][0]['scope_sha256']='wrong'
    else:
        value['records']*=51
    payload=json.dumps(value)
    with pytest.raises(ValueError):
        restore(x,payload)


def test_tampering_and_duplicate_json_fail_before_archive_access():
    x=Experiment()
    payload=checkpoint_study(x.study)
    calls=[]
    for candidate,digest in [(payload+' ',checkpoint_sha256(payload)),
                             ('{"version":1,"version":1}',checkpoint_sha256('{"version":1,"version":1}'))]:
        with pytest.raises(ValueError):
            restore_study(candidate,expected_sha256=digest,tenant_id=x.study.tenant,
                company=x.study.company,source_id=x.study.source,evidence_lookup=lambda key:calls.append(key))
    assert calls==[]


def test_scope_change_invalidates_existing_claims_and_checkpoint():
    x=Experiment()
    observations,req=x.observe(rows())
    x.archive[('scope',observations[0].evidence.evidence_id)]=replace(req,company='other')
    with pytest.raises(ValueError):
        x.study.claims()
    with pytest.raises(ValueError):
        checkpoint_study(x.study)


def test_restored_state_cannot_authorize_another_read():
    x=Experiment()
    x.observe(rows())
    restarted=restore(x,checkpoint_study(x.study))
    before=x.reads
    with pytest.raises(ValueError):
        x.acquire(rows(),lookup=lambda key:restarted)
    assert x.reads==before
