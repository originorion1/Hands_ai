"""Unconfined pipeline execution is explicit negative control, not containment proof."""
import pytest
from isolated_broker_lab import run_case, run_integration


@pytest.mark.parametrize('protocol', ['local_rows_v1', 'local_columns_v1'])
def test_real_broker_and_fresh_semantic_consumers_without_isolation_cannot_pass(tmp_path, protocol):
    report = run_case(tmp_path / protocol, protocol, confined=False)
    assert report['status'] == 'FAIL' and not report['negative_controls']
    assert all(report['pipeline_checks'].values())
    assert not report['isolation_checks']['source_denied']
    assert not report['isolation_checks']['configuration_denied']
    assert not report['isolation_checks']['journal_write_denied']
    assert report['observations'] == 2 and report['unknown_count'] > 0
    assert report['attempts_after_revocation'] == 1
    assert report['metadata_attempts_after_revocation'] == 3
    assert not report['isolation_checks']['metadata_source_denied']
    assert report['pipeline_checks']['brokered_discovery']
    assert report['pipeline_checks']['metadata_token_read_denied']
    assert not report['execution_allowed'] and not report['live_ready']


def test_actual_isolated_integration_preserves_release_denial():
    report = run_integration()
    assert report['status'] in ('PASS', 'BLOCKED')
    assert not report['execution_allowed'] and not report['live_ready']
    if report['status'] == 'PASS':
        assert len(report['cases']) == 2
        assert all(case['status'] == 'PASS' for case in report['cases'])


@pytest.mark.parametrize('data', [b'{"x":1,"x":2}\n', b'{}', b'"' + b'x' * 65536 + b'"\n'])
def test_consumer_pipe_rejects_duplicate_truncated_and_oversized_frames(tmp_path, data):
    from isolated_broker_lab import Frames
    path = tmp_path / 'frame'
    path.write_bytes(data)
    with path.open('rb') as stream, pytest.raises(ValueError):
        Frames(stream).read()


def test_owner_recomputes_instead_of_trusting_consumer_claim(tmp_path, monkeypatch):
    import isolated_broker_lab
    original = isolated_broker_lab.relay
    def forged(*args, **kwargs):
        report, observations, trace = original(*args, **kwargs)
        report['state']['audit_id'] = 'forged'
        return report, observations, trace
    monkeypatch.setattr(isolated_broker_lab, 'relay', forged)
    with pytest.raises(AssertionError):
        isolated_broker_lab.run_case(tmp_path / 'forged', 'local_rows_v1', confined=False)
