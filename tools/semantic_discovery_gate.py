"""Execute the offline semantic gate. No live transport or activation path.

Run from the repository root with development dependencies installed. The result
is an engineering test gate under documented trusted-instrument assumptions,
not a live-pilot readiness or independent security certification.
"""
import json
import subprocess
import sys
import tempfile
from pathlib import Path
from xml.etree import ElementTree

from orion_lab import Orchestrator

# Executable evidence for each acceptance criterion; never user-provided results.
CRITERIA = {
    'unfamiliar_discovery': 'test_orion_understands_an_unfamiliar_organization_from_independent_evidence',
    'evidence_hypotheses': 'test_orion_understands_an_unfamiliar_organization_from_independent_evidence',
    'competing_interpretations': 'test_ambiguous_dates_are_not_selected_by_order_or_equal_values',
    'independent_validation': 'test_orion_understands_an_unfamiliar_organization_from_independent_evidence',
    'contradiction_revision': 'test_organization_b_contradiction_revision_and_reconvergence',
    'unknown': 'test_organization_c_refuses_high_structural_confidence',
    'no_confidence_promotion': 'test_organization_c_refuses_high_structural_confidence',
    'temporal_grounding': 'test_changing_only_process_evidence_reverses_date_or_relationship',
    'relationship_grounding': 'test_missing_relationship_target_records_prevent_semantic_promotion',
    'process_provenance': 'test_untrusted_or_cross_scope_evidence_fails_closed',
    'lineage_independence': 'test_shared_lineage_of_transformed_copy_is_not_another_independent_class',
    'renaming_invariance': 'test_names_field_order_and_irrelevant_metadata_are_not_semantic_authority',
    'world_model_status': 'test_orion_understands_an_unfamiliar_organization_from_independent_evidence',
    'restart_without_authority': 'test_semantic_restart_in_a_new_process_without_authority',
    'proposal_only': 'test_planner_is_bounded_sensitive_default_denial_and_proposal_only',
    'no_record_grants': 'test_evidence_acquisition_still_requires_fresh_external_authority',
    'untrusted_text': 'test_malicious_process_evidence_remains_data',
    'tenant_isolation': 'test_another_tenant_evidence_and_checkpoint_rejected',
    'regression': 'test_fresh_acquisition_reproduces_semantic_claims',
    'offline_only': 'test_metadata_hypotheses_and_predictions_are_not_semantic_observations',
}


def run():
    root = Path.cwd()
    checks = {}
    counts = {}
    evidence = {}
    with tempfile.TemporaryDirectory(prefix='orion-semantic-gate-') as temporary:
        for suite, files in (('full_pytest', ()), ('focused_pytest', (
                'tests/test_semantic_discovery.py', 'tests/test_semantic_restart.py',
                'tests/test_role_study.py', 'tests/test_role_checkpoint.py',
                'tests/test_process_roles.py', 'tests/test_pilot_recovery.py'))):
            report = Path(temporary) / f'{suite}.xml'
            result = subprocess.run([sys.executable, '-m', 'pytest', '-q', *files,
                                     f'--junitxml={report}'], check=False)
            checks[suite] = result.returncode == 0 and report.exists()
            if report.exists():
                cases = ElementTree.parse(report).findall('.//testcase')
                counts[suite] = len(cases)
                if suite == 'full_pytest':
                    for name, prefix in CRITERIA.items():
                        relevant = [c for c in cases if c.get('name', '').split('[')[0] == prefix]
                        evidence[name] = bool(relevant) and all(len(c) == 0 for c in relevant)
        demo = subprocess.run([sys.executable, '-m', 'orion.demo'], check=False,
                              capture_output=True, text=True)
        try:
            checks['demo'] = demo.returncode == 0 and json.loads(demo.stdout)['execution_allowed'] is False
        except (ValueError, KeyError):
            checks['demo'] = False
        checks['ruff'] = subprocess.run([sys.executable, '-m', 'ruff', 'check', '.'],
                                         check=False).returncode == 0
        files = tuple(p for area in ('src', 'tests', 'tools') for p in (root / area).rglob('*.py'))
        try:
            for path in files:
                compile(path.read_bytes(), str(path), 'exec')
            checks['compilation'] = True
        except (SyntaxError, OSError):
            checks['compilation'] = False
        counts['compiled_files'] = len(files)
        source = tuple(str(p.relative_to(root)) for p in files
                       if p.relative_to(root).parts[0] in ('src', 'tools'))
        checks['capability_scan'] = Orchestrator.source_capability_scan(root, source)
        counts['scanned_files'] = len(source)
        checks['diff'] = all(subprocess.run(['git', 'diff', *flags, '--check'],
                             check=False).returncode == 0 for flags in ((), ('--cached',)))
    passed = all(checks.values()) and len(evidence) == len(CRITERIA) and all(evidence.values())
    print(json.dumps({'SEMANTIC_DISCOVERY_GATE': 'PASS' if passed else 'FAIL',
                      'checks': checks, 'criteria': evidence, 'counts': counts,
                      'execution_allowed': False, 'live_pilot_ready': False}, sort_keys=True))
    return 0 if passed else 1


if __name__ == '__main__':
    raise SystemExit(run())
