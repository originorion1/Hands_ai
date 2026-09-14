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
from dataclasses import asdict, replace
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


def relay(h, scope, *, confined):
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
            for _ in range(8):
                frame = frames.read()
                if type(frame) is dict and set(frame) == {'consumer_result'}:
                    child.stdin.close()
                    child.wait(timeout=5)
                    if child.returncode or child.stderr.read(4097):
                        raise ValueError('consumer failed')
                    text = _json(frame)
                    if h.secret in text or h.key.decode() in text:
                        raise ValueError('consumer secret leakage')
                    return frame['consumer_result'], observations, trace
                response = h.send(frame)  # Ordinary broker API; no control signing here.
                trace.append((response['status'], response['budget']['attempts']))
                if response['status'] == 'admitted':
                    observations = observations_from(response['observations'])
                child.stdin.write(_json(response).encode() + b'\n'); child.stdin.flush()
            raise ValueError('consumer message budget exhausted')
        finally:
            if child.poll() is None:
                child.kill(); child.wait(timeout=5)


def run_case(root, protocol, *, confined=True):
    # Existing dev fixtures are used only by the trusted owner, never mounted in the consumer.
    from isolated_semantic_consumer import semantic_state
    from semantic_lab import Organization
    from test_supervised_broker import Harness

    from orion.contracts import EvidenceKind, utc_now
    from orion.discovery.pilot_read import PilotAuthorization, PilotRequest
    from orion.discovery.read_window import ReviewedReadWindow
    from orion.history.evidence import _observation_to_data
    from orion.understanding.role_checkpoint import _json

    lab = Organization('C')  # Existing admitted opaque metadata, no record acquisition here.
    fields = (*lab.fields, lab.identity, lab.partition)
    rows = [dict(zip(fields, (*values, f's_{i}', lab.company), strict=True))
            for i, values in enumerate(lab.values)]
    request = PilotRequest(lab.tenant, lab.company, lab.source, lab.resources[0], fields,
                           lab.fields[2], date(2024, 6, 1), date(2024, 6, 7), 2)
    grant = PilotAuthorization('isolated-read', lab.source, ReviewedReadWindow(lab.tenant,
        lab.company, request.resource, fields, request.date_field, request.start, request.end,
        utc_now() + timedelta(hours=1)), lab.identity, lab.partition, 'local-records', EvidenceKind.EXPERIMENT, 2)
    h = Harness(root, protocol, rows=rows, request=request, grant=grant)
    try:
        with ExitStack() as stack:
            port, unix_path, negative = 0, str(root / 'listener'), False
            if confined:
                tcp = stack.enter_context(socket.socket(socket.AF_INET, socket.SOCK_STREAM))
                unix = stack.enter_context(socket.socket(socket.AF_UNIX, socket.SOCK_STREAM))
                tcp.bind(('127.0.0.1', 0)); tcp.listen(4)
                unix.bind(unix_path); unix.listen(4)
                port = tcp.getsockname()[1]
                negative = connectable(socket.AF_INET, ('127.0.0.1', port)) and connectable(socket.AF_UNIX, unix_path)
            assert h.start()['status'] == 'unarmed'
            assert h.control('arm')['status'] == 'arm'
            secret, audit = root / 'secret-canary', root / 'audit-canary'
            secret.write_text(h.secret); secret.chmod(0o600)
            audit.write_bytes(b'original'); audit.chmod(0o600)
            scope = {'schema': [_observation_to_data(replace(lab.base.schema, evidence=replace(
                lab.base.schema.evidence, payload=json.loads(_json(lab.base.schema.evidence.payload)))))], 'message': h.message(),
                'instruments': [asdict(i) for i in lab.instruments], 'source_denied': str(h.source),
                'configuration_denied': str(root / 'config.json'), 'journal_write_denied': str(h.state / 'broker.db'),
                'isolation': {'secret': str(secret), 'audit': str(audit), 'unix': unix_path,
                    'port': port, 'parent': h.process.pid,
                    **{kind: os.readlink('/proc/self/ns/' + kind) for kind in ('net', 'pid', 'mnt', 'user')}}}
            negative = negative and readable(secret) and readable(h.source) and readable(f'/proc/{h.process.pid}/environ')
            first, observations, trace = relay(h, scope, confined=confined)
            assert trace == [('denied', 0)] * 3 + [('admitted', 1), ('denied', 1)]
            assert all(first['pipeline_checks'].values()) and len(observations) == 2
            # Consumer output is not trusted as an archive or semantic attestation.
            expected = semantic_state(scope, observations)
            assert first['state'] == expected
            assert h.control('revoke')['status'] == 'revoke'
            head = h.close()['head']
            assert h.start(head)['status'] == 'unarmed'
            assert h.control('arm')['status'] == 'denied'
            scope.update(checkpoint=expected['checkpoint'], checkpoint_sha256=expected['checkpoint_sha256'],
                         observations=[_observation_to_data(o) for o in observations])
            scope['isolation']['parent'] = h.process.pid
            second, acquired, trace = relay(h, scope, confined=confined)
            assert trace == [('denied', 1)] and not acquired
            assert all(second['pipeline_checks'].values()) and second['state'] == expected
            checks = {k: first['isolation_checks'][k] and second['isolation_checks'][k]
                      for k in first['isolation_checks']}
            passed = confined and negative and all(checks.values()) and audit.read_bytes() == b'original'
            return {'protocol': protocol, 'status': 'PASS' if passed else 'FAIL',
                    'pipeline_checks': first['pipeline_checks'] | second['pipeline_checks'] |
                        {'canonical_recomputation': True, 'fresh_restart_identity': True},
                    'isolation_checks': checks, 'negative_controls': negative,
                    'observations': len(observations), 'attempts_after_revocation': h.last['budget']['attempts'],
                    'unknown_count': expected['unknown_count'], 'audit_id': expected['audit_id'],
                    'execution_allowed': False, 'live_ready': False}
    finally:
        if h.process and h.process.poll() is None:
            h.close()


def run_integration():
    prerequisite = run_lab()
    if prerequisite['status'] != 'PASS':
        return result('BLOCKED', 'kernel_prerequisite_' + prerequisite['reason'])
    try:
        with tempfile.TemporaryDirectory(prefix='orion-isolated-broker-') as temporary:
            cases = [run_case(Path(temporary) / protocol, protocol)
                     for protocol in ('local_rows_v1', 'local_columns_v1')]
        report = result('PASS' if all(c['status'] == 'PASS' for c in cases) else 'FAIL',
                        'local_broker_consumer_only', negative_controls=True)
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
