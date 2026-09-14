"""Adversarial local-profile tests; BLOCKED never counts as kernel proof."""
import json
import os
import subprocess
import sys
from pathlib import Path

import broker_namespace_lab as lab
import pytest


def test_unavailable_kernel_never_starts_broker(monkeypatch):
    monkeypatch.setattr(lab, 'run_lab', lambda: {'status': 'BLOCKED', 'reason': 'unavailable'})
    monkeypatch.setattr(lab, 'run_case', lambda *a: pytest.fail('broker invoked'))
    result = lab.run()
    assert result['status'] == 'BLOCKED' and result['cases'] == []
    assert result['live_ready'] is False


def test_root_owner_is_not_certified(monkeypatch):
    monkeypatch.setattr(lab, 'run_lab', lambda: {'status': 'PASS'})
    monkeypatch.setattr(lab.os, 'getuid', lambda: 0)
    monkeypatch.setattr(lab, 'run_case', lambda *a: pytest.fail('broker invoked'))
    assert lab.run()['reason'] == 'unprivileged_owner_required'


@pytest.mark.parametrize('mutation', ['missing', 'false', 'integer', 'extra'])
def test_witness_cannot_omit_or_forge_denial(mutation):
    checks = dict.fromkeys(lab.CHECKS + lab.EXTRA, True)
    assert lab.verify_checks(checks)
    if mutation == 'missing':
        del checks['tcp_denied']
    elif mutation == 'false':
        checks['tcp_denied'] = False
    elif mutation == 'integer':
        checks['tcp_denied'] = 1
    else:
        checks['unrequested'] = True
    assert not lab.verify_checks(checks)


def test_fixed_profile_retains_network_namespace_and_only_state_is_writable(monkeypatch, tmp_path):
    monkeypatch.setattr(lab.os, 'getuid', lambda: 1000)
    monkeypatch.setattr(lab.os, 'getgid', lambda: 1000)
    config, source, state = (tmp_path / n for n in ('config', 'source', 'state'))
    command = lab.broker_command(config, source, state)
    assert '--unshare-all' in command and '--share-net' not in command
    assert command[command.index('--cap-drop') + 1] == 'ALL'
    assert '--clearenv' in command
    assert command.count('--bind') == 1
    assert command[command.index('--bind') + 1:command.index('--bind') + 3] == [str(state)] * 2
    for path in (config, source):
        i = command.index(str(path))
        assert command[i - 1] == '--ro-bind'
        assert i > command.index('--tmpfs')
    assert command.index('--bind') > command.index('--tmpfs')
    assert '--setenv' not in command


def test_unconfined_child_fails_before_broker_start(tmp_path):
    # Real fresh process, not a mocked PASS. Identical-namespace and writable
    # config/source witnesses must reject before source admission even with keys.
    paths = {n: tmp_path / n for n in ('secret', 'audit', 'config', 'source')}
    for path in paths.values():
        path.write_text('synthetic')
    scope = {k: str(v) for k, v in paths.items()}
    scope.update(unix=str(tmp_path / 'socket'), port=1, parent=os.getpid(), uid=os.getuid())
    scope.update({k: os.readlink('/proc/self/ns/' + k) for k in ('net', 'pid', 'mnt', 'user')})
    bootstrap = {'config': str(paths['config']), 'state': str(tmp_path / 'state'), 'head': None,
                 'key': 'synthetic-key', 'secret': 'synthetic-secret', 'scope': scope}
    result = subprocess.run([sys.executable, '-I', str(Path(lab.__file__)), '--child'],
        input=json.dumps(bootstrap) + '\n', text=True, capture_output=True, timeout=5, check=False)
    assert result.returncode == 2 and not result.stderr
    output = json.loads(result.stdout)
    if 'checks' in output:
        checks = output['checks']
        assert not checks['network_namespace_changed']
        assert not checks['secret_denied']
        assert not checks['config_write_denied']
    else:
        assert output == {'status': 'blocked', 'reason': 'custody_probe_unavailable'}
    assert not (tmp_path / 'state').exists()
    assert 'synthetic-key' not in result.stdout and 'synthetic-secret' not in result.stdout


def test_actual_kernel_broker_profile_or_explicit_blocker():
    result = lab.run()
    assert result['status'] in ('PASS', 'BLOCKED'), result
    assert result['execution_allowed'] is False
    assert result['live_ready'] is False
    assert result['production_containment'] == 'NOT PROVEN'
    if result['status'] == 'PASS':
        assert {c['protocol'] for c in result['cases']} == {'local_rows_v1', 'local_columns_v1'}
        for case in result['cases']:
            assert case['negative_controls'] and case['observations'] == 1
            assert case['attempts_after_revoked_restart'] == 1
            assert len(case['checks']) == 2
            assert all(lab.verify_checks(w) for w in case['checks'])
