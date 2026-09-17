import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from fnb_lab import Restaurant

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.discovery.pilot_read import (
    PilotAuthorization,
    PilotRequest,
    launch_pilot_read,
)
from orion.discovery.read_window import ReviewedReadWindow
from orion.learning.organizational_cycle import (
    BASELINE_MODEL,
    REVISED_MODEL,
    owner_report,
    run_learning_cycle,
    select_prediction_question,
)
from orion.learning.prediction_ledger import PredictionLedger

START = datetime(2031, 1, 1, tzinfo=UTC)
FIELDS = ('case_id', 'company', 'observed_on', 'target_definition', 'unit', 'value')


class SyntheticOutcomeReader:
    def __init__(self, source_id, provenance_source, rows, now):
        self.source_id = source_id
        self.provenance_source = provenance_source
        self.rows = rows
        self.now = now
        self.calls = 0

    def read(self, permit):
        request, _ = permit.claim_io(self.source_id)
        self.calls += 1
        return tuple(Observation(Evidence(
            EvidenceKind.EXPERIMENT, self.provenance_source,
            {'resource': request.resource, 'record': row},
            observed_at=self.now(), tenant_id=request.tenant_id,
        )) for row in self.rows)


def acquisition(path, assessment, state, phase, values, seen, *, reuse_source=False):
    def acquire(queries):
        ledger = PredictionLedger(path, clock=lambda: state['now'])
        question = select_prediction_question(assessment)
        baseline = ledger.score(
            assessment['tenant'], target_definition=question.target_definition,
            model_version=BASELINE_MODEL,
        )
        if phase == 'development':
            assert baseline['predictions'] == baseline['pending'] == 2
        else:
            revised = ledger.score(
                assessment['tenant'], target_definition=question.target_definition,
                model_version=REVISED_MODEL,
            )
            assert baseline['pending'] == revised['pending'] == 2
            assert len(ledger.revisions(assessment['tenant'])) == 1
        # The only transport-capable operation occurs after the assertions above.
        state['now'] = queries[0].horizon_end
        source = 'synthetic://development' if reuse_source else f'synthetic://{phase}'
        authorization = f'outcome-{phase}-v1'
        provenance = f'synthetic-{phase}-outcome'
        rows = tuple({
            'case_id': query.case_id, 'company': assessment['company'],
            'observed_on': query.horizon_end.date().isoformat(),
            'target_definition': query.target_definition, 'unit': query.unit,
            'value': value,
        } for query, value in zip(queries, values, strict=True))
        request = PilotRequest(
            assessment['tenant'], assessment['company'], source, 'SyntheticOutcome',
            FIELDS, 'observed_on', queries[0].horizon_end.date(),
            queries[0].horizon_end.date(), len(rows),
        )
        window = ReviewedReadWindow(
            assessment['tenant'], assessment['company'], 'SyntheticOutcome', FIELDS,
            'observed_on', request.start, request.end, state['now'] + timedelta(hours=1),
        )
        grant = PilotAuthorization(
            authorization, source, window, 'case_id', 'company', provenance,
            EvidenceKind.EXPERIMENT, len(rows),
        )
        reader = SyntheticOutcomeReader(source, provenance, rows, lambda: state['now'])
        result = launch_pilot_read(
            request, authorization_id=authorization,
            lookup=lambda key: grant if key == authorization else None,
            adapter=reader, clock=lambda: state['now'],
        )
        assert reader.calls == 1
        seen.append((phase, tuple(query.case_id for query in queries)))
        return result
    return acquire


def test_complete_prospective_learning_cycle_uses_admitted_later_outcomes(tmp_path):
    assessment = Restaurant().populate().assessment()
    state = {'now': START}
    seen = []
    ledger_path = tmp_path / 'predictions.db'
    result = run_learning_cycle(
        assessment, ledger_path=ledger_path, queue_path=tmp_path / 'events.db',
        acquire_development=acquisition(
            ledger_path, assessment, state, 'development', (150, 130), seen),
        acquire_evaluation=acquisition(
            ledger_path, assessment, state, 'evaluation', (100, 140), seen),
        clock=lambda: state['now'],
    )

    assert seen == [
        ('development', ('development-1', 'development-2')),
        ('evaluation', ('evaluation-1', 'evaluation-2')),
    ]
    assert result['status'] == 'MEASURED'
    assert result['relationship']['status'] == 'VALIDATED_SEMANTIC_ROLE'
    assert result['question']['source_finding_was_prospective'] is False
    assert result['development']['actuals'] == (True, True)
    assert result['revision']['prior_probability'] == 0.5
    assert result['revision']['revised_probability'] == 0.75
    assert result['evaluation']['cases'] == 2
    assert result['evaluation']['prior_brier'] == pytest.approx(0.25)
    assert result['evaluation']['revised_brier'] == pytest.approx(0.3125)
    assert result['learning_loop_works'] is True
    assert result['prediction_improved'] is False
    assert result['failed_prediction_cases'] == ('evaluation-1',)
    assert result['discovery_contradictions'] == tuple(assessment['contradictions'])
    assert result['fresh_process_recovery']['comparison'] == {
        key: value for key, value in result['evaluation'].items()
        if key not in {'authorization_domains', 'worker'}
    }
    assert result['fresh_process_recovery']['authority_restored'] is False
    assert result['shared_fixture_engine_authorship'] is True
    assert result['synthetic_generalization_proven'] is False
    assert result['deployment_qualification'] == 'UNRESOLVED'
    assert result['execution_allowed'] is False
    assert result['allow_live_customer_access'] is False
    assert result['LIVE_PILOT_READY'] is False
    json.dumps(result, sort_keys=True)
    report = owner_report(result)
    assert 'Learning loop works: true' in report
    assert 'Prediction improved on later unseen outcomes: false' in report
    assert 'does not prove real-organization generalization' in report


def test_insufficient_evidence_stays_unknown_without_acquisition_or_commitment(tmp_path):
    assessment = Restaurant().populate().assessment()
    assessment = {**assessment, 'relationships': []}

    def forbidden(_queries):
        pytest.fail('UNKNOWN question reached acquisition')

    result = run_learning_cycle(
        assessment, ledger_path=tmp_path / 'predictions.db',
        queue_path=tmp_path / 'events.db', acquire_development=forbidden,
        acquire_evaluation=forbidden, clock=lambda: START,
    )
    assert result['status'] == 'UNKNOWN'
    assert result['prediction_improved'] is None
    assert result['execution_allowed'] is False
    assert not (tmp_path / 'predictions.db').exists()


def test_development_and_evaluation_cannot_share_authority_or_source(tmp_path):
    assessment = Restaurant().populate().assessment()
    state = {'now': START}
    ledger_path = tmp_path / 'predictions.db'
    with pytest.raises(ValueError, match='distinct authorizations and sources'):
        run_learning_cycle(
            assessment, ledger_path=ledger_path, queue_path=tmp_path / 'events.db',
            acquire_development=acquisition(
                ledger_path, assessment, state, 'development', (150, 130), []),
            acquire_evaluation=acquisition(
                ledger_path, assessment, state, 'evaluation', (100, 140), [],
                reuse_source=True),
            clock=lambda: state['now'],
        )
    ledger = PredictionLedger(ledger_path, clock=lambda: state['now'])
    question = select_prediction_question(assessment)
    assert ledger.score(
        assessment['tenant'], target_definition=question.target_definition,
        model_version=REVISED_MODEL,
    )['resolved'] == 0


def test_question_selection_does_not_promote_historical_estimate_to_prospective():
    assessment = Restaurant().populate().assessment()
    finding = next(item for item in assessment['findings']
                   if item['code'] == 'next_day_sample_sales')
    assert finding['prediction']['target_date'] < finding['prediction']['evidence_as_of'][:10]
    question = select_prediction_question(assessment)
    assert question.status == 'SUPPORTED'
    assert question.threshold == finding['value']
    altered = replace(question, status='UNKNOWN')
    assert altered.status == 'UNKNOWN'
