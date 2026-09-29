"""Offline durable recovery reuses canonical originals and graph projection."""
import json
import os
import subprocess
from dataclasses import asdict, fields, replace
from datetime import timedelta
from types import SimpleNamespace

import pytest
import test_packaged_runtime
from semantic_lab import Organization
from test_pilot_recovery import fixture_archive
from test_semantic_restart import PREFIX

from orion.stores.sqlite_checkpoint import SQLiteStudyCheckpointStore
from orion.understanding.role_checkpoint import _plain
from orion.understanding.semantic_rules import RULES

clean_artifact = test_packaged_runtime.clean_artifact


def graph_content(study):
    graph = study.world_model()

    def row(value):
        return {f.name: getattr(value, f.name) for f in fields(value)}

    return _plain({'nodes': sorted((row(n) for n in graph._nodes.values()),
                                  key=lambda n: str(n['node_id'])),
                   'relationships': sorted((row(r) for r in graph._relationships.values()),
                                           key=lambda r: str(r['relationship_id']))})

CHILD = PREFIX + r'''
from orion.stores.sqlite_checkpoint import SQLiteStudyCheckpointStore
from orion.understanding.semantic_study import Instrument,Origin
from orion.understanding.semantic_rules import RULES
for identity,raw in v['origins']:
    archive[('origin',UUID(identity))]=Origin(tuple(tuple(r) for r in raw['roots']),
                                            tuple(UUID(p) for p in raw['parents']))
instruments=tuple(Instrument(i['source_id'],i['resource'],i['provenance_source'],tuple(i['classes']))
                  for i in v['instruments'])
store=SQLiteStudyCheckpointStore(v['database'],read_only=True)
study=store.restore_semantic(tenant_id=v['tenant'],company=v['company'],source_id=v['source'],
    study_id='synthetic-study',instruments=instruments,rules=RULES,evidence_lookup=archive.get)
request=study.base.evidence_snapshot()[0][1]
class NoReader:
    source_id=request.source_id
    def read(self,permit):
        raise AssertionError('restoration created acquisition authority')
try:
    launch_pilot_read(request,authorization_id='historical',lookup=lambda _:None,adapter=NoReader())
except ValueError:
    denied=True
else:
    denied=False
'''


def child_program():
    # Share only this test oracle; the installed child imports no test/checkout code.
    import inspect

    return (CHILD + '\nfrom dataclasses import fields\n'
            + 'from orion.understanding.role_checkpoint import _plain\n'
            + inspect.getsource(graph_content) + r'''
print(json.dumps({'graph':graph_content(study),'denied':denied,
                  'revisions':[r.revision_id for r in study.history],
                  'execution_allowed':False}))
''')


def restore(store, lab, **changes):
    arguments = {'tenant_id': lab.tenant, 'company': lab.company, 'source_id': lab.source,
                 'study_id': 'synthetic-study', 'instruments': lab.instruments, 'rules': RULES,
                 'evidence_lookup': lab.archive.get}
    arguments.update(changes)
    return store.restore_semantic(**arguments)


@pytest.mark.parametrize('variant', ['A', 'B', 'C'])
def test_installed_fresh_process_recovers_current_and_historical_graph(
    tmp_path, clean_artifact, variant,
):
    root, _, python = clean_artifact
    lab = Organization(variant).populate()
    database = tmp_path / 'semantic.sqlite'
    store = SQLiteStudyCheckpointStore(database)
    store.append_semantic(lab.study, study_id='synthetic-study', sequence=1)
    original_ids = [r.revision_id for r in lab.study.history]
    original_evidence = {o.evidence.evidence_id for o in lab.study.evidence_snapshot()}
    if variant == 'B':
        previous = tuple(sorted((o for o in lab.study.evidence_snapshot()
            if o.evidence.payload['record']['dimension'] == 'currency'),
            key=lambda o: (o.evidence.payload['provenance']['source_id'],
                           o.evidence.payload['record']['subject_id'])))
        before = [(c.hypothesis.hypothesis_id, c.hypothesis.status) for c in lab.study.claims()]
        lab.correct('monetary', previous, [8, 11])
        assert before != [(c.hypothesis.hypothesis_id, c.hypothesis.status)
                          for c in lab.study.claims()]
        store.append_semantic(lab.study, study_id='synthetic-study', sequence=2)
        assert [r.revision_id for r in lab.study.history][:len(original_ids)] == original_ids
        assert original_evidence <= {o.evidence.evidence_id for o in lab.study.evidence_snapshot()}
    if variant == 'C':
        assert all(c.hypothesis.status == 'unknown' for c in lab.study.claims())
    filtered = {k: v for k, v in lab.archive.items()
                if not isinstance(k, tuple) or k[0] == 'scope'}
    value = {'archive': fixture_archive(SimpleNamespace(archive=filtered)),
             'database': str(database), 'tenant': lab.tenant, 'company': lab.company,
             'source': lab.source, 'instruments': [asdict(i) for i in lab.instruments],
             'origins': [(str(k[1]), _plain(asdict(v))) for k, v in lab.archive.items()
                         if isinstance(k, tuple) and k[0] == 'origin']}
    expected = graph_content(lab.study)
    revisions = [r.revision_id for r in lab.study.history]
    del lab, store
    result = subprocess.run([str(python), '-I', '-c', child_program()], input=json.dumps(value),
                            cwd=root, env={'PATH': os.defpath}, text=True,
                            capture_output=True, timeout=20, check=False)
    assert result.returncode == 0, result.stderr
    actual = json.loads(result.stdout)
    assert actual['graph'] == expected
    assert actual['revisions'] == revisions
    assert actual['denied'] and actual['execution_allowed'] is False


@pytest.mark.parametrize('failure', ['missing', 'retention-expired', 'changed', 'scope',
                                    'origin', 'policy', 'instrument'])
def test_unavailable_or_changed_dependencies_never_publish_validated_graph(tmp_path, failure):
    lab = Organization().populate()
    store = SQLiteStudyCheckpointStore(tmp_path / 'semantic.sqlite')
    store.append_semantic(lab.study, study_id='synthetic-study', sequence=1)
    obs = lab.study.evidence_snapshot()[0]
    key = obs.evidence.evidence_id
    changes = {}
    if failure in ('missing', 'retention-expired'):
        del lab.archive[key]  # Archive retention returns unavailable, not stored status.
    elif failure == 'changed':
        lab.archive[key] = replace(obs, evidence=replace(obs.evidence,
                                  observed_at=lab.now + timedelta(seconds=1)))
    elif failure in ('scope', 'origin'):
        del lab.archive[(failure, key)]
    elif failure == 'policy':
        changes['rules'] = RULES[:-1]
    else:
        changes['instruments'] = lab.instruments[:-1]
    with pytest.raises(ValueError):
        restore(store, lab, **changes)
