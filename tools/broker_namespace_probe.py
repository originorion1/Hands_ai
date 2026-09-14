"""Fixed synthetic broker namespace proof; cannot activate networking or a pilot."""
import json
import os
import signal
import subprocess
import sys
from pathlib import Path


def run():
    report = {'status': 'BLOCKED', 'reason': 'broker_namespace_probe_unavailable',
              'execution_allowed': False, 'allow_live_customer_access': False,
              'live_ready': False, 'production_containment': 'NOT PROVEN'}
    process = None
    try:
        fixture = Path(__file__).resolve().parents[1] / 'tests' / 'broker_namespace_lab.py'
        process = subprocess.Popen([sys.executable, '-I', str(fixture)], stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, text=True, close_fds=True, start_new_session=True,
            env={'PATH': os.defpath})
        stdout, stderr = process.communicate(timeout=45)
        if process.returncode or stderr or len(stdout) > 32768:
            raise ValueError('invalid probe output')
        value = json.loads(stdout)
        if (type(value) is not dict or value.get('status') not in ('PASS', 'FAIL', 'BLOCKED')
                or value.get('production_containment') != 'NOT PROVEN'
                or any(value.get(k) is not False for k in
                       ('execution_allowed', 'allow_live_customer_access', 'live_ready'))):
            raise ValueError('invalid probe report')
        report = value
    except (OSError, ValueError, subprocess.TimeoutExpired):
        pass
    finally:
        if process and process.poll() is None:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
    print(json.dumps(report, sort_keys=True))
    return 0 if report['status'] == 'PASS' else 2


if __name__ == '__main__':
    if len(sys.argv) != 1:
        raise SystemExit(2)
    raise SystemExit(run())
