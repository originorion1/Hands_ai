"""Synthetic archives cross a real Python-process restart, never a live connection."""
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from pathlib import Path
from uuid import UUID

import pytest
from test_process_roles import acquire, setup

from orion.history.evidence import _observation_to_data
from orion.understanding.process_checkpoint import checkpoint_process, restore_process
from orion.understanding.role_checkpoint import _plain, checkpoint_sha256


def fixture_archive(x):
    # Test-only export. The production restart contract intentionally exports no records.
    observations=[]
    requests=[]
    for key,value in x.archive.items():
        if type(key) is UUID:
            copied=replace(value,evidence=replace(value.evidence,payload=_plain(value.evidence.payload)))
            observations.append(_observation_to_data(copied))
        else:
            requests.append([str(key[1]),_plain(asdict(value))])
    return {'observations':observations,'requests':requests}


CHILD=r'''
import json,sys
from dataclasses import replace
from datetime import date
from uuid import UUID
from orion.history.evidence import _observation_from_data
from orion.discovery.pilot_metadata import _freeze
from orion.discovery.pilot_read import PilotRequest,launch_pilot_read
from orion.understanding.process_roles import TraceProtocol
from orion.understanding.process_checkpoint import restore_process
v=json.load(sys.stdin)
archive={}
for raw in v['archive']['observations']:
    obs=_observation_from_data(raw)
    obs=replace(obs,evidence=replace(obs.evidence,payload=_freeze(dict(obs.evidence.payload))))
    archive[obs.evidence.evidence_id]=obs
for identity,raw in v['archive']['requests']:
    raw['fields']=tuple(raw['fields'])
    raw['start']=date.fromisoformat(raw['start'])
    raw['end']=date.fromisoformat(raw['end'])
    archive[('scope',UUID(identity))]=PilotRequest(**raw)
study=restore_process(v['checkpoint'],expected_sha256=v['digest'],
    tenant_id=v['tenant'],company=v['company'],source_id=v['source'],
    protocol=TraceProtocol(**v['protocol']),evidence_lookup=archive.get)
base,_,traces=study.evidence_snapshot()
request=traces[0][1] if traces else base.evidence_snapshot()[0][1]
class NoReader:
    source_id=request.source_id
    def read(self,permit):
        raise AssertionError('revoked continuation contacted adapter')
try:
    launch_pilot_read(request,authorization_id='revoked',lookup=lambda key:None,adapter=NoReader())
except ValueError:
    denied=True
else:
    denied=False
print(json.dumps({'claims':[(str(c.hypothesis.hypothesis_id),c.hypothesis.status) for c in study.claims()],
                  'continuation_denied':denied,'execution_allowed':False}))
'''


@pytest.mark.parametrize('contradiction',[False,True,None])
def test_fresh_process_restores_epistemic_state_but_not_authority(tmp_path,contradiction):
    x,other,protocol,study=setup()
    if contradiction is not None:
        acquire(x,other,protocol,study)
    if contradiction:
        acquire(x,other,protocol,study,changed=True)
    checkpoint=checkpoint_process(study)
    payload={'archive':fixture_archive(x),'checkpoint':checkpoint,'digest':checkpoint_sha256(checkpoint),
             'tenant':x.study.tenant,'company':x.study.company,'source':x.study.source,
             'protocol':asdict(protocol)}
    path=tmp_path/'synthetic.json'
    path.write_text(json.dumps(payload))
    expected=[[str(c.hypothesis.hypothesis_id),c.hypothesis.status] for c in study.claims()]
    del study
    result=subprocess.run([sys.executable,'-c',CHILD],input=path.read_text(),text=True,
        capture_output=True,timeout=20,check=False,env={'PATH':os.defpath,'PYTHONPATH':str(Path('src').resolve())})
    assert result.returncode==0,result.stderr
    report=json.loads(result.stdout)
    assert report['claims']==expected
    assert report['continuation_denied'] and not report['execution_allowed']


@pytest.mark.parametrize('mutation',['record','trace','protocol','checkpoint','duplicate'])
def test_process_restore_rejects_tampering(mutation):
    x,other,p,s=setup()
    traces,_req,_=acquire(x,other,p,s)
    checkpoint=checkpoint_process(s)
    digest=checkpoint_sha256(checkpoint)
    if mutation=='checkpoint':
        checkpoint+=' '
    elif mutation=='protocol':
        p=replace(p,source_id='https://other.test')
    elif mutation=='duplicate':
        data=json.loads(checkpoint)
        data['traces'].append(data['traces'][0])
        checkpoint=json.dumps(data)
        digest=checkpoint_sha256(checkpoint)
    else:
        key=traces[0].evidence.evidence_id if mutation=='trace' else x.study.evidence_snapshot()[0][0].evidence.evidence_id
        del x.archive[key]
    with pytest.raises(ValueError):
        restore_process(checkpoint,expected_sha256=digest,tenant_id=x.study.tenant,
                        company=x.study.company,source_id=x.study.source,protocol=p,evidence_lookup=x.archive.get)


def test_reference_checkpoint_contains_no_record_values_or_grants():
    x,other,p,s=setup()
    acquire(x,other,p,s)
    payload=checkpoint_process(s)
    assert all(word not in payload for word in ('q_731','trace-grant','2024-06-03','validated'))
