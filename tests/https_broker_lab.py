"""Fixed HTTPS loopback laboratory; never imported by ORION production modules.

The fixture-only supervisor composes the existing broker and sealed source worker.
It does not prove deployment isolation or supply a production connector.
"""
import hashlib
import hmac
import http.client
import json
import os
import socket
import ssl
import subprocess
import sys
import tempfile
import threading
from contextlib import closing, contextmanager
from dataclasses import dataclass
from pathlib import Path

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from orion.pilot.broker import Broker
from orion.pilot.broker_contract import (
    MAX_FRAME,
    authenticate,
    decode,
    digest,
    exact,
    private_bytes,
)
from orion.understanding.role_checkpoint import _json

HOST = '127.0.0.1'
TARGET = '/fixture'


def certificates(root, *, hostname=HOST):
    """Ephemeral local test certificate; no committed key or external CA."""
    cert, key = root / 'cert.pem', root / 'key.pem'
    subprocess.run(['openssl', 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
        '-keyout', str(key), '-out', str(cert), '-days', '1', '-subj', '/CN=local-fixture',
        '-addext', 'subjectAltName=' + ('IP:' if hostname == HOST else 'DNS:') + hostname],
        check=True, capture_output=True, timeout=10)
    cert.chmod(0o600); key.chmod(0o600)
    return cert, key


@dataclass(frozen=True)
class LocalPolicy:
    port: int
    certificate: str
    certificate_sha256: str

    def context(self):
        if type(self.port) is not int or not 1 <= self.port <= 65535:
            raise ValueError('invalid fixture port')
        pem = private_bytes(self.certificate)
        if hashlib.sha256(pem).hexdigest() != self.certificate_sha256:
            raise ValueError('fixture trust changed')
        context = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        context.minimum_version = ssl.TLSVersion.TLSv1_2
        context.check_hostname = True
        context.verify_mode = ssl.CERT_REQUIRED
        context.load_verify_locations(cadata=pem.decode('ascii'))
        return context

    def authorize_wire(self, *, host, port, method, target, body):
        if (host != HOST or type(port) is not int or port != self.port
                or method != 'GET' or target != TARGET or body is not None):
            raise ValueError('fixed read-only loopback route required')


def bounded_body(response, limit):
    """No redirects, transfer coding, compression or ambiguous response framing."""
    if type(limit) is not int or not 1 <= limit <= MAX_FRAME // 2:
        raise ValueError('bounded local response required')
    headers = response.getheaders()
    lengths = [v for k, v in headers if k.lower() == 'content-length']
    if (response.status != 200 or len(lengths) != 1 or not lengths[0].isascii()
            or not lengths[0].isdecimal() or len(lengths[0]) > 6
            or any(k.lower() in ('transfer-encoding', 'content-encoding', 'location') for k, _ in headers)):
        raise ValueError('response policy denied')
    size = int(lengths[0])
    if not 1 <= size <= limit:
        raise ValueError('response size denied')
    data = response.read(size + 1)
    if type(data) is not bytes or len(data) != size:
        raise ValueError('partial or oversized response')
    return data


def https_read(policy, secret, limit):
    # No URL parsing, DNS name, proxy environment, redirects, caller headers or body.
    policy.authorize_wire(host=HOST, port=policy.port, method='GET', target=TARGET, body=None)
    context = policy.context()
    if type(secret) is not str or not secret.isascii() or not secret.isalnum():
        raise ValueError('bounded fixture credential required')
    with closing(http.client.HTTPSConnection(HOST, policy.port, timeout=1, context=context)) as connection:
        connection.request('GET', TARGET, headers={'Authorization': 'Bearer ' + secret,
                                                  'Accept': 'application/json', 'Connection': 'close'})
        response = connection.getresponse()
        return bounded_body(response, limit)


class HTTPSBroker(Broker):
    """Test-only fixed supervisor. Existing application API stays unchanged."""
    def __init__(self, config, directory, **kwargs):
        super().__init__(config, directory, **kwargs)
        if self.operation != 'read':
            raise ValueError('record fixture required')
        self.profile_path = Path(self.config['source_path']).parent / 'tls-profile.json'
        self.profile_bytes = private_bytes(self.profile_path)
        envelope = exact(decode(self.profile_bytes), ('profile', 'mac'))
        profile = exact(envelope['profile'], ('binding', 'port', 'certificate', 'certificate_sha256'))
        if (profile['binding'] != self.binding or type(envelope['mac']) is not str
                or not hmac.compare_digest(envelope['mac'], authenticate(self.key, 'local_https_fixture', profile))):
            raise ValueError('fixture supervisor route denied')
        self.policy = LocalPolicy(**{k: profile[k] for k in ('port', 'certificate', 'certificate_sha256')})
        self.policy.context()

    def _worker(self, bootstrap, *, timeout=5):
        # Broker's canonical permit and durable reservation precede this method.
        if (not self.armed or self.stopped() or not self.journal.inspect()['pending']
                or private_bytes(self.profile_path) != self.profile_bytes):
            raise ValueError('fixture acquisition denied')
        raw = https_read(self.policy, self.secret, min(MAX_FRAME // 2, self.limits.response_bytes // 2))
        if hashlib.sha256(raw).hexdigest() != self.config['source_digest']:
            raise ValueError('fixture content changed')
        # Normalize the exact received bytes through the unchanged sealed worker,
        # never substitute the owner's pre-existing source file for the response.
        with tempfile.TemporaryDirectory(prefix='orion-tls-received-') as temporary:
            path = Path(temporary) / 'received.json'
            path.write_bytes(raw); path.chmod(0o600)
            result = super()._worker(dict(bootstrap, path=str(path)), timeout=timeout)
        if len(raw) + len(result.stdout) > self.limits.response_bytes:
            raise ValueError('combined response reservation exceeded')
        return result


class FixtureServer:
    def __init__(self, cert, key, body, secret, behavior='ok'):
        self.body, self.secret, self.behavior = body, secret, behavior
        self.requests = []
        self.listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        self.listener.bind((HOST, 0)); self.listener.listen(4); self.listener.settimeout(0.1)
        self.port = self.listener.getsockname()[1]
        self.context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        self.context.minimum_version = ssl.TLSVersion.TLSv1_2
        self.context.load_cert_chain(str(cert), str(key))
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self.serve, daemon=True)
        self.thread.start()

    def serve(self):
        while not self.stop.is_set():
            try:
                connection, _ = self.listener.accept()
            except TimeoutError:
                continue
            except OSError:
                return
            try:
                connection.settimeout(1)
                with self.context.wrap_socket(connection, server_side=True) as peer:
                    request = b''
                    while b'\r\n\r\n' not in request and len(request) <= 4096:
                        part = peer.recv(1024)
                        if not part:
                            break
                        request += part
                    authorized = b'Authorization: Bearer ' + self.secret.encode() + b'\r\n' in request
                    self.requests.append({'get': request.startswith(b'GET /fixture HTTP/1.1\r\n'),
                                          'authorized': authorized})  # Never retain the credential.
                    if not authorized or not self.requests[-1]['get']:
                        peer.sendall(b'HTTP/1.1 403 Forbidden\r\nContent-Length: 0\r\n\r\n')
                    elif self.behavior == 'redirect':
                        peer.sendall(b'HTTP/1.1 302 Found\r\nLocation: https://attacker.invalid/\r\nContent-Length: 0\r\n\r\n')
                    elif self.behavior == 'oversized':
                        peer.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 999999\r\n\r\n')
                    elif self.behavior == 'partial':
                        peer.sendall(b'HTTP/1.1 200 OK\r\nContent-Length: 100\r\n\r\n{}')
                    elif self.behavior == 'timeout':
                        self.stop.wait(1.2)
                    else:
                        peer.sendall(b'HTTP/1.1 200 OK\r\nContent-Type: application/json\r\nContent-Length: '
                                     + str(len(self.body)).encode() + b'\r\n\r\n' + self.body)
            except (OSError, ssl.SSLError):
                connection.close()

    def close(self):
        self.stop.set(); self.listener.close(); self.thread.join(timeout=2)
        if self.thread.is_alive():
            raise ValueError('fixture server shutdown failed')


def supervisor():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--config', required=True)
    parser.add_argument('--state', required=True)
    parser.add_argument('--expected-head')
    args = parser.parse_args()
    try:
        broker = HTTPSBroker(decode(private_bytes(args.config)), args.state, expected_head=args.expected_head)
        print(_json(broker.status('unarmed')), flush=True)
        for _ in range(20):
            line = sys.stdin.buffer.readline(MAX_FRAME + 1)
            if not line:
                broker._event('broker_shutdown')
                print(_json(broker.status('shutdown')), flush=True)
                return 0
            if not line.endswith(b'\n') or len(line) > MAX_FRAME:
                raise ValueError('fixture frame denied')
            print(_json(broker.handle(decode(line))), flush=True)
        raise ValueError('fixture frames exhausted')
    except Exception:  # noqa: BLE001 - no credentials or source details on the pipe
        print('{"status":"broker_blocked","execution_allowed":false}', flush=True)
        return 2


from test_supervised_broker import Harness


class TLSHarness(Harness):
    def start(self, head=None, env_changes=None):
        path = self.root / 'config.json'
        path.write_text(_json(self.config)); path.chmod(0o600)
        env = {'PATH': os.defpath, 'BROKER_AUTH_KEY': self.key.decode(), 'BROKER_SOURCE_SECRET': self.secret,
               'HTTPS_PROXY': 'http://127.0.0.1:1', 'ALL_PROXY': 'http://127.0.0.1:1'}
        args = [sys.executable, '-I', str(Path(__file__).resolve()), '--config', str(path), '--state', str(self.state)]
        if head:
            args.extend(('--expected-head', head))
        self.process = subprocess.Popen(args, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, env=env, close_fds=True)
        self.last = json.loads(self.process.stdout.readline())
        return self.last


@contextmanager
def prepared_https(h, behavior="ok"):
    import secrets
    root = h.root
    # Fixed synthetic alphanumeric credential, not a real secret.
    h.secret = secrets.token_hex(24)
    envelope = decode(h.source.read_bytes())
    envelope['credential_digest'] = hashlib.sha256(h.secret.encode()).hexdigest()
    raw = _json(envelope).encode()
    h.config['source_digest'] = hashlib.sha256(raw).hexdigest()
    cert, key = certificates(root, hostname='wrong.invalid' if behavior == 'wrong_hostname' else HOST)
    trust = cert
    if behavior == 'untrusted_certificate':
        (root / 'other-trust').mkdir()
        trust, _ = certificates(root / 'other-trust')
    server = FixtureServer(cert, key, raw, h.secret, behavior)
    try:
        profile = {'binding': digest(h.config), 'port': server.port, 'certificate': str(trust),
                   'certificate_sha256': hashlib.sha256(trust.read_bytes()).hexdigest()}
        path = root / 'tls-profile.json'
        path.write_text(_json({'profile': profile, 'mac': authenticate(h.key, 'local_https_fixture', profile)}))
        path.chmod(0o600)
        h.source.unlink()  # Success must use HTTPS response bytes, not cached source data.
        yield server
    finally:
        server.close()


def run_case(root, behavior="ok", protocol="local_rows_v1"):
    from orion.pilot.broker_contract import observations_from
    h = TLSHarness(root, protocol)
    with prepared_https(h, behavior) as server:
        try:
            assert h.start()['status'] == 'unarmed'
            assert h.send(h.message())['budget']['attempts'] == 0
            assert h.control('arm')['status'] == 'arm'
            for message in (dict(h.message(), operation='write'), dict(h.message(), url='https://attacker.invalid/'),
                            dict(h.message(), grant_token='0' * 64)):
                assert h.send(message)['budget']['attempts'] == 0
            assert server.requests == []
            result = h.send(h.message())
            assert result['budget']['attempts'] == 1
            if behavior == 'ok':
                assert result['status'] == 'admitted'
                observations = observations_from(result['observations'])
                assert observations[0].evidence.payload['record'] == h.rows[0]
                assert observations[0].evidence.payload['provenance']['authorization_id'] == h.grant.authorization_id
                assert h.secret not in _json(result) and h.key.decode() not in _json(result)
            else:
                assert result['status'] == 'denied' and 'observations' not in result
                assert result['budget']['failures'] == 1
            expected_requests = [] if behavior in ('wrong_hostname', 'untrusted_certificate') else [{'get': True, 'authorized': True}]
            assert server.requests == expected_requests
            assert h.send(h.message())['status'] == 'denied'
            h.control('revoke'); head = h.close()['head']
            assert h.start(head)['status'] == 'unarmed'
            assert h.control('arm')['status'] == 'denied'
            assert h.send(h.message('restart'))['budget']['attempts'] == 1
            assert server.requests == expected_requests
            return {'case': behavior, 'protocol': protocol, 'status': 'PASS', 'source_requests': len(server.requests),
                    'attempts_after_restart': 1, 'execution_allowed': False}
        finally:
            h.close()

def run():
    report = {'status': 'BLOCKED', 'reason': 'loopback_socket_unavailable', 'cases': [],
              'live_ready': False, 'execution_allowed': False, 'allow_live_customer_access': False,
              'production_containment': 'NOT PROVEN'}
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            probe.bind((HOST, 0))
    except OSError:
        return report
    try:
        with tempfile.TemporaryDirectory(prefix='orion-https-lab-') as temporary:
            root = Path(temporary)
            report['cases'] = [run_case(root / f'case-{i}', behavior, protocol)
                for i, (behavior, protocol) in enumerate([
                    ('ok', 'local_rows_v1'), ('ok', 'local_columns_v1'),
                    ('redirect', 'local_rows_v1'), ('oversized', 'local_rows_v1'),
                    ('partial', 'local_rows_v1'), ('timeout', 'local_rows_v1'),
                    ('wrong_hostname', 'local_rows_v1'), ('untrusted_certificate', 'local_rows_v1')])]
        report.update(status='PASS', reason='local_https_fixture_only')
    except (FileNotFoundError, ImportError):
        report.update(status='BLOCKED', reason='local_development_dependency_missing')
    except Exception:  # noqa: BLE001 - fixed failure, never return source or credential details
        report.update(status='FAIL', reason='local_https_lifecycle_failed')
    return report


if __name__ == '__main__':
    if '--config' in sys.argv:
        raise SystemExit(supervisor())
    print(_json(run()))
