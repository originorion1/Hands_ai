"""Run the fixed synthetic broker/consumer integration; never activate a pilot."""
import json
import os
import subprocess
import sys
from pathlib import Path


def run():
    fixture = Path(__file__).resolve().parents[1] / 'tests' / 'isolated_broker_lab.py'
    try:
        completed = subprocess.run([sys.executable, '-I', str(fixture)], capture_output=True,
            text=True, timeout=60, check=False, close_fds=True, cwd=fixture.parents[1], env={'PATH': os.defpath})
        if completed.returncode or completed.stderr or len(completed.stdout) > 16384:
            raise ValueError('fixture failed')
        report = json.loads(completed.stdout)
        if (type(report) is not dict or report.get('status') not in ('PASS', 'FAIL', 'BLOCKED')
                or report.get('production_containment') != 'NOT PROVEN'
                or any(report.get(k) is not False for k in
                       ('live_ready', 'allow_live_customer_access', 'execution_allowed'))):
            raise ValueError('invalid fixture result')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        report = {'status': 'BLOCKED', 'reason': 'integration_probe_unavailable',
                  'execution_allowed': False, 'allow_live_customer_access': False, 'live_ready': False}
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(run())
