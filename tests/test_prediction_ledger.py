from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from orion.learning.prediction_ledger import Outcome, Prediction, PredictionLedger

NOW = datetime(2026, 1, 1, tzinfo=UTC)
REF = UUID('00000000-0000-4000-8000-000000000001')


def prediction(identity='p1', tenant='tenant-a', probability=0.8):
    return Prediction(tenant, identity, 'stockout-within-24h-v1', 'baseline-v1',
                      NOW, NOW, NOW + timedelta(days=1), probability, (REF,))


def test_prospective_scoring_restart_and_idempotence(tmp_path):
    path = tmp_path / 'ledger.db'
    ledger = PredictionLedger(path, clock=lambda: NOW)
    ledger.record(prediction())
    ledger.record(prediction())
    ledger.record(prediction('p2', probability=0.2))
    ledger.record(prediction('pending'))
    later = NOW + timedelta(days=1)
    ledger = PredictionLedger(path, clock=lambda: later)
    for identity, actual in [('p1', True), ('p2', False)]:
        outcome = Outcome('tenant-a', identity, later, actual, (REF,))
        ledger.resolve(outcome)
        ledger.resolve(outcome)
    report = ledger.score('tenant-a')
    assert report['predictions'] == 3
    assert report['resolved'] == 2 and report['pending'] == 1
    assert report['brier'] == pytest.approx(0.04)
    assert report['true_positive'] == report['true_negative'] == 1
    assert report['false_positive'] == report['false_negative'] == 0
    assert report['execution_allowed'] is False
    assert report['economic_value'] is None
    assert ledger.score('tenant-b')['resolved'] == 0


def test_rejects_backfill_conflicting_replay_and_cross_tenant(tmp_path):
    ledger = PredictionLedger(tmp_path / 'ledger.db', clock=lambda: NOW)
    ledger.record(prediction())
    with pytest.raises(ValueError, match='conflict'):
        ledger.record(prediction(probability=0.3))
    with pytest.raises(ValueError, match='horizon'):
        ledger.resolve(Outcome('tenant-a', 'p1', NOW, True, (REF,)))
    with pytest.raises(ValueError, match='unknown'):
        ledger.resolve(Outcome('tenant-b', 'p1', NOW, True, (REF,)))
    later = PredictionLedger(tmp_path / 'ledger.db', clock=lambda: NOW + timedelta(days=2))
    with pytest.raises(ValueError, match='prospective'):
        later.record(prediction('late'))
    later.resolve(Outcome('tenant-a', 'p1', NOW + timedelta(days=1), True, (REF,)))
    with pytest.raises(ValueError, match='conflict'):
        later.resolve(Outcome('tenant-a', 'p1', NOW + timedelta(days=1), False, (REF,)))


@pytest.mark.parametrize('probability', [float('nan'), float('inf'), -0.1, 1.1, True])
def test_invalid_probabilities_fail_closed(probability):
    with pytest.raises(ValueError):
        prediction(probability=probability)


def test_evidence_cutoff_and_immutable_references():
    with pytest.raises(ValueError):
        Prediction('a', 'p', 'target', 'model', NOW, NOW + timedelta(seconds=1),
                   NOW + timedelta(days=1), 0.5, (REF,))
    with pytest.raises(ValueError):
        Prediction('a', 'p', 'target', 'model', NOW, NOW,
                   NOW + timedelta(days=1), 0.5, [])


def test_future_outcomes_and_delayed_insertion_lead_time(tmp_path):
    ledger = PredictionLedger(tmp_path / 'ledger.db', clock=lambda: NOW + timedelta(hours=2))
    ledger.record(prediction())
    assert ledger.score('tenant-a')['brier'] is None
    with pytest.raises(ValueError, match='future'):
        ledger.resolve(Outcome('tenant-a', 'p1', NOW + timedelta(days=1), True, (REF,)))
    later = PredictionLedger(tmp_path / 'ledger.db', clock=lambda: NOW + timedelta(days=1))
    later.resolve(Outcome('tenant-a', 'p1', NOW + timedelta(days=1), False, (REF,)))
    assert later.score('tenant-a')['lead_seconds'] == (22 * 3600,)
    assert later.score('tenant-a')['false_positive'] == 1


def test_same_prediction_identity_in_two_tenants_is_independent(tmp_path):
    ledger = PredictionLedger(tmp_path / 'ledger.db', clock=lambda: NOW)
    ledger.record(prediction(tenant='tenant-a'))
    ledger.record(prediction(tenant='tenant-b', probability=0.1))
    later = PredictionLedger(tmp_path / 'ledger.db', clock=lambda: NOW + timedelta(days=1))
    later.resolve(Outcome('tenant-b', 'p1', NOW + timedelta(days=1), True, (REF,)))
    assert later.score('tenant-a')['pending'] == 1
    assert later.score('tenant-b')['false_negative'] == 1


def test_offline_demonstration_is_repeatable_and_reports_all_outcomes():
    from orion.learning.prediction_ledger import synthetic_demo

    report = synthetic_demo()
    assert report == synthetic_demo()
    assert report['data_source'] == 'synthetic-fixture'
    assert report['persistence_reopened'] is True
    assert report['predictions'] == 5
    assert report['resolved'] == 4
    assert report['pending'] == 1
    assert report['brier'] == pytest.approx(0.34)
    for name in ('true_positive', 'false_positive', 'true_negative', 'false_negative'):
        assert report[name] == 1
    assert report['lead_seconds'] == (86400.0,) * 4
    assert report['economic_value'] is None
    assert report['execution_allowed'] is False


def test_cohorts_are_never_silently_pooled(tmp_path):
    from dataclasses import replace

    ledger = PredictionLedger(tmp_path / 'ledger.db', clock=lambda: NOW)
    first = prediction()
    ledger.record(first)
    ledger.record(replace(first, prediction_id='other-model', model_version='candidate-v1',
                          probability=0.2))
    ledger.record(replace(first, prediction_id='other-target', target_definition='different-v1'))
    ledger = PredictionLedger(tmp_path / 'ledger.db', clock=lambda: NOW + timedelta(days=1))
    ledger.resolve(Outcome('tenant-a', 'p1', NOW + timedelta(days=1), True, (REF,)))
    with pytest.raises(ValueError, match='cohort'):
        ledger.score('tenant-a')
    report = ledger.score('tenant-a', target_definition=first.target_definition,
                          model_version=first.model_version)
    assert report['predictions'] == report['resolved'] == 1
    assert report['brier'] == pytest.approx(0.04)
    pending = ledger.score('tenant-a', target_definition=first.target_definition,
                           model_version='candidate-v1')
    assert pending['pending'] == 1 and pending['brier'] is None
    for tenant, target in [('tenant-b', first.target_definition), ('tenant-a', 'absent-v1')]:
        empty = ledger.score(tenant, target_definition=target, model_version=first.model_version)
        assert empty['predictions'] == 0 and empty['brier'] is None


@pytest.mark.parametrize('filters', [
    {'model_version': 'm'}, {'target_definition': 't'},
    {'target_definition': '', 'model_version': 'm'},
])
def test_cohort_filters_must_be_complete_and_valid(tmp_path, filters):
    ledger = PredictionLedger(tmp_path / 'ledger.db', clock=lambda: NOW)
    with pytest.raises(ValueError):
        ledger.score('tenant-a', **filters)
