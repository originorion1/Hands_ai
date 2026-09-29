"""Fixed synthetic broker namespace laboratory. No production egress profile."""
import json
import os
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

if __name__ == '__main__':
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
    sys.path.insert(0, str(Path(__file__).resolve().parent))

from isolation_lab import CHECKS, connectable, inspect_child, namespace_command, readable, run_lab

EXTRA = ('source_write_denied', 'config_write_denied')


def readonly(path):
    try:
        with open(path, 'ab'):
            return False
    except OSError:
        return True


def child():
    from orion.pilot.broker import main
    from orion.pilot.broker_contract import MAX_FRAME, decode, exact
    # This private owner pipe is not the application request interface. No secret
    # is passed in argv, mounted configuration, or the launch environment.
    raw = sys.stdin.buffer.readline(MAX_FRAME + 1)
    if not raw.endswith(b'\n') or len(raw) > MAX_FRAME:
        raise ValueError('bootstrap denied')
    value = exact(decode(raw), ('config', 'state', 'head', 'key', 'secret', 'scope'))
    try:
        checks = inspect_child(value['scope'])
    except OSError:
        print('{"status":"blocked","reason":"custody_probe_unavailable"}', flush=True)
        return 2
    checks['unprivileged_uid'] = os.getuid() > 0 and os.getuid() == value['scope']['uid']
    checks.update(source_write_denied=readonly(value['scope']['source']),
                  config_write_denied=readonly(value['config']))
    print(json.dumps({'checks': checks}), flush=True)
    if not all(checks.values()):
        return 2
    os.environ['BROKER_AUTH_KEY'] = value['key']
    os.environ['BROKER_SOURCE_SECRET'] = value['secret']
    args = ['--config', value['config'], '--state', value['state']]
    if value['head'] is not None:
        args += ['--expected-head', value['head']]
    return main(args)


def broker_command(config, source, state):
    """Owner-only fixed profile. Never accepts an application mount/command list."""
    if os.getuid() == 0:
        raise ValueError('unprivileged owner required')
    root = Path(__file__).resolve().parents[1]
    command = namespace_command(Path(__file__), readonly=(
        (root / 'src', '/src'), (Path(__file__).parent / 'isolation_lab.py', '/work/isolation_lab.py')))
    # Keep the owner's unprivileged mapped ID for the dedicated 0700 journal.
    # The semantic consumer's separate 65534 profile remains unchanged.
    command[command.index('--uid') + 1] = str(os.getuid())
    command[command.index('--gid') + 1] = str(os.getgid())
    # Mount after the private /tmp, otherwise a /tmp fixture is hidden by it.
    index = command.index('--chdir')
    command[index:index] = ['--ro-bind', str(config), str(config),
                            '--ro-bind', str(source), str(source),
                            '--bind', str(state), str(state)]
    return command


def verify_checks(value):
    return (type(value) is dict and set(value) == set(CHECKS + EXTRA)
            and all(v is True for v in value.values()))


def run_case(root, protocol, command):
    from test_supervised_broker import Harness

    from orion.pilot.broker_contract import observations_from
    from orion.understanding.role_checkpoint import _json

    h = Harness(root / 'broker', protocol)
    config = h.root / 'config.json'
    config.write_text(_json(h.config)); config.chmod(0o600)
    secret, audit = root / 'unrelated-secret', root / 'unrelated-audit'
    secret.write_text('synthetic-custody-canary'); secret.chmod(0o600)
    audit.write_text('unchanged'); audit.chmod(0o600)
    with audit.open('a') as stream:
        stream.write('-negative-control')
    source_before = h.source.read_bytes()
    config_before = config.read_bytes()
    with socket.socket() as tcp, socket.socket(socket.AF_UNIX) as unix:
        tcp.bind(('127.0.0.1', 0)); tcp.listen(4)
        path = str(root / 'unrelated-socket')
        unix.bind(path); unix.listen(4)
        port = tcp.getsockname()[1]
        negative = (readable(secret) and readable(audit) and
                    connectable(socket.AF_INET, ('127.0.0.1', port)) and
                    connectable(socket.AF_UNIX, path) and
                    not readonly(config) and not readonly(h.source))
        if not negative:
            raise ValueError('negative controls unavailable')
        scope = {'secret': str(secret), 'audit': str(audit), 'unix': path, 'port': port,
                 'parent': os.getpid(), 'uid': os.getuid(), 'source': str(h.source)}
        scope.update({k: os.readlink('/proc/self/ns/' + k) for k in ('net', 'pid', 'mnt', 'user')})
        witnesses = []

        def start(head=None):
            h.process = subprocess.Popen(command(config, h.source, h.state), stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, close_fds=True,
                env={'PATH': os.defpath, 'ORION_ISOLATION_CANARY': 'synthetic',
                     'HTTPS_PROXY': 'http://127.0.0.1:1'})
            bootstrap = {'config': str(config), 'state': str(h.state), 'head': head,
                         'key': h.key.decode(), 'secret': h.secret, 'scope': scope}
            h.process.stdin.write(_json(bootstrap) + '\n'); h.process.stdin.flush()
            output = json.loads(h.process.stdout.readline())
            if set(output) != {'checks'} or not verify_checks(output['checks']):
                raise ValueError('broker isolation witnesses failed')
            witnesses.append(output['checks'])
            h.last = json.loads(h.process.stdout.readline())
            assert h.last['status'] == 'unarmed'

        try:
            start()
            assert h.send(h.message())['budget']['attempts'] == 0
            assert h.control('arm')['status'] == 'arm'
            attacks = [dict(h.message(), operation='write'), dict(h.message(), grant_token='0' * 64),
                       dict(h.message(), url='https://unapproved.invalid/'), {'proposal': 'read'},
                       {'review': 'validated'}, dict(h.message(), credential='synthetic')]
            wrong = h.message(); wrong['request']['tenant_id'] = 'other'
            attacks.append(wrong)
            for message in attacks:
                denied = h.send(message)
                assert denied['status'] == 'denied' and denied['budget']['attempts'] == 0
            admitted = h.send(h.message())
            assert admitted['status'] == 'admitted' and admitted['budget']['attempts'] == 1
            observations = observations_from(admitted['observations'])
            assert observations[0].evidence.payload['record'] == h.rows[0]
            assert observations[0].evidence.tenant_id == h.request.tenant_id
            assert h.secret not in _json(admitted) and h.key.decode() not in _json(admitted)
            assert h.send(h.message())['status'] == 'denied'
            h.control('revoke'); head = h.close()['head']
            start(head)
            assert h.control('arm')['status'] == 'denied'
            denied = h.send(h.message('restart'))
            assert denied['status'] == 'denied' and denied['budget']['attempts'] == 1
            assert audit.read_text() == 'unchanged-negative-control'
            assert source_before == h.source.read_bytes() and config_before == config.read_bytes()
            assert not denied['execution_allowed'] and not denied['allow_live_customer_access']
            return {'status': 'PASS', 'protocol': protocol, 'checks': witnesses,
                    'negative_controls': negative, 'observations': len(observations),
                    'attempts_after_revoked_restart': 1, 'execution_allowed': False}
        finally:
            if h.process and h.process.poll() is None:
                h.process.kill(); h.process.wait(timeout=5)


def run():
    report = {'status': 'BLOCKED', 'reason': 'kernel_prerequisite', 'cases': [],
              'execution_allowed': False, 'allow_live_customer_access': False,
              'live_ready': False, 'production_containment': 'NOT PROVEN'}
    prerequisite = run_lab()
    if prerequisite['status'] != 'PASS':
        report['reason'] = 'kernel_prerequisite_' + prerequisite['reason']
        return report
    if os.getuid() == 0:
        report['reason'] = 'unprivileged_owner_required'
        return report
    try:
        with tempfile.TemporaryDirectory(prefix='orion-broker-namespace-') as temporary:
            report['cases'] = [run_case(Path(temporary) / p, p, broker_command)
                               for p in ('local_rows_v1', 'local_columns_v1')]
        report.update(status='PASS', reason='local_zero_egress_broker_only')
    except (OSError, ImportError):
        report.update(status='BLOCKED', reason='local_runtime_unavailable')
    except Exception:  # noqa: BLE001 - no bootstrap, credentials, paths or traces
        report.update(status='FAIL', reason='broker_namespace_lifecycle_failed')
    return report


if __name__ == '__main__':
    if sys.argv[1:] == ['--child']:
        try:
            raise SystemExit(child())
        except Exception:  # noqa: BLE001 - never disclose private bootstrap
            raise SystemExit(2) from None
    elif len(sys.argv) == 1:
        print(json.dumps(run(), sort_keys=True))
    else:
        raise SystemExit(2)
