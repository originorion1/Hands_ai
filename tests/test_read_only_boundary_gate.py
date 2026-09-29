from xml.etree.ElementTree import Element, SubElement

import pytest

from orion.pilot.boundary_report import REQUIREMENTS, boundary_report


def test_green_synthetic_witnesses_cannot_waive_missing_isolation():
    cases = [Element('testcase', name=name) for name, _ in REQUIREMENTS.values() if name]
    report = boundary_report(cases, {'full_test_suite': True, 'capability_scan': True})
    assert len(report['requirements']) == 25
    assert report['READ_ONLY_PILOT_BOUNDARY'] == 'READ_ONLY_PILOT_BOUNDARY_NOT_READY'
    assert not report['LIVE_PILOT_READY'] and not report['execution_allowed']
    states = {r['requirement']: r['status'] for r in report['requirements']}
    assert states['compromised_reasoner_containment'] == 'FAIL'
    assert states['second_protocol'] == states['secret_isolation'] == 'BLOCKED'


@pytest.mark.parametrize('outcome, expected', [('skipped', 'NOT PROVEN'), ('failure', 'FAIL'),
                                              ('error', 'FAIL')])
def test_skipped_failed_or_missing_witness_never_passes(outcome, expected):
    name = REQUIREMENTS['bounded_request'][0]
    passed = Element('testcase', name=name + '[one]')
    rejected = Element('testcase', name=name + '[two]')
    SubElement(rejected, outcome)
    report = boundary_report([passed, rejected], {'full_test_suite': True})
    states = {r['requirement']: r['status'] for r in report['requirements']}
    assert states['bounded_request'] == expected
    assert states['tenant_isolation'] == 'NOT PROVEN'
