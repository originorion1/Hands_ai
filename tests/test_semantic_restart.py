"""Fresh interpreter recovery of the three synthetic organizations."""
import json
import os
import subprocess
import sys
from dataclasses import asdict
from pathlib import Path
from types import SimpleNamespace

import pytest
from semantic_lab import Organization
from test_pilot_recovery import CHILD, fixture_archive

from orion.understanding.role_checkpoint import _plain, checkpoint_sha256
from orion.understanding.semantic_checkpoint import checkpoint_semantic

# Reuse the existing test-only canonical observation/scope archive decoder.
PREFIX = CHILD.split('study=restore_process')[0]
SEMANTIC_CHILD = PREFIX + r'''
from orion.understanding.semantic_study import Instrument,Origin
from orion.understanding.semantic_rules import RULES
from orion.understanding.semantic_checkpoint import restore_semantic
for identity,raw in v['origins']:
    archive[('origin',UUID(identity))]=Origin(tuple(tuple(r) for r in raw['roots']),
                                             tuple(UUID(p) for p in raw['parents']))
instruments=tuple(Instrument(i['source_id'],i['resource'],i['provenance_source'],tuple(i['classes']))
                  for i in v['instruments'])
study=restore_semantic(v['checkpoint'],expected_sha256=v['digest'],
    tenant_id=v['tenant'],company=v['company'],source_id=v['source'],
    instruments=instruments,rules=RULES,evidence_lookup=archive.get)
request=study.base.evidence_snapshot()[0][1]
class NoReader:
    source_id=request.source_id
    def read(self,permit):
        raise AssertionError('restoration created authority')
try:
    launch_pilot_read(request,authorization_id='revoked',lookup=lambda _:None,adapter=NoReader())
except ValueError:
    denied=True
else:
    denied=False
print(json.dumps({'claims':[(str(c.hypothesis.hypothesis_id),c.hypothesis.status)
                           for c in study.claims()],
                  'revisions':[r.revision_id for r in study.history],
                  'continuation_denied':denied,'execution_allowed':False}))
'''


@pytest.mark.parametrize('organization', ['A', 'B', 'C'])
def test_semantic_restart_in_a_new_process_without_authority(tmp_path, organization):
    lab = Organization(organization).populate()
    if organization == 'B':
        previous = tuple(sorted((o for o in lab.study.evidence_snapshot()
            if o.evidence.payload['record']['dimension'] == 'currency'),
            key=lambda o: (o.evidence.payload['provenance']['source_id'],
                           o.evidence.payload['record']['subject_id'])))
        lab.correct('monetary', previous, [8, 11])
    checkpoint = checkpoint_semantic(lab.study)
    filtered = {k: v for k, v in lab.archive.items() if not isinstance(k, tuple) or k[0] == 'scope'}
    archive = fixture_archive(SimpleNamespace(archive=filtered))
    value = {'archive': archive, 'checkpoint': checkpoint, 'digest': checkpoint_sha256(checkpoint),
             'tenant': lab.tenant, 'company': lab.company, 'source': lab.source,
             'origins': [(str(k[1]), _plain(asdict(v))) for k, v in lab.archive.items()
                         if isinstance(k, tuple) and k[0] == 'origin'],
             'instruments': [asdict(i) for i in lab.instruments]}
    path = tmp_path / 'synthetic-only.json'
    path.write_text(json.dumps(value))
    expected = [[str(c.hypothesis.hypothesis_id), c.hypothesis.status] for c in lab.study.claims()]
    revisions = [r.revision_id for r in lab.study.history]
    del lab
    result = subprocess.run([sys.executable, '-c', SEMANTIC_CHILD], input=path.read_text(),
        text=True, capture_output=True, timeout=20, check=False,
        env={'PATH': os.defpath, 'PYTHONPATH': str(Path('src').resolve())})
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual['claims'] == expected and actual['revisions'] == revisions
    assert actual['continuation_denied'] and actual['execution_allowed'] is False
