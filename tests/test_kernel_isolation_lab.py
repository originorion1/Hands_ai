"""Report validation is not OS isolation; the real-host probe reports its own capability."""
import json

import pytest
from isolation_lab import CHECKS, assess_child, run_lab


def test_actual_host_probe_never_enables_live_access():
    report = run_lab()
    assert report['status'] in ('PASS', 'BLOCKED')  # An exercised escape must fail this test.
    assert not report['live_ready'] and not report['execution_allowed']
    assert report['production_containment'] == 'NOT PROVEN'
    if report['status'] == 'PASS':
        assert report['negative_controls'] and all(report['checks'].values())


@pytest.mark.parametrize('check', CHECKS)
def test_each_failed_isolation_assertion_prevents_profile_pass(check):
    checks = dict.fromkeys(CHECKS, True)
    checks[check] = False
    assert assess_child(0, json.dumps(checks), '', negative_controls=True,
                        audit_unchanged=True)['status'] == 'FAIL'


@pytest.mark.parametrize('payload', ['{}', 'null', '[]', 'not json', '{"x":true,"x":true}',
                                     json.dumps(dict.fromkeys(CHECKS, 1))])
def test_missing_malformed_or_nonboolean_assertions_fail_closed(payload):
    assert assess_child(0, payload, '', negative_controls=True,
                        audit_unchanged=True)['status'] == 'FAIL'


@pytest.mark.parametrize('negative,audit', [(False, True), (True, False)])
def test_no_negative_control_or_changed_audit_prevents_pass(negative, audit):
    assert assess_child(0, json.dumps(dict.fromkeys(CHECKS, True)), '', negative_controls=negative,
                        audit_unchanged=audit)['status'] == 'FAIL'


def test_missing_kernel_capability_is_blocked_not_passed():
    assert assess_child(1, '', 'private details', negative_controls=True,
                        audit_unchanged=True)['status'] == 'BLOCKED'
