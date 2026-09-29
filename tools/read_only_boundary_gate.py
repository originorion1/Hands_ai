"""Execute offline boundary witnesses. Exit 2 means unresolved release requirements."""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree

from orion_lab import Orchestrator

from orion.pilot.boundary_report import boundary_report


def run():
    root = Path.cwd()
    with tempfile.TemporaryDirectory(prefix='orion-boundary-') as temporary:
        report = Path(temporary) / 'tests.xml'
        result = subprocess.run([sys.executable, '-m', 'pytest', '-q',
                                 f'--junitxml={report}'], check=False)
        cases = ElementTree.parse(report).findall('.//testcase') if report.exists() else []
        checks = {'full_test_suite': result.returncode == 0 and bool(cases)}
        checks['ruff'] = subprocess.run([sys.executable, '-m', 'ruff', 'check', '.'],
                                        check=False).returncode == 0
        paths = tuple(p for area in ('src', 'tests', 'tools') for p in (root / area).rglob('*.py'))
        try:
            for path in paths:
                compile(path.read_bytes(), str(path), 'exec')
            checks['compilation'] = True
        except (SyntaxError, OSError):
            checks['compilation'] = False
        sources = tuple(str(p.relative_to(root)) for p in paths if p.relative_to(root).parts[0] != 'tests')
        checks['capability_scan'] = Orchestrator.source_capability_scan(root, sources)
        checks['diff'] = all(subprocess.run(['git', 'diff', *flags, '--check'], check=False).returncode == 0
                             for flags in ((), ('--cached',)))
        demo = subprocess.run([sys.executable, '-m', 'orion.demo'], capture_output=True,
                               text=True, check=False)
        try:
            checks['demo'] = demo.returncode == 0 and json.loads(demo.stdout)['execution_allowed'] is False
        except (ValueError, KeyError):
            checks['demo'] = False
        output = boundary_report(cases, checks)
        output['test_count'] = len(cases)
        print(json.dumps(output, sort_keys=True))
        return 0 if output['READ_ONLY_PILOT_BOUNDARY'] == 'READ_ONLY_PILOT_BOUNDARY_PASS' else 2


if __name__ == '__main__':
    raise SystemExit(run())
