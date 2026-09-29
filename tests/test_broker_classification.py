"""Local policy checks; collector honesty and hostile same-UID isolation are unproven."""
import hashlib

import pytest
from test_supervised_broker import Harness

from orion.pilot import broker_worker
from orion.pilot.broker import Broker
from orion.pilot.broker_contract import authenticate, digest
from orion.understanding.role_checkpoint import _json


@pytest.mark.parametrize('classification', [None, 'hidden', 'unclassified', 'personal',
    'financial', 'employee', 'customer', 'payment', 'credential', 'authentication',
    'secret', 'sensitive', 'PUBLIC', True, {'classification': 'public'}])
def test_nonpublic_policy_denied_before_credentials_or_source(tmp_path, classification, monkeypatch):
    h = Harness(tmp_path / 'policy')
    h.config['field_classifications']['f_d'] = classification
    monkeypatch.delenv('BROKER_AUTH_KEY', raising=False)
    monkeypatch.delenv('BROKER_SOURCE_SECRET', raising=False)
    h.source.unlink()
    with pytest.raises(ValueError, match='field classification denied'):
        Broker(h.config, h.state)
    assert not (h.state / 'broker.db').exists()


@pytest.mark.parametrize('mutation', ['missing_policy', 'missing_field', 'extra_field', 'old_version'])
def test_missing_or_ambiguous_policy_cannot_start(tmp_path, mutation):
    h = Harness(tmp_path / mutation)
    if mutation == 'missing_policy':
        del h.config['field_classifications']
    elif mutation == 'old_version':
        h.config['version'] = 'local-broker-v1'
    elif mutation == 'missing_field':
        del h.config['field_classifications']['f_d']
    else:
        h.config['field_classifications']['*'] = 'public'
    try:
        assert h.start()['status'] == 'broker_blocked'
        assert not (h.state / 'broker.db').exists()
    finally:
        h.close()


def test_application_cannot_replace_classification_or_reuse_other_policy_token(tmp_path):
    h = Harness(tmp_path / 'app')
    try:
        h.start(); h.control('arm')
        message = h.message()
        message['field_classifications'] = {'f_d': 'public'}
        assert h.send(message)['budget']['attempts'] == 0
        changed = dict(h.config, field_classifications={'f_d': 'public'})
        message = h.message()
        message['grant_token'] = authenticate(h.key, 'read_grant', digest(changed))
        result = h.send(message)
        assert result['status'] == 'denied' and result['budget']['attempts'] == 0
    finally:
        h.close()


def test_worker_rechecks_policy_before_source_io(tmp_path, monkeypatch):
    h = Harness(tmp_path / 'worker')
    bootstrap = {'grant': h.config['grant'], 'request': h.message()['request'],
        'path': str(h.source), 'source_digest': h.config['source_digest'],
        'protocol': h.config['protocol'], 'secret': h.secret,
        'field_classifications': dict(h.config['field_classifications'], f_d='secret')}
    calls = []
    monkeypatch.setattr(broker_worker, 'private_bytes', lambda *args: calls.append(args))
    with pytest.raises(ValueError, match='field classification denied'):
        broker_worker.acquire(bootstrap)
    assert calls == []


def test_mutated_policy_cannot_continue_or_restore_authority(tmp_path, monkeypatch):
    h = Harness(tmp_path / 'mutation')
    monkeypatch.setenv('BROKER_AUTH_KEY', h.key.decode())
    monkeypatch.setenv('BROKER_SOURCE_SECRET', h.secret)
    b = Broker(h.config, h.state)
    b.armed = True  # Trusted-owner test setup, not an application wire operation.
    original = h.message()
    h.config['field_classifications']['f_d'] = 'secret'
    result = b.handle(original)
    assert result['status'] == 'denied' and result['budget']['attempts'] == 0
    with pytest.raises(ValueError, match='field classification denied'):
        Broker(h.config, h.state, expected_head=b.journal.head)


def test_empty_second_format_still_requires_exact_column_scope(tmp_path):
    h = Harness(tmp_path / 'columns', 'local_columns_v1', rows=[])
    envelope = {'resource': h.request.resource, 'credential_digest': hashlib.sha256(h.secret.encode()).hexdigest(),
                'columns': list(h.request.fields) + ['f_unapproved'], 'values': []}
    h.source.write_text(_json(envelope))
    h.config['source_digest'] = hashlib.sha256(h.source.read_bytes()).hexdigest()
    try:
        h.start(); h.control('arm')
        result = h.send(h.message())
        assert result['status'] == 'denied' and result['budget']['attempts'] == 1
        assert 'observations' not in result
    finally:
        h.close()
