"""Local owner harness: actual broker and semantic consumer, synthetic sources only."""
import json
import os
import select
import socket
import subprocess
import sys
import tempfile
import time
from contextlib import ExitStack
from dataclasses import asdict
from datetime import date, timedelta
from pathlib import Path

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))

from isolation_lab import connectable, namespace_command, readable, result, run_lab


class Frames:
    """Bounded pipe framing with a per-frame deadline, not an RPC endpoint."""
    def __init__(self, stream):
        self.stream, self.pending = stream, b''

    def read(self):
        deadline = time.monotonic() + 10
        while b'\n' not in self.pending:
            if len(self.pending) > 65536:
                raise ValueError('consumer frame oversized')
            remaining = deadline - time.monotonic()
            if remaining <= 0 or not select.select([self.stream], [], [], remaining)[0]:
                raise ValueError('consumer deadline')
            data = os.read(self.stream.fileno(), 4096)
            if not data:
                raise ValueError('consumer terminated')
            self.pending += data
        line, self.pending = self.pending.split(b'\n', 1)
        from orion.pilot.broker_contract import decode
        return decode(line)


def relay(h, scope, *, confined, metadata_broker):
    from orion.pilot.broker_contract import observations_from
    from orion.understanding.role_checkpoint import _json
    root = Path(__file__).resolve().parents[1]
    script = root / 'tests' / 'isolated_semantic_consumer.py'
    command = namespace_command(script, readonly=((root / 'src', '/app/src'),
        (root / 'tests' / 'isolation_lab.py', '/work/isolation_lab.py'))) if confined else (
        [sys.executable, '-I', '-S', str(script), '--child'])
    with subprocess.Popen(command, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, env={'PATH': os.defpath}, close_fds=True) as child:
        observations, trace = (), []
        try:
            bootstrap = _json(scope).encode() + b'\n'
            if len(bootstrap) > 65536:
                raise ValueError('bootstrap oversized')
            child.stdin.write(bootstrap); child.stdin.flush()
            frames = Frames(child.stdout)
            for _ in range(12):
                frame = frames.read()
                if type(frame) is dict and set(frame) == {'consumer_result'}:
                    child.stdin.close()
                    child.wait(timeout=5)
                    if child.returncode or child.stderr.read(4097):
                        raise ValueError('consumer failed')
                    text = _json(frame)
                    if any(b.secret in text or b.key.decode() in text for b in (h, metadata_broker)):
                        raise ValueError('consumer secret leakage')
                    return frame['consumer_result'], observations, trace
                broker = metadata_broker if type(frame) is dict and frame.get('operation') == 'metadata' else h
                response = broker.send(frame)  # Ordinary API; never signs controls or grants.
                trace.append((response['status'], response['budget']['attempts']))
                if response['status'] == 'admitted':
                    if broker is metadata_broker:
                        scope['schema'] = response['observations']
                    else:
                        observations = observations_from(response['observations'])
                child.stdin.write(_json(response).encode() + b'\n'); child.stdin.flush()
            raise ValueError('consumer message budget exhausted')
        finally:
            if child.poll() is None:
                child.kill(); child.wait(timeout=5)


def run_case(root, protocol, *, confined=True, transport="local"):
    if transport not in ("local", "https"):
        raise ValueError("fixed laboratory transport required")
    # Existing dev fixtures are used only by the trusted owner, never mounted in the consumer.
    from isolated_semantic_consumer import semantic_state
    from semantic_lab import Organization
    from test_broker_metadata import MetadataHarness
    from test_supervised_broker import Harness

    from orion.contracts import EvidenceKind, utc_now
    from orion.discovery.pilot_metadata import MetadataRequest
    from orion.discovery.pilot_read import PilotAuthorization, PilotRequest
    from orion.discovery.read_window import ReviewedReadWindow
    from orion.history.evidence import _observation_to_data

    lab = Organization('C')  # Existing admitted opaque metadata, no record acquisition here.
    fields = (*lab.fields, lab.identity, lab.partition)
    rows = [dict(zip(fields, (*values, f's_{i}', lab.company), strict=True))
            for i, values in enumerate(lab.values)]
    request = PilotRequest(lab.tenant, lab.company, lab.source, lab.resources[0], fields,
                           lab.fields[2], date(2024, 6, 1), date(2024, 6, 7), 2)
    grant = PilotAuthorization('isolated-read', lab.source, ReviewedReadWindow(lab.tenant,
        lab.company, request.resource, fields, request.date_field, request.start, request.end,
        utc_now() + timedelta(hours=1)), lab.identity, lab.partition, 'local-records', EvidenceKind.EXPERIMENT, 2)
    if transport == 'https':
        from https_broker_lab import TLSHarness
        h = TLSHarness(root, protocol, rows=rows, request=request, grant=grant)
    else:
        h = Harness(root, protocol, rows=rows, request=request, grant=grant)
    schemas = {resource: [{'name': name, 'kind': kind, 'classification': 'public'}
                         for r, name, kind, _evidence in lab.base.facts if r == resource]
               for resource in lab.resources}
    mh = MetadataHarness(root / 'metadata', schemas=schemas,
                         request=MetadataRequest(lab.tenant, lab.company, lab.source))
    try:
        with ExitStack() as stack:
            server, https_scope, https_negative = None, None, False
            source_canary = h.source
            if transport == 'https':
                from https_broker_lab import prepared_https
                server = stack.enter_context(prepared_https(h))
                source_canary = root / 'protected-source-canary.json'
                source_canary.write_bytes(server.body); source_canary.chmod(0o600)
                https_scope = {'port': server.port, 'profile': str(root / 'tls-profile.json'),
                               'key': str(root / 'key.pem')}
                https_negative = (connectable(socket.AF_INET, ('127.0.0.1', server.port))
                                  and readable(https_scope['profile']) and readable(https_scope['key'])
                                  and readable(source_canary))
                assert https_negative and not h.source.exists()
            port, unix_path, negative = 0, str(root / 'listener'), False
            if confined:
                tcp = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
                unix = stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
                tcp.bind(('127.0.0.1', 0)); tcp.listen(4)
                unix.bind(unix_path); unix.listen(4)
                port = tcp.getsockname()[1]
                negative = connectable(socket.AF_INET, ('127.0.0.1', port)) and connectable(socket.AF_UNIX, unix_path)
            assert mh.start()['status'] == 'unarmed'
            assert mh.control('arm')['status'] == 'arm'
            assert h.start()['status'] == 'unarmed'
            assert h.control('arm')['status'] == 'arm'
            secret, audit = root / 'secret-canary', root / 'audit-canary'
            secret.write_text(h.secret); secret.chmod(0o600)
            audit.write_bytes(b'original'); audit.chmod(0o600)
            scope = {'metadata_message': mh.message(), 'message': h.message(),
                'metadata_source_denied': str(mh.source),
                'metadata_configuration_denied': str(mh.root / 'config.json'),
                'metadata_journal_write_denied': str(mh.state / 'broker.db'),
                'instruments': [asdict(i) for i in lab.instruments], 'source_denied': str(source_canary),
                'configuration_denied': str(root / 'config.json'), 'journal_write_denied': str(h.state / 'broker.db'),
                'isolation': {'secret': str(secret), 'audit': str(audit), 'unix': unix_path,
                    'port': port, 'parent': h.process.pid,
                    **{kind: os.readlink('/proc/self/ns/' + kind) for kind in ('net', 'pid', 'mnt', 'user')}}}
            if https_scope is not None:
                scope['https'] = https_scope
            negative = negative and readable(secret) and readable(source_canary) and readable(f'/proc/{h.process.pid}/environ')
            first, observations, trace = relay(h, scope, confined=confined, metadata_broker=mh)
            assert trace == [('admitted', 3)] + [('denied', 0)] * 4 + [('admitted', 1), ('denied', 1)]
            assert all(first['pipeline_checks'].values()) and len(observations) == 2
            if server is not None:
                assert server.requests == [{'get': True, 'authorized': True}]
                assert not h.source.exists()
            # Consumer output is not trusted as an archive or semantic attestation.
            expected = semantic_state(scope, observations)
            assert first['state'] == expected
            assert mh.control('revoke')['status'] == 'revoke'
            metadata_head = mh.close()['head']
            assert mh.start(metadata_head)['status'] == 'unarmed'
            assert mh.control('arm')['status'] == 'denied'
            assert h.control('revoke')['status'] == 'revoke'
            head = h.close()['head']
            assert h.start(head)['status'] == 'unarmed'
            assert h.control('arm')['status'] == 'denied'
            scope.update(checkpoint=expected['checkpoint'], checkpoint_sha256=expected['checkpoint_sha256'],
                         observations=[_observation_to_data(o) for o in observations])
            scope['isolation']['parent'] = h.process.pid
            second, acquired, trace = relay(h, scope, confined=confined, metadata_broker=mh)
            assert trace == [('denied', 3), ('denied', 1)] and not acquired
            assert all(second['pipeline_checks'].values()) and second['state'] == expected
            checks = {k: first['isolation_checks'][k] and second['isolation_checks'][k]
                      for k in first['isolation_checks']}
            if server is not None:
                assert server.requests == [{'get': True, 'authorized': True}]
                required = {'https_profile_denied', 'https_private_key_denied', 'https_direct_tcp_denied'}
                assert required <= first['isolation_checks'].keys()
                assert required <= second['isolation_checks'].keys()
            passed = confined and negative and all(checks.values()) and audit.read_bytes() == b'original'
            return {'protocol': protocol, 'transport': transport,
                    'https_requests': len(server.requests) if server is not None else 0,
                    'https_negative_controls': https_negative, 'status': 'PASS' if passed else 'FAIL',
                    'pipeline_checks': first['pipeline_checks'] | second['pipeline_checks'] |
                        {'canonical_recomputation': True, 'fresh_restart_identity': True},
                    'isolation_checks': checks, 'negative_controls': negative,
                    'metadata_attempts_after_revocation': mh.last['budget']['attempts'],
                    'observations': len(observations), 'attempts_after_revocation': h.last['budget']['attempts'],
                    'unknown_count': expected['unknown_count'], 'audit_id': expected['audit_id'],
                    'execution_allowed': False, 'live_ready': False}
    finally:
        if mh.process and mh.process.poll() is None:
            mh.close()
        if h.process and h.process.poll() is None:
            h.close()


def run_integration(*, transport="local"):
    if transport not in ("local", "https"):
        raise ValueError("fixed laboratory transport required")
    prerequisite = run_lab()
    if prerequisite['status'] != 'PASS':
        return result('BLOCKED', 'kernel_prerequisite_' + prerequisite['reason'])
    try:
        with tempfile.TemporaryDirectory(prefix='orion-isolated-broker-') as temporary:
            cases = [run_case(Path(temporary) / protocol, protocol, transport=transport)
                     for protocol in ('local_rows_v1', 'local_columns_v1')]
        report = result('PASS' if all(c['status'] == 'PASS' for c in cases) else 'FAIL',
                        'local_https_consumer_only' if transport == 'https' else 'local_broker_consumer_only', negative_controls=True)
        report['cases'] = cases
        return report
    except ImportError:
        return result('BLOCKED', 'development_fixture_dependencies_missing')
    except OSError:
        return result('BLOCKED', 'local_runtime_unavailable')
    except Exception:  # noqa: BLE001 - do not expose source/credential exception details
        return result('FAIL', 'broker_consumer_lifecycle_failed')


if __name__ == '__main__':
    print(json.dumps(run_integration(), sort_keys=True))
