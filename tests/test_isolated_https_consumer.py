"""A working HTTPS pipeline without confinement must still fail containment."""
import pytest
from isolated_broker_lab import run_case, run_integration


@pytest.mark.parametrize('protocol', ['local_rows_v1', 'local_columns_v1'])
def test_https_semantic_restart_without_isolation_cannot_pass(tmp_path, protocol):
    report = run_case(tmp_path / protocol, protocol, transport='https', confined=False)
    assert report['status'] == 'FAIL' and report['negative_controls'] is False
    assert report['https_requests'] == 1 and report['https_negative_controls'] is True
    assert all(report['pipeline_checks'].values())
    for check in ('https_profile_denied', 'https_private_key_denied', 'https_direct_tcp_denied', 'source_denied'):
        assert report['isolation_checks'][check] is False
    assert report['metadata_attempts_after_revocation'] == 3
    assert report['attempts_after_revocation'] == 1
    assert report['observations'] == 2 and report['unknown_count'] > 0
    assert not report['execution_allowed'] and not report['live_ready']


def test_real_https_namespace_probe_never_substitutes_unconfined_success():
    report = run_integration(transport='https')
    assert report['status'] in ('PASS', 'BLOCKED')
    assert report['production_containment'] == 'NOT PROVEN'
    assert not report['live_ready'] and not report['execution_allowed']
    if report['status'] == 'PASS':
        assert len(report['cases']) == 2
        for case in report['cases']:
            assert case['status'] == 'PASS' and case['https_negative_controls']
            assert case['https_requests'] == 1
            assert all(case['pipeline_checks'].values())
            assert all(case['isolation_checks'].values())


def test_kernel_prerequisite_failure_never_starts_https_source(monkeypatch):
    import isolated_broker_lab
    monkeypatch.setattr(isolated_broker_lab, 'run_lab', lambda: {'status': 'BLOCKED', 'reason': 'test_unavailable'})
    monkeypatch.setattr(isolated_broker_lab, 'run_case', lambda *a, **k: pytest.fail('source started without kernel prerequisite'))
    result = run_integration(transport='https')
    assert result['status'] == 'BLOCKED' and result['reason'] == 'kernel_prerequisite_test_unavailable'


def test_https_owner_rejects_forged_consumer_review(tmp_path, monkeypatch):
    import isolated_broker_lab
    original = isolated_broker_lab.relay
    def forged(*args, **kwargs):
        report, observations, trace = original(*args, **kwargs)
        report['state']['audit_id'] = 'forged'
        return report, observations, trace
    monkeypatch.setattr(isolated_broker_lab, 'relay', forged)
    with pytest.raises(AssertionError):
        run_case(tmp_path / 'forged', 'local_rows_v1', transport='https', confined=False)


def test_fixed_profiles_reject_arbitrary_selector():
    with pytest.raises(ValueError):
        run_integration(transport='https://unapproved.invalid')


def test_missing_https_custody_witness_rejects(tmp_path, monkeypatch):
    import isolated_broker_lab
    original = isolated_broker_lab.relay
    def incomplete(*args, **kwargs):
        report, observations, trace = original(*args, **kwargs)
        del report['isolation_checks']['https_private_key_denied']
        return report, observations, trace
    monkeypatch.setattr(isolated_broker_lab, 'relay', incomplete)
    with pytest.raises(AssertionError):
        run_case(tmp_path / 'missing', 'local_rows_v1', transport='https', confined=False)
