"""Run a fixed loopback TLS laboratory. No production activation or egress option."""
import json
import os
import subprocess
import sys
from pathlib import Path


def run():
    fixture = Path(__file__).resolve().parents[1] / 'tests' / 'https_broker_lab.py'
    try:
        result = subprocess.run([sys.executable, '-I', str(fixture)], capture_output=True,
            text=True, timeout=60, check=False, close_fds=True, cwd=fixture.parents[1], env={'PATH': os.defpath})
        if result.returncode or result.stderr or len(result.stdout) > 16384:
            raise ValueError('fixture execution failed')
        report = json.loads(result.stdout)
        if (type(report) is not dict or report.get('status') not in ('PASS', 'FAIL', 'BLOCKED')
                or report.get('production_containment') != 'NOT PROVEN'
                or any(report.get(k) is not False for k in ('live_ready', 'execution_allowed', 'allow_live_customer_access'))):
            raise ValueError('fixture result invalid')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        report = {'status': 'BLOCKED', 'reason': 'local_https_probe_unavailable',
                  'live_ready': False, 'execution_allowed': False, 'allow_live_customer_access': False,
                  'production_containment': 'NOT PROVEN'}
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(run())
