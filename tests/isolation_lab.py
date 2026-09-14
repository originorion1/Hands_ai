"""Synthetic kernel-isolation experiment. Socket operations are loopback/local only.

This fixed fixture is not an application adapter, broker or production sandbox.
"""
import json
import os
import secrets
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

CHECKS = (
    'secret_denied', 'audit_write_denied', 'tcp_denied', 'unix_denied',
    'parent_environment_denied', 'environment_cleared', 'descriptors_closed',
    'network_namespace_changed', 'pid_namespace_changed', 'mount_namespace_changed',
    'user_namespace_changed', 'capabilities_dropped', 'unprivileged_uid',
)


def readable(path):
    try:
        with open(path, 'rb') as stream:
            stream.read(1)
        return True
    except OSError:
        return False


def connectable(family, destination):
    with socket.socket(family, socket.SOCK_STREAM) as client:
        client.settimeout(0.3)
        try:
            client.connect(destination)
            return True
        except OSError:
            return False


def inspect_child(scope):
    try:
        # Never create a same-path shadow inside the private filesystem.
        # Test write access to an existing target, including write-only files.
        fd = os.open(scope['audit'], os.O_WRONLY | os.O_APPEND)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(b'canary-mutation')
        audit_denied = False
    except OSError:
        audit_denied = True
    descriptors = []
    for fd in Path('/proc/self/fd').iterdir():
        try:
            if int(fd.name) > 2:
                descriptors.append(os.readlink(fd))
        except FileNotFoundError:
            pass  # The directory iteration descriptor can already be closed.
    status = dict(line.split(':', 1) for line in Path('/proc/self/status').read_text().splitlines()
                  if ':' in line)
    result = {
        'secret_denied': not readable(scope['secret']),
        'audit_write_denied': audit_denied,
        'tcp_denied': not connectable(socket.AF_INET, ('127.0.0.1', scope['port'])),
        'unix_denied': not connectable(socket.AF_UNIX, scope['unix']),
        'parent_environment_denied': not readable(f"/proc/{scope['parent']}/environ"),
        'environment_cleared': 'ORION_ISOLATION_CANARY' not in os.environ and
                               not any('proxy' in name.lower() for name in os.environ),
        'descriptors_closed': not descriptors,
        'capabilities_dropped': int(status['CapEff'], 16) == 0,
        'unprivileged_uid': os.getuid() == 65534,
    }
    for kind in ('net', 'pid', 'mnt', 'user'):
        name = {'net': 'network', 'mnt': 'mount'}.get(kind, kind)
        result[name + '_namespace_changed'] = os.readlink('/proc/self/ns/' + kind) != scope[kind]
    return result


def result(status, reason, *, negative_controls=False, checks=None):
    return {'version': 1, 'status': status, 'reason': reason,
            'negative_controls': negative_controls, 'checks': checks or {},
            'execution_allowed': False, 'allow_live_customer_access': False,
            'live_ready': False, 'production_containment': 'NOT PROVEN'}


def assess_child(returncode, stdout, stderr, *, negative_controls, audit_unchanged):
    if returncode:
        # A failed launcher never establishes isolation. Suppress paths/exception text.
        return result('BLOCKED', 'namespace_launch_failed', negative_controls=negative_controls)
    if stderr or len(stdout) > 4096:
        return result('FAIL', 'invalid_child_output', negative_controls=negative_controls)
    try:
        def unique(pairs):
            value = {}
            for key, item in pairs:
                if key in value:
                    raise ValueError('duplicate')
                value[key] = item
            return value
        checks = json.loads(stdout, object_pairs_hook=unique)
        if type(checks) is not dict or set(checks) != set(CHECKS) or any(
                type(value) is not bool for value in checks.values()):
            raise ValueError('shape')
    except (ValueError, TypeError):
        return result('FAIL', 'invalid_child_output', negative_controls=negative_controls)
    passed = negative_controls and audit_unchanged and all(checks.values())
    return result('PASS' if passed else 'FAIL', 'local_profile_only' if passed else
                  'isolation_assertion_failed', negative_controls=negative_controls, checks=checks)


def namespace_command(script, *, readonly=()):
    """Trusted fixture composition of the same profile; no CLI mount/code selectors."""
    bwrap = shutil.which('bwrap')
    executable = str(Path(sys.executable).resolve())
    prefix = Path(sys.base_prefix).resolve()
    if bwrap is None or prefix == Path('/') or not Path(executable).is_relative_to(prefix):
        raise ValueError('namespace runtime unavailable')
    command = [bwrap, '--unshare-all', '--die-with-parent', '--new-session',
               '--cap-drop', 'ALL', '--uid', '65534', '--gid', '65534', '--clearenv']
    for directory in ('/usr', '/lib', '/lib64', '/bin'):
        if Path(directory).exists():
            command.extend(('--ro-bind', directory, directory))
    if not str(prefix).startswith('/usr'):
        command.extend(('--ro-bind', str(prefix), str(prefix)))
    for source, target in readonly:
        command.extend(('--ro-bind', str(source), target))
    command.extend(('--proc', '/proc', '--dev', '/dev', '--tmpfs', '/tmp',
                    '--dir', '/work', '--ro-bind', str(Path(script).resolve()),
                    '/work/probe.py', '--chdir', '/work', executable,
                    '-I', '-S', '/work/probe.py', '--child'))
    return command


def run_lab():
    bwrap = shutil.which('bwrap')
    executable = str(Path(sys.executable).resolve())
    if bwrap is None:
        return result('BLOCKED', 'bubblewrap_unavailable')
    prefix = Path(sys.base_prefix).resolve()
    if prefix == Path('/') or not Path(executable).is_relative_to(prefix):
        return result('BLOCKED', 'unsupported_python_runtime_location')
    stage = 'temporary_storage'
    try:
        with tempfile.TemporaryDirectory(prefix='orion-isolation-') as temporary:
            root = Path(temporary)
            secret, audit = root / 'secret', root / 'audit'
            secret.write_text(secrets.token_hex(32)); secret.chmod(0o600)
            audit.write_bytes(b'original-audit'); audit.chmod(0o600)
            stage = 'local_socket_setup'
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as tcp, socket.socket(
                    socket.AF_UNIX, socket.SOCK_STREAM) as unix:
                tcp.bind(('127.0.0.1', 0)); tcp.listen(4)
                unix_path = str(root / 'listener')
                unix.bind(unix_path); unix.listen(4)
                port = tcp.getsockname()[1]
                negative = readable(secret) and connectable(socket.AF_INET, ('127.0.0.1', port)) and (
                    connectable(socket.AF_UNIX, unix_path))
                with audit.open('ab') as stream:
                    stream.write(b'-negative-control')
                original = audit.read_bytes()
                if not negative:
                    return result('BLOCKED', 'negative_control_failed')
                stage = 'namespace_launch'
                scope = {'secret': str(secret), 'audit': str(audit), 'unix': unix_path,
                         'port': port, 'parent': os.getpid()}
                scope.update({kind: os.readlink('/proc/self/ns/' + kind)
                              for kind in ('net', 'pid', 'mnt', 'user')})
                command = namespace_command(Path(__file__))
                # A canary environment entry proves --clearenv, without real credentials.
                completed = subprocess.run(command, input=json.dumps(scope), capture_output=True,
                    text=True, timeout=10, close_fds=True, check=False,
                    env={'PATH': os.defpath, 'ORION_ISOLATION_CANARY': secrets.token_hex(16)})
                return assess_child(completed.returncode, completed.stdout, completed.stderr,
                                    negative_controls=negative, audit_unchanged=audit.read_bytes() == original)
    except subprocess.TimeoutExpired:
        return result('BLOCKED', 'probe_timeout')
    except OSError:
        return result('BLOCKED', stage + '_unavailable')


if __name__ == '__main__':
    if sys.argv[1:] == ['--child']:
        print(json.dumps(inspect_child(json.loads(sys.stdin.read(8193)))))
    elif len(sys.argv) == 1:
        print(json.dumps(run_lab(), sort_keys=True))
    else:
        raise SystemExit(2)
