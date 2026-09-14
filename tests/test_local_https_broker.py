"""TLS-in-memory is real certificate verification, never loopback/isolation proof."""
import hashlib
import ssl
from dataclasses import replace

import pytest
from https_broker_lab import (
    HOST,
    HTTPSBroker,
    LocalPolicy,
    bounded_body,
    certificates,
    https_read,
    run,
)
from test_supervised_broker import Harness

from orion.contracts import utc_now
from orion.pilot.broker_contract import authenticate, digest, observations_from
from orion.understanding.role_checkpoint import _json


@pytest.fixture(scope='module')
def tls_files(tmp_path_factory):
    root = tmp_path_factory.mktemp('tls-memory')
    return certificates(root)


def policy(cert):
    return LocalPolicy(443, str(cert), hashlib.sha256(cert.read_bytes()).hexdigest())


def tls_pair(cert, key, client_context, hostname=HOST):
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(str(cert), str(key))
    ci, co, si, so = [ssl.MemoryBIO() for _ in range(4)]
    client = client_context.wrap_bio(ci, co, server_hostname=hostname)
    server = server_context.wrap_bio(si, so, server_side=True)
    completed = set()
    for _ in range(100):
        for index, peer in enumerate((client, server)):
            if index not in completed:
                try:
                    peer.do_handshake(); completed.add(index)
                except ssl.SSLWantReadError:
                    pass
        si.write(co.read()); ci.write(so.read())
        if len(completed) == 2:
            return client, server, co, si
    raise AssertionError('TLS handshake failed to terminate')


def test_tls_certificate_hostname_and_encrypted_credential_bytes(tls_files):
    cert, key = tls_files
    context = policy(cert).context()
    assert context.check_hostname and context.verify_mode == ssl.CERT_REQUIRED
    assert context.minimum_version >= ssl.TLSVersion.TLSv1_2
    client, server, output, incoming = tls_pair(cert, key, context)
    secret = b'synthetic-credential-over-encrypted-memory-channel'
    client.write(secret)
    wire = output.read()
    assert secret not in wire
    incoming.write(wire)
    assert server.read(1024) == secret


def test_tls_wrong_hostname_rejects_before_application_bytes(tls_files):
    cert, key = tls_files
    with pytest.raises(ssl.SSLCertVerificationError):
        tls_pair(cert, key, policy(cert).context(), 'wrong.invalid')


def test_tls_untrusted_certificate_rejects_before_application_bytes(tls_files):
    cert, key = tls_files
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
    with pytest.raises(ssl.SSLCertVerificationError):
        tls_pair(cert, key, context)


def test_changed_trust_anchor_rejects(tls_files):
    with pytest.raises(ValueError, match='trust changed'):
        replace(policy(tls_files[0]), certificate_sha256='0' * 64).context()


@pytest.mark.parametrize('changes', [{'host': 'attacker.invalid'}, {'host': '::1'}, {'port': 444},
    {'port': True}, {'method': 'POST'}, {'target': '/fixture?url=https://attacker.invalid'},
    {'target': '//attacker.invalid/'}, {'target': 'https://127.0.0.1/fixture'}, {'body': b'x'}])
def test_fixed_route_rejects_arbitrary_destination_method_and_query(tls_files, changes):
    values = {'host': HOST, 'port': 443, 'method': 'GET', 'target': '/fixture', 'body': None}
    with pytest.raises(ValueError):
        policy(tls_files[0]).authorize_wire(**(values | changes))


class Response:
    def __init__(self, status=200, headers=None, data=b'{}'):
        self.status = status
        self.headers = headers if headers is not None else [('Content-Length', '2')]
        self.data, self.reads = data, []
    def getheaders(self):
        return self.headers
    def read(self, limit):
        self.reads.append(limit)
        return self.data


@pytest.mark.parametrize('response', [Response(302, [('Location', 'https://attacker.invalid/')]),
    Response(307), Response(401), Response(headers=[]),
    Response(headers=[('Content-Length', '2'), ('content-length', '2')]),
    Response(headers=[('Content-Length', '2'), ('Transfer-Encoding', 'chunked')]),
    Response(headers=[('Content-Length', '2'), ('Content-Encoding', 'gzip')]),
    Response(headers=[('Content-Length', '999999')]), Response(headers=[('Content-Length', '-1')]),
    Response(headers=[('Content-Length', '²')])])
def test_response_denial_precedes_body_read(response):
    with pytest.raises(ValueError):
        bounded_body(response, 64)
    assert response.reads == []


@pytest.mark.parametrize('data', [b'', b'{', b'{}x', 'not-bytes'])
def test_partial_or_oversized_body_rejects(data):
    response = Response(data=data)
    with pytest.raises(ValueError):
        bounded_body(response, 64)
    assert response.reads == [3]


def test_client_uses_fixed_route_no_proxy_and_closes(tls_files, monkeypatch):
    import https_broker_lab
    calls = []
    class Connection:
        def __init__(self, host, port, *, timeout, context):
            calls.append((host, port, timeout, context.check_hostname))
        def request(self, method, target, *, headers):
            assert headers['Authorization'] == 'Bearer abc123'
            calls.append((method, target))
        def getresponse(self):
            return Response()
        def close(self):
            calls.append('closed')
    monkeypatch.setenv('HTTPS_PROXY', 'http://attacker.invalid')
    monkeypatch.setenv('ALL_PROXY', 'http://attacker.invalid')
    monkeypatch.setattr(https_broker_lab.http.client, 'HTTPSConnection', Connection)
    assert https_read(policy(tls_files[0]), 'abc123', 64) == b'{}'
    assert calls == [(HOST, 443, 1, True), ('GET', '/fixture'), 'closed']


def configured_broker(tmp_path, monkeypatch, tls_files):
    h = Harness(tmp_path / 'broker')
    profile = {'binding': digest(h.config), 'port': 443, 'certificate': str(tls_files[0]),
               'certificate_sha256': hashlib.sha256(tls_files[0].read_bytes()).hexdigest()}
    path = h.root / 'tls-profile.json'
    path.write_text(_json({'profile': profile, 'mac': authenticate(h.key, 'local_https_fixture', profile)}))
    path.chmod(0o600)
    monkeypatch.setenv('BROKER_AUTH_KEY', h.key.decode())
    monkeypatch.setenv('BROKER_SOURCE_SECRET', h.secret)
    return h, HTTPSBroker(h.config, h.state)


def test_existing_broker_admits_exact_received_bytes_after_reservation(tmp_path, monkeypatch, tls_files):
    import https_broker_lab
    h, broker = configured_broker(tmp_path, monkeypatch, tls_files)
    raw = h.source.read_bytes(); h.source.unlink()
    calls = []
    def transport(policy, secret, limit):
        assert broker.journal.inspect()['pending']
        assert secret == h.secret and limit <= broker.limits.response_bytes // 2
        calls.append(policy.port)
        return raw
    monkeypatch.setattr(https_broker_lab, 'https_read', transport)
    assert broker.handle(h.message())['budget']['attempts'] == 0
    broker.armed = True  # Trusted test issuer; not a new application control path.
    wrong = h.message(); wrong['request']['tenant_id'] = 'other'
    assert broker.handle(wrong)['budget']['attempts'] == 0 and calls == []
    result = broker.handle(h.message())
    assert result['status'] == 'admitted' and result['budget']['attempts'] == 1
    assert observations_from(result['observations'])[0].evidence.payload['record'] == h.rows[0]
    assert broker.handle(h.message())['status'] == 'denied' and calls == [443]
    assert h.secret not in _json(result) and result['execution_allowed'] is False


@pytest.mark.parametrize('failure', ['tls', 'timeout', 'changed_body', 'revoked', 'expired', 'profile'])
def test_failed_tls_acquisition_never_creates_observation(tmp_path, monkeypatch, tls_files, failure):
    from datetime import timedelta

    import https_broker_lab
    h, broker = configured_broker(tmp_path, monkeypatch, tls_files)
    raw = h.source.read_bytes(); h.source.unlink()
    calls = []
    def transport(*args):
        calls.append(True)
        if failure == 'tls':
            raise ssl.SSLCertVerificationError('synthetic TLS failure')
        if failure == 'timeout':
            raise TimeoutError('synthetic timeout')
        if failure == 'revoked':
            broker.armed = False
            broker.grant = None  # Trusted lookup now returns no grant.
        if failure == 'expired':
            broker.grant = replace(broker.grant, window=replace(broker.grant.window,
                                  expires_at=utc_now() - timedelta(seconds=1)))
        return b'{}' if failure == 'changed_body' else raw
    monkeypatch.setattr(https_broker_lab, 'https_read', transport)
    broker.armed = True
    if failure == 'profile':
        broker.profile_path.write_text('{}')
    result = broker.handle(h.message())
    assert result['status'] == 'denied' and 'observations' not in result
    assert result['budget']['attempts'] == 1
    assert len(calls) == (0 if failure == 'profile' else 1)


def test_loopback_lifecycle_requires_actual_sockets_and_never_claims_live_readiness():
    report = run()
    assert report['status'] in ('PASS', 'BLOCKED')
    assert report['live_ready'] is False and report['execution_allowed'] is False
    assert report['production_containment'] == 'NOT PROVEN'
    if report['status'] == 'PASS':
        assert len(report['cases']) == 8
        for case in report['cases']:
            assert case['status'] == 'PASS'
            assert case['source_requests'] == (0 if case['case'] in ('wrong_hostname', 'untrusted_certificate') else 1)
    else:
        assert report['reason'] in ('loopback_socket_unavailable', 'local_development_dependency_missing')


@pytest.mark.parametrize('output', ['[]', 'null', '{"status":"PASS","live_ready":true}', 'not-json'])
def test_operator_probe_rejects_malformed_or_overclaiming_fixture_output(monkeypatch, capsys, output):
    import importlib.util
    import subprocess
    from pathlib import Path
    path = Path(__file__).resolve().parents[1] / 'tools' / 'local_https_broker_probe.py'
    spec = importlib.util.spec_from_file_location('local_https_probe_test', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module.subprocess, 'run', lambda *args, **kwargs:
                        subprocess.CompletedProcess([], 0, output, ''))
    assert module.run() == 2
    import json
    report = json.loads(capsys.readouterr().out)
    assert report['status'] == 'BLOCKED' and report['execution_allowed'] is False
