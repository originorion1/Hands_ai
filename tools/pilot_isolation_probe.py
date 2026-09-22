"""Fixed local experiment launcher; never grants authority or changes release gates."""
import json
import os
import subprocess
import sys
from pathlib import Path


def run():
    fixture = Path(__file__).resolve().parents[1] / 'tests' / 'isolation_lab.py'
    try:
        completed = subprocess.run([sys.executable, '-I', str(fixture)],
            capture_output=True, text=True, timeout=15, close_fds=True, check=False, env={'PATH': os.defpath})
        if completed.returncode or completed.stderr or len(completed.stdout) > 8192:
            raise ValueError('fixture failed')
        report = json.loads(completed.stdout)
        if (type(report) is not dict or report.get('production_containment') != 'NOT PROVEN' or
                report.get('status') not in ('PASS', 'FAIL', 'BLOCKED') or
                any(report.get(key) is not False for key in
                    ('live_ready', 'execution_allowed', 'allow_live_customer_access'))):
            raise ValueError('invalid fixture result')
    except (OSError, ValueError, subprocess.TimeoutExpired):
        report = {'status': 'BLOCKED', 'reason': 'isolation_probe_unavailable',
                  'live_ready': False, 'execution_allowed': False, 'allow_live_customer_access': False}
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'PASS' else 2


if __name__ == '__main__':
    raise SystemExit(run())
