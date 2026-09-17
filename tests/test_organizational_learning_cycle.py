import json
import os
import subprocess
import sys
from copy import deepcopy
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
    select_operational_layout,
    select_prediction_question,
)
from orion.learning.prediction_ledger import PredictionLedger

START = datetime(2031, 1, 1, tzinfo=UTC)


class SyntheticOperationalReader:
    def __init__(self, source_id, provenance_source, resource, rows, now):
        self.source_id = source_id
        self.provenance_source = provenance_source
        self.resource = resource
        self.rows = rows
        self.now = now
        self.calls = 0

    def read(self, permit):
        request, _ = permit.claim_io(self.source_id)
        assert request.resource == self.resource
        self.calls += 1
        return tuple(Observation(Evidence(
            EvidenceKind.EXPERIMENT, self.provenance_source,
            {'resource': request.resource, 'record': row},
            observed_at=self.now(), tenant_id=request.tenant_id,
        )) for row in self.rows)


def _fresh_process_recovery(path, assessment, question):
    program = (
        'import json,sys; '
        'from orion.learning.organizational_cycle import recover_committed_measurement; '
        'print(json.dumps(recover_committed_measurement(sys.argv[1], tenant_id=sys.argv[2], '
        'target_definition=sys.argv[3], model_version=sys.argv[4]), sort_keys=True))'
    )
    result = subprocess.run(
        [sys.executable, '-c', program, str(path), assessment['tenant'],
         question.target_definition, BASELINE_MODEL],
        cwd=path.parent, env={'PATH': os.defpath}, check=True,
        capture_output=True, text=True, timeout=20,
    )
    return json.loads(result.stdout)


def acquisition(
    path, environment, assessment, state, phase, parts_by_case, seen, restart_evidence,
    *, reuse_source=False, wrong_date=False,
):
    layout = select_operational_layout(assessment)
    question = select_prediction_question(assessment)
    counter = {'value': 0}

    def acquire(queries):
        assert len(queries) == 1
        query = queries[0]
        ledger = PredictionLedger(path, clock=lambda: state['now'])
        baseline = ledger.score(
            assessment['tenant'], target_definition=question.target_definition,
            model_version=BASELINE_MODEL,
        )
        assert baseline['pending'] == 1
        if phase == 'evaluation':
            revised = ledger.score(
                assessment['tenant'], target_definition=question.target_definition,
                model_version=REVISED_MODEL,
            )
            assert revised['pending'] == 1
        if phase == 'development' and counter['value'] == 0:
            recovered = _fresh_process_recovery(path, assessment, question)
            assert recovered['pending'] == 1
            assert recovered['authority_restored'] is False
            assert recovered['acquisition_available'] is False
            restart_evidence.append(recovered)
        # Only after the committed-state process exits do we authorize source I/O.
        state['now'] = query.horizon_end
        source = 'synthetic://development' if reuse_source else f'synthetic://{phase}'
        authorization = f'operational-{phase}-v1'
        provenance = f'synthetic-{phase}-operational-record'
        occurred = query.horizon_end.date() + (timedelta(days=1) if wrong_date else timedelta())
        parts = parts_by_case[counter['value']]
        rows = tuple({
            environment.identity: f'{phase}-{counter["value"]}-{index}',
            environment.partition: assessment['company'],
            layout.value_field: value,
            layout.date_field: occurred.isoformat(),
        } for index, value in enumerate(parts, 1))
        fields = (environment.identity, environment.partition,
                  layout.value_field, layout.date_field)
        request = PilotRequest(
            assessment['tenant'], assessment['company'], source, layout.resource,
            fields, layout.date_field, occurred, occurred, len(rows),
        )
        window = ReviewedReadWindow(
            assessment['tenant'], assessment['company'], layout.resource, fields,
            layout.date_field, occurred, occurred, state['now'] + timedelta(hours=1),
        )
        grant = PilotAuthorization(
            authorization, source, window, environment.identity, environment.partition,
            provenance, EvidenceKind.EXPERIMENT, len(rows),
        )
        reader = SyntheticOperationalReader(
            source, provenance, layout.resource, rows, lambda: state['now'])
        result = launch_pilot_read(
            request, authorization_id=authorization,
            lookup=lambda key: grant if key == authorization else None,
            adapter=reader, clock=lambda: state['now'],
        )
        assert reader.calls == 1
        seen.append((phase, query.case_id, query.cohort))
        counter['value'] += 1
        return result

    return acquire


def environments():
    development = Restaurant().populate()
    evaluation = Restaurant(
        seed=73, reorder=True, source='https://unfamiliar-restaurant.test',
        structural_variant=True,
    ).populate()
    return development, evaluation


def test_complete_cycle_normalizes_records_and_recovers_in_fresh_process(tmp_path):
    development, evaluation = environments()
    assessment, evaluation_assessment = development.assessment(), evaluation.assessment()
    state = {'now': START}
    seen, restart_evidence = [], []
    ledger_path = tmp_path / 'predictions.db'
    result = run_learning_cycle(
        assessment, evaluation_assessment, ledger_path=ledger_path,
        queue_path=tmp_path / 'events.db',
        acquire_development=acquisition(
            ledger_path, development, assessment, state, 'development',
            ((70, 80), (60, 70)), seen, restart_evidence),
        acquire_evaluation=acquisition(
            ledger_path, evaluation, evaluation_assessment, state, 'evaluation',
            ((40, 60), (75, 65)), seen, restart_evidence),
        clock=lambda: state['now'],
    )

    assert [item[:2] for item in seen] == [
        ('development', 'development-1'), ('development', 'development-2'),
        ('evaluation', 'evaluation-1'), ('evaluation', 'evaluation-2'),
    ]
    assert len({_cohort.window_start for _, _, _cohort in seen}) == 4
    assert all(cohort.entity_id == assessment['tenant'] for _, _, cohort in seen)
    assert all(cohort.location_id == assessment['company'] for _, _, cohort in seen)
    assert restart_evidence[0]['predictions'] == restart_evidence[0]['pending'] == 1
    assert result['status'] == 'MEASURED'
    assert result['relationship']['status'] == 'VALIDATED_SEMANTIC_ROLE'
    assert result['question']['source_finding_was_prospective'] is False
    assert result['question']['unit'] == 'USD'
    assert '-unit-USD-' in result['question']['target_definition']
    assert result['development']['actuals'] == (True, True)
    assert [item['normalized_total'] for item in
            result['development']['operational_outcomes']] == ['150', '130']
    assert [item['normalized_total'] for item in
            result['evaluation']['operational_outcomes']] == ['100', '140']
    assert all(len(item['record_provenance']) == 2 for item in
               (*result['development']['operational_outcomes'],
                *result['evaluation']['operational_outcomes']))
    assert all(item['completeness_limitations'] for item in
               result['evaluation']['operational_outcomes'])
    assert result['revision']['prior_probability'] == 0.5
    assert result['revision']['revised_probability'] == 0.75
    assert result['evaluation']['cases'] == 2
    assert result['evaluation']['prior_brier'] == pytest.approx(0.25)
    assert result['evaluation']['revised_brier'] == pytest.approx(0.3125)
    assert result['learning_cycle_integration'] is True
    assert result['unfamiliar_environment_evaluation']['works'] is True
    assert result['unfamiliar_environment_evaluation']['topologies_differ'] is True
    assert result['unfamiliar_environment_evaluation'][
        'separate_access_grants_are_independence_proof'] is False
    assert result['predictive_improvement'] is False
    assert result['failed_prediction_cases'] == ('evaluation-1',)
    assert result['durable_completed_state_reopen']['process_restart_claimed'] is False
    assert 'fresh_process_recovery' not in result
    assert result['shared_fixture_engine_authorship'] is True
    assert result['synthetic_generalization_proven'] is False
    assert result['deployment_qualification'] == 'UNRESOLVED'
    assert result['execution_allowed'] is False
    assert result['allow_live_customer_access'] is False
    assert result['LIVE_PILOT_READY'] is False
    json.dumps(result, sort_keys=True)
    report = owner_report(result)
    assert 'Learning-cycle integration: true' in report
    assert 'Unfamiliar-environment evaluation: true' in report
    assert 'Predictive improvement: false' in report


def test_unknown_and_unit_mismatch_stop_before_acquisition(tmp_path):
    development, evaluation = environments()
    assessment, evaluation_assessment = development.assessment(), evaluation.assessment()

    def forbidden(_queries):
        pytest.fail('UNKNOWN question reached acquisition')

    for changed in (
        {**assessment, 'relationships': []},
        deepcopy(assessment),
    ):
        if changed.get('relationships'):
            finding = next(item for item in changed['findings']
                           if item['code'] == 'next_day_sample_sales')
            finding['unit'] = 'EUR'
            assert select_prediction_question(changed).status == 'UNKNOWN'
        result = run_learning_cycle(
            changed, evaluation_assessment, ledger_path=tmp_path / 'predictions.db',
            queue_path=tmp_path / 'events.db', acquire_development=forbidden,
            acquire_evaluation=forbidden, clock=lambda: START,
        )
        assert result['status'] == 'UNKNOWN'
        assert result['predictive_improvement'] is None
        assert result['execution_allowed'] is False
    assert not (tmp_path / 'predictions.db').exists()


def test_evaluation_requires_structural_difference_not_only_access_grants(tmp_path):
    environment = Restaurant().populate()
    assessment = environment.assessment()

    def forbidden(_queries):
        pytest.fail('same structure reached acquisition')

    with pytest.raises(ValueError, match='structurally different'):
        run_learning_cycle(
            assessment, assessment, ledger_path=tmp_path / 'predictions.db',
            queue_path=tmp_path / 'events.db', acquire_development=forbidden,
            acquire_evaluation=forbidden, clock=lambda: START,
        )


def test_development_and_evaluation_cannot_share_operational_source(tmp_path):
    development, evaluation = environments()
    assessment, evaluation_assessment = development.assessment(), evaluation.assessment()
    state = {'now': START}
    ledger_path = tmp_path / 'predictions.db'
    with pytest.raises(ValueError, match='distinct authorizations and sources'):
        run_learning_cycle(
            assessment, evaluation_assessment, ledger_path=ledger_path,
            queue_path=tmp_path / 'events.db',
            acquire_development=acquisition(
                ledger_path, development, assessment, state, 'development',
                ((70, 80), (60, 70)), [], []),
            acquire_evaluation=acquisition(
                ledger_path, evaluation, evaluation_assessment, state, 'evaluation',
                ((40, 60), (75, 65)), [], [], reuse_source=True),
            clock=lambda: state['now'],
        )


def test_operational_record_outside_committed_cohort_is_rejected(tmp_path):
    development, evaluation = environments()
    assessment, evaluation_assessment = development.assessment(), evaluation.assessment()
    state = {'now': START}
    ledger_path = tmp_path / 'predictions.db'
    with pytest.raises(ValueError, match='committed business cohort'):
        run_learning_cycle(
            assessment, evaluation_assessment, ledger_path=ledger_path,
            queue_path=tmp_path / 'events.db',
            acquire_development=acquisition(
                ledger_path, development, assessment, state, 'development',
                ((70, 80), (60, 70)), [], [], wrong_date=True),
            acquire_evaluation=acquisition(
                ledger_path, evaluation, evaluation_assessment, state, 'evaluation',
                ((40, 60), (75, 65)), [], []),
            clock=lambda: state['now'],
        )
