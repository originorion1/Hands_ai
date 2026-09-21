"""Real child processes, synthetic files only. This is not an OS sandbox test."""
import hashlib
import json
import os
import secrets
import subprocess
import sys
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

import pytest

from orion.contracts import EvidenceKind, utc_now
from orion.discovery.erpnext_live_session import CredentialEnvironmentReferences
from orion.discovery.pilot_read import PilotAuthorization, PilotRequest
from orion.discovery.read_window import ReviewedReadWindow
from orion.pilot.broker import Broker
from orion.pilot.broker_contract import VERSION, authenticate, digest, observations_from
from orion.pilot.journal import JournalDenied, TransportLimits
from orion.understanding.role_checkpoint import _json


class Harness:
    """Independent test supervisor/issuer. Application receives only its read token."""

    def __init__(self, root, protocol='local_rows_v1', *, rows=None, grant=None, request=None):
        self.root = root
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        self.key = secrets.token_hex(32).encode()
        self.secret = 'synthetic-' + secrets.token_hex(16)
        fields = ('f_a', 'f_b', 'f_c', 'f_d')
        self.grant = grant or PilotAuthorization('a_01', 'https://opaque.test',
            ReviewedReadWindow('t_01', 'c_01', 'r_01', fields, 'f_c', date(2024, 6, 1),
                date(2024, 6, 7), utc_now() + timedelta(hours=1)),
            'f_a', 'f_b', 'local-records', EvidenceKind.EXPERIMENT, 2)
        w = self.grant.window
        self.request = request or PilotRequest(w.tenant_id, w.company, self.grant.source_id,
            w.resource, w.fields, w.date_field, w.start, w.end, self.grant.max_records)
        self.rows = rows if rows is not None else [dict(zip(fields, ('x_1', 'c_01', '2024-06-02', 9)))]
        envelope = {'resource': self.request.resource,
                    'credential_digest': hashlib.sha256(self.secret.encode()).hexdigest()}
        if protocol == 'local_rows_v1':
            envelope['rows'] = self.rows
        else:
            envelope.update(columns=list(self.request.fields),
                            values=[[r[f] for f in self.request.fields] for r in self.rows])
        self.source = root / 'synthetic.json'
        self.source.write_text(_json(envelope))
        self.source.chmod(0o600)
        self.config = json.loads(_json({'version': VERSION, 'mode': 'synthetic_read_only',
            'caller': 'app_01', 'operation': 'read', 'grant': asdict(self.grant),
            'field_classifications': {f: 'public' for f in self.grant.window.fields},
            'limits': asdict(TransportLimits(3, 32768, 65536, 196608, 1, 2,
                                             self.grant.window.expires_at)),
            'protocol': protocol, 'secret_reference': 'BROKER_SOURCE_SECRET',
            'auth_reference': 'BROKER_AUTH_KEY', 'source_path': str(self.source),
            'source_digest': hashlib.sha256(self.source.read_bytes()).hexdigest()}))
        self.state = root / 'state'
        self.state.mkdir(mode=0o700)
        self.process = None

    def start(self, head=None, env_changes=None):
        path = self.root / 'config.json'
        path.write_text(_json(self.config)); path.chmod(0o600)
        env = {'PATH': os.defpath, 'PYTHONPATH': str(Path('src').resolve()),
               'BROKER_AUTH_KEY': self.key.decode(), 'BROKER_SOURCE_SECRET': self.secret}
        for k, v in (env_changes or {}).items():
            if v is None:
                env.pop(k, None)
            else:
                env[k] = v
        args = [sys.executable, '-m', 'orion.pilot.broker', '--config', str(path),
                '--state', str(self.state)]
        if head is not None:
            args.extend(('--expected-head', head))
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                         stderr=subprocess.PIPE, env=env, text=True)
        self.last = json.loads(self.process.stdout.readline())
        return self.last

    def send(self, message):
        self.process.stdin.write(_json(message) + '\n'); self.process.stdin.flush()
        self.last = json.loads(self.process.stdout.readline())
        return self.last

    def control(self, command):
        payload = {'control': command, 'nonce': self.last['nonce'], 'head': self.last['head']}
        return self.send(payload | {'mac': authenticate(self.key, 'control', payload)})

    def message(self, identity='q_01'):
        return {'operation': 'read', 'request_id': identity, 'request': json.loads(_json(asdict(self.request))),
                'grant_token': authenticate(self.key, 'read_grant', digest(self.config))}

    def close(self):
        if self.process and self.process.poll() is None:
            self.process.stdin.close()
            value = self.process.stdout.readline()
            if value:
                self.last = json.loads(value)
            self.process.wait(timeout=10)
        if self.process:
            assert self.secret not in self.process.stderr.read()
        return self.last


def test_unsupported_supervised_budget_denies_before_credentials_or_journal(tmp_path, monkeypatch):
    harness = Harness(tmp_path/'unsupported')
    harness.config['limits']['max_requests'] = 49

    def credential_access(*unused):
        pytest.fail('credential resolution preceded journal-capacity validation')

    monkeypatch.setattr(CredentialEnvironmentReferences, 'resolve', credential_access)
    with pytest.raises(JournalDenied, match='supervised request budget exceeds audit capacity'):
        Broker(harness.config, harness.state)
    assert not (harness.state/'broker.db').exists()


@pytest.fixture
def broker(tmp_path):
    lab = Harness(tmp_path / 'broker')
    yield lab
    if lab.process and lab.process.poll() is None:
        lab.close()


@pytest.mark.parametrize('protocol', ['local_rows_v1', 'local_columns_v1'])
def test_two_protocols_actual_supervised_read_admits_canonical_observations(tmp_path, protocol):
    h = Harness(tmp_path / protocol, protocol)
    try:
        assert h.start()['status'] == 'unarmed'
        assert h.send(h.message())['budget']['attempts'] == 0
        assert h.control('arm')['status'] == 'arm'
        result = h.send(h.message())
        assert result['status'] == 'admitted' and result['budget']['attempts'] == 1
        assert not result['execution_allowed'] and not result['allow_live_customer_access']
        observations = observations_from(result['observations'])
        assert len(observations) == 1
        evidence = observations[0].evidence
        assert evidence.payload['record'] == h.rows[0]
        assert evidence.tenant_id == h.request.tenant_id
        assert evidence.payload['provenance']['authorization_id'] == h.grant.authorization_id
        with pytest.raises(TypeError):
            evidence.payload['record']['f_d'] = 99
        assert h.secret not in _json(result) and h.key.decode() not in _json(result)
    finally:
        h.close()


@pytest.mark.parametrize('attack', ['tenant', 'company', 'source', 'resource', 'fields',
    'date', 'operation', 'token', 'malformed', 'credential', 'path', 'url', 'adapter',
    'control', 'review', 'proposal', 'grant'])
def test_application_attacks_denied_before_source_io(broker, attack):
    h = broker; h.start(); h.control('arm')
    message = h.message()
    if attack in ('tenant', 'company', 'source', 'resource'):
        name = {'tenant': 'tenant_id', 'source': 'source_id'}.get(attack, attack)
        message['request'][name] = 'https://other.test' if attack == 'source' else 'other'
    elif attack == 'fields':
        message['request']['fields'].append('f_extra')
    elif attack == 'date':
        message['request']['start'] = '2023-01-01'
    elif attack == 'operation':
        message['operation'] = 'write'
    elif attack == 'token':
        message['grant_token'] = '0' * 64
    elif attack == 'malformed':
        message['request'] = []
    elif attack == 'control':
        message = {'control': 'arm', 'nonce': h.last['nonce'], 'head': h.last['head'], 'mac': '0'*64}
    elif attack in ('review', 'proposal', 'grant'):
        message = {attack: 'validated', 'execution_allowed': True}
    else:
        message[attack] = '/etc/passwd' if attack == 'path' else 'unapproved'
    result = h.send(message)
    assert result['status'] == 'denied' and result['budget']['attempts'] == 0


@pytest.mark.parametrize('command', ['stop', 'revoke'])
def test_durable_control_survives_fresh_supervisor_restart(broker, command):
    h = broker; h.start(); h.control('arm')
    h.control(command)
    assert h.send(h.message())['status'] == 'denied'
    head = h.close()['head']
    assert h.start(head)['status'] == 'unarmed'
    assert h.control('arm')['status'] == 'denied'
    assert h.send(h.message('q_02'))['budget']['attempts'] == 0


def test_restart_requires_rearming_and_does_not_reset_budget_or_replay(broker):
    import time
    h = broker; h.config['limits']['max_requests'] = 1
    h.start(); h.control('arm')
    assert h.send(h.message())['status'] == 'admitted'
    head = h.close()['head']
    h.start(head)
    assert h.send(h.message('q_02'))['status'] == 'denied'
    h.control('arm')
    assert h.send(h.message())['status'] == 'denied'
    time.sleep(1.05)  # Rate interval has elapsed: this denial must exercise the budget.
    denied = h.send(h.message('q_03'))
    assert denied['status'] == 'denied' and denied['budget']['attempts'] == 1


def test_remaining_budget_allows_new_attempt_after_rate_interval(broker):
    import time
    h = broker; h.start(); h.control('arm')
    assert h.send(h.message())['status'] == 'admitted'
    time.sleep(1.05)
    result = h.send(h.message('q_02'))
    assert result['status'] == 'admitted' and result['budget']['attempts'] == 2


def test_rate_limit_survives_restart_and_duplicate_attempt_cannot_repeat(broker):
    h = broker; h.config['limits']['minimum_interval_seconds'] = 3600
    h.start(); h.control('arm'); h.send(h.message())
    assert h.send(h.message())['budget']['attempts'] == 1
    head = h.close()['head']; h.start(head); h.control('arm')
    assert h.send(h.message('q_new'))['budget']['attempts'] == 1


@pytest.mark.parametrize('attack', ['missing_secret', 'missing_key', 'bad_reference',
    'live_source', 'missing_provenance', 'malformed_grant', 'protocol', 'expired'])
def test_invalid_bootstrap_or_expiry_never_permits_read(broker, attack):
    h = broker; env = {}
    if attack == 'missing_secret':env['BROKER_SOURCE_SECRET'] = None
    elif attack == 'missing_key':env['BROKER_AUTH_KEY'] = None
    elif attack == 'bad_reference':h.config['secret_reference'] = 'not a reference'
    elif attack == 'live_source':h.config['grant']['source_id'] = 'https://example.com'
    elif attack == 'missing_provenance':h.config['grant']['provenance_source'] = ''
    elif attack == 'malformed_grant':h.config['grant'] = {}
    elif attack == 'protocol':h.config['protocol'] = 'http'
    else:h.config['grant']['window']['expires_at'] = '2000-01-01T00:00:00+00:00'
    result = h.start(env_changes=env)
    if result['status'] == 'unarmed':
        assert h.control('arm')['status'] == 'denied'
        assert h.send(h.message())['budget']['attempts'] == 0
    else:
        assert result['status'] == 'broker_blocked'


@pytest.mark.parametrize('mutation', ['tip', 'journal', 'config', 'missing_tip'])
def test_checkpoint_audit_or_authorization_mutation_blocks_restart(broker, mutation):
    import sqlite3
    h = broker; h.start(); head = h.close()['head']
    if mutation == 'tip':head = '0'*64
    elif mutation == 'missing_tip':head = None
    elif mutation == 'config':h.config['caller'] = 'other'
    else:
        with sqlite3.connect(h.state / 'broker.db') as db:
            db.execute("UPDATE events SET body='{}' WHERE sequence=1")
    assert h.start(head)['status'] == 'broker_blocked'


@pytest.mark.parametrize('mutation', ['source', 'secret', 'row', 'oversize'])
def test_worker_failure_is_counted_and_no_observation_admitted(broker, mutation):
    h = broker
    if mutation == 'source':h.source.write_text('{}')
    elif mutation == 'secret':h.secret = 'different-synthetic-secret'
    else:
        data = json.loads(h.source.read_text())
        data['rows'][0]['f_b' if mutation == 'row' else 'f_d'] = 'other' if mutation == 'row' else 'x'*65536
        h.source.write_text(_json(data))
        h.config['source_digest'] = hashlib.sha256(h.source.read_bytes()).hexdigest()
    h.start(); h.control('arm')
    result = h.send(h.message())
    assert result['status'] == 'denied' and 'observations' not in result
    assert result['budget']['attempts'] == 1 and result['budget']['failures'] == 1


def test_untrusted_application_process_has_no_broker_environment_secrets(broker):
    h = broker; h.start()
    # A separate application interpreter receives no issuer/credential environment.
    code = "import os,json;print(json.dumps([k for k in os.environ if k.startswith('BROKER_')]))"
    result = subprocess.run([sys.executable, '-I', '-c', code], capture_output=True,
                            text=True, check=True, env={'PATH': os.defpath})
    assert json.loads(result.stdout) == []
    denied = h.send({'operation': 'get_secret', 'secret_reference': 'BROKER_SOURCE_SECRET'})
    assert denied['status'] == 'denied' and h.secret not in _json(denied)


def test_direct_worker_without_supervisor_bootstrap_fails():
    result = subprocess.run([sys.executable, '-m', 'orion.pilot.broker_worker'],
        input=b'{}', capture_output=True, timeout=5, check=False,
        env={'PATH': os.defpath, 'PYTHONPATH': str(Path('src').resolve())})
    assert result.returncode == 2 and not result.stdout


def test_tampered_worker_bootstrap_rejects_before_source_access(monkeypatch):
    from orion.pilot import broker_worker
    monkeypatch.setattr(broker_worker, 'private_bytes',
                        lambda *_: pytest.fail('source accessed without supervisor seal'))
    with pytest.raises(ValueError, match='authentication denied'):
        broker_worker.authenticated_acquire({'bootstrap': {'path': 'unapproved'}, 'mac': '0'*64}, b'x'*32)


def test_real_supervisor_termination_preserves_pending_attempt(broker):
    import sqlite3
    import time
    h = broker; h.start(); h.control('arm')
    h.process.stdin.write(_json(h.message()) + '\n'); h.process.stdin.flush()
    deadline = time.monotonic() + 5
    pending = False
    while time.monotonic() < deadline:
        with sqlite3.connect(h.state / 'broker.db') as db:
            row = db.execute('SELECT body,mac FROM events ORDER BY sequence DESC LIMIT 1').fetchone()
        if json.loads(row[0])['event'] == 'attempt':
            h.process.kill()
            h.process.wait(timeout=5)
            pending = True
            break
        time.sleep(0.001)
    assert pending, 'failed to exercise an actual interrupted attempt'
    assert h.start(row[1])['status'] == 'broker_blocked'


def test_broker_audit_contains_digests_not_secret_or_record_contents(broker):
    import sqlite3
    h = broker; h.start(); h.control('arm'); h.send(h.message())
    with sqlite3.connect(h.state / 'broker.db') as db:
        rows = db.execute('SELECT body FROM events').fetchall()
    text = ''.join(row[0] for row in rows)
    assert all(secret not in text for secret in (h.secret, h.key.decode(), 'f_d', 'x_1', str(h.source)))
    events = [json.loads(row[0]) for row in rows]
    admitted = next(e for e in events if e['event'] == 'broker_admitted')
    assert set(admitted['references']) == {'caller', 'scope', 'request', 'observations', 'version'}
    assert admitted['at'] and admitted['binding'] == digest(h.config)
    assert admitted['execution_allowed'] is False and admitted['execution_status'] == 'not_attempted'


@pytest.mark.parametrize('which', ['credential', 'issuer_key'])
def test_secret_in_source_payload_is_not_returned(broker, which):
    h = broker
    value = json.loads(h.source.read_text())
    value['rows'][0]['f_d'] = h.secret if which == 'credential' else h.key.decode()
    h.source.write_text(_json(value))
    h.config['source_digest'] = hashlib.sha256(h.source.read_bytes()).hexdigest()
    h.start(); h.control('arm')
    result = h.send(h.message())
    assert result['status'] == 'denied' and h.secret not in _json(result)


@pytest.mark.parametrize('raw', ['{"operation":"read","operation":"write"}\n',
                               '{malformed}\n', 'null\n'])
def test_malformed_application_frames_do_not_reach_source(broker, raw):
    h = broker; h.start(); h.control('arm')
    h.process.stdin.write(raw); h.process.stdin.flush()
    h.last = json.loads(h.process.stdout.readline())
    assert h.last['status'] == 'denied' and h.last['budget']['attempts'] == 0


def test_same_uid_custody_is_not_a_claim_of_os_isolation(broker):
    from orion.pilot.readiness import release_report
    h = broker; h.start()
    code = """import sys
try:
    data=open('/proc/'+sys.argv[1]+'/environ','rb').read()
except OSError:
    print('unavailable')
else:
    print('exposed' if b'BROKER_SOURCE_SECRET=' in data else 'absent')
"""
    result = subprocess.run([sys.executable, '-I', '-c', code, str(h.process.pid)],
        capture_output=True, text=True, check=True, env={'PATH': os.defpath})
    assert result.stdout.strip() in ('exposed', 'unavailable', 'absent')
    # Either the escape is observed or this probe is unavailable. Neither is
    # sufficient proof that a same-UID application is isolated by this module.
    assert not release_report()['live_ready']
    assert next(g for g in release_report()['gates'] if g['category'] == 'SECRETS')['status'] == 'FAIL'


@pytest.mark.parametrize('protocol', ['local_rows_v1', 'local_columns_v1'])
def test_broker_observations_feed_existing_semantic_revision_review_and_recovery(tmp_path, protocol):
    from semantic_lab import Organization

    from orion.shadow.semantic_review import review_semantic_study
    from orion.understanding.role_checkpoint import checkpoint_sha256
    from orion.understanding.semantic_checkpoint import checkpoint_semantic, restore_semantic

    class BrokerOrganization(Organization):
        def admit(self, source, resource, fields, when, rows, *, provenance='local-records',
                  identity=None, partition=None, lookup=None):
            # Independent synthetic control-plane bindings, never semantic answers.
            assert lookup is None
            scope = PilotRequest(self.tenant, self.company, source, resource, fields, when,
                                 date(2024, 6, 1), date(2024, 6, 7), len(rows))
            grant = PilotAuthorization('g_' + provenance, source, ReviewedReadWindow(
                self.tenant, self.company, resource, fields, when, scope.start, scope.end,
                utc_now() + timedelta(hours=1)), identity or self.identity,
                partition or self.partition, provenance, EvidenceKind.EXPERIMENT, len(rows))
            self.counter += 1
            h = Harness(tmp_path / str(self.counter), protocol, rows=rows, grant=grant, request=scope)
            try:
                h.start(); h.control('arm')
                response = h.send(h.message())
                assert response['status'] == 'admitted'
                observations = observations_from(response['observations'])
            finally:
                h.close()
            for observation in observations:
                key = observation.evidence.evidence_id
                self.archive[key], self.archive[('scope', key)] = observation, scope
            return observations, scope

    lab = BrokerOrganization('B')
    lab.records()
    initial = lab.anchors('monetary')
    assert any(c.hypothesis.status == 'validated' for c in lab.study.claims())
    lab.correct('monetary', initial, [8, 11])
    review = review_semantic_study(lab.study, tenant_id=lab.tenant, company=lab.company, source_id=lab.source)
    assert review.decision and review.contradicted_hypotheses and not review.execution_allowed
    assert any(c.hypothesis.status == 'unknown' for c in review.evaluated_claims)
    payload = checkpoint_semantic(lab.study)
    restored = restore_semantic(payload, expected_sha256=checkpoint_sha256(payload),
        tenant_id=lab.tenant, company=lab.company, source_id=lab.source,
        instruments=lab.instruments, rules=lab.study.rules, evidence_lookup=lab.archive.get)
    assert review_semantic_study(restored, tenant_id=lab.tenant, company=lab.company,
                                 source_id=lab.source) == review
