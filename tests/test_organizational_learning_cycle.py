import json
import os
import sqlite3
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

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
    begin_learning_cycle,
    owner_report,
    recover_interrupted_learning_cycle,
    resume_learning_cycle,
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


def acquisition(
    path, environment, assessment, state, phase, parts_by_case, seen, source_counts,
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
        state['now'] = query.horizon_end
        source = 'synthetic://development' if reuse_source else f'synthetic://{phase}'
        provenance = f'synthetic-{phase}-operational-record'
        occurred = query.horizon_end.date() + (timedelta(days=1) if wrong_date else timedelta())
        parts = parts_by_case[counter['value']]
        batches = []
        if layout.representation == 'flat':
            rows = tuple({
                environment.identity: f'{phase}-{counter["value"]}-{index}',
                environment.partition: assessment['company'],
                layout.value_field: value, layout.date_field: occurred.isoformat(),
            } for index, value in enumerate(parts, 1))
            batches.append(('flat', layout.value_resource, layout.date_field, rows))
        else:
            header_id = f'{phase}-header-{counter["value"]}'
            header_rows = ({
                environment.identity: header_id,
                environment.partition: assessment['company'],
                layout.date_field: occurred.isoformat(),
            },)
            detail_index = environment.resources.index(layout.value_resource)
            admission_date = environment.columns[detail_index][
                environment.kinds[detail_index].index('Date')]
            detail_rows = tuple({
                environment.identity: f'{phase}-detail-{counter["value"]}-{index}',
                environment.partition: assessment['company'],
                layout.value_field: value, layout.relationship_field: header_id,
                admission_date: occurred.isoformat(),
            } for index, value in enumerate(parts, 1))
            batches.extend((('header', layout.date_resource, layout.date_field, header_rows),
                            ('detail', layout.value_resource, admission_date, detail_rows)))
        result = []
        for label, resource, date_field, rows in batches:
            fields = tuple(rows[0])
            authorization = f'operational-{phase}-{label}-v1'
            request = PilotRequest(
                assessment['tenant'], assessment['company'], source, resource,
                fields, date_field, occurred, occurred, len(rows),
            )
            window = ReviewedReadWindow(
                assessment['tenant'], assessment['company'], resource, fields,
                date_field, occurred, occurred, state['now'] + timedelta(hours=1),
            )
            grant = PilotAuthorization(
                authorization, source, window, environment.identity, environment.partition,
                provenance, EvidenceKind.EXPERIMENT, len(rows),
            )
            reader = SyntheticOperationalReader(
                source, provenance, resource, rows, lambda: state['now'])
            result.extend(launch_pilot_read(
                request, authorization_id=authorization,
                lookup=lambda key, expected=authorization, current=grant:
                    current if key == expected else None,
                adapter=reader, clock=lambda: state['now'],
            ))
            source_counts.append(reader.calls)
            assert reader.calls == 1
        seen.append((phase, query.case_id, query.cohort))
        counter['value'] += 1
        return tuple(result)

    return acquire


def environments():
    development = Restaurant().populate()
    evaluation = Restaurant(
        seed=73, reorder=True, source='https://unfamiliar-restaurant.test',
        header_detail=True,
    ).populate()
    return development, evaluation


def _process_main(arguments):
    mode, ledger_name, queue_name, *scope = arguments
    ledger_path, queue_path = Path(ledger_name), Path(queue_name)
    if mode == 'start':
        development, evaluation = environments()
        receipt = begin_learning_cycle(
            development.assessment(), evaluation.assessment(), ledger_path=ledger_path,
            clock=lambda: START,
        )
        print(json.dumps(receipt, sort_keys=True))
        return
    tenant_id, cycle_id = scope
    recovered = recover_interrupted_learning_cycle(
        ledger_path, tenant_id=tenant_id, cycle_id=cycle_id)
    if mode == 'unauthorized':
        try:
            resume_learning_cycle(
                ledger_path=ledger_path, queue_path=queue_path,
                tenant_id=tenant_id, cycle_id=cycle_id,
                acquire_development=None, acquire_evaluation=None,
                clock=lambda: START,
            )
        except PermissionError as error:
            print(json.dumps({
                'status': 'DENIED', 'reason': str(error), 'source_io_count': 0,
                'recovered': recovered,
            }, sort_keys=True))
            return
        raise AssertionError('unauthorized continuation was not denied')
    if mode != 'authorized':
        raise ValueError('unknown process mode')
    development, evaluation = environments()
    assessment, evaluation_assessment = development.assessment(), evaluation.assessment()
    state = {'now': datetime.fromisoformat(recovered['cohort']['window_start'])}
    seen, source_counts = [], []
    result = resume_learning_cycle(
        ledger_path=ledger_path, queue_path=queue_path,
        tenant_id=tenant_id, cycle_id=cycle_id,
        acquire_development=acquisition(
            ledger_path, development, assessment, state, 'development',
            ((70, 80), (60, 70)), seen, source_counts),
        acquire_evaluation=acquisition(
            ledger_path, evaluation, evaluation_assessment, state, 'evaluation',
            ((40, 60), (75, 65)), seen, source_counts),
        clock=lambda: state['now'],
    )
    print(json.dumps({
        'status': 'CONTINUED', 'source_io_count': sum(source_counts),
        'seen': tuple((phase, case) for phase, case, _ in seen), 'result': result,
    }, sort_keys=True))


def _run_process(mode, ledger_path, queue_path, *scope):
    return subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), mode, str(ledger_path),
         str(queue_path), *scope],
        cwd=ledger_path.parent, env={'PATH': os.defpath}, capture_output=True,
        text=True, timeout=90, check=True,
    )


def test_complete_cycle_normalizes_related_records_after_interrupted_process(tmp_path):
    ledger_path, queue_path = tmp_path / 'predictions.db', tmp_path / 'events.db'
    process_a = subprocess.Popen(
        [sys.executable, str(Path(__file__).resolve()), 'start', str(ledger_path),
         str(queue_path)],
        cwd=tmp_path, env={'PATH': os.defpath}, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, text=True,
    )
    process_a_pid = process_a.pid
    stdout, stderr = process_a.communicate(timeout=90)
    assert process_a.returncode == 0, stderr
    assert process_a.poll() == 0
    assert not Path(f'/proc/{process_a_pid}').exists()
    started = json.loads(stdout)
    with sqlite3.connect(ledger_path) as database:
        checkpoint_payload = json.loads(database.execute(
            'SELECT payload FROM learning_cycle_checkpoints').fetchone()[0])
    persisted_plan = checkpoint_payload['plan_json'].lower()
    assert all(value not in persisted_plan for value in (
        'authorization', 'credential', 'api_key', 'secret', 'callable'))

    unauthorized = json.loads(_run_process(
        'unauthorized', ledger_path, queue_path,
        started['tenant_id'], started['cycle_id']).stdout)
    assert unauthorized['status'] == 'DENIED'
    assert unauthorized['source_io_count'] == 0
    assert not queue_path.exists()
    assert unauthorized['recovered']['prediction_id'] == started['prediction_id']
    assert unauthorized['recovered']['evidence_ids'] == started['evidence_ids']
    assert unauthorized['recovered']['cohort'] == started['cohort']
    assert unauthorized['recovered']['unit'] == started['unit']
    assert unauthorized['recovered']['horizon_end'] == started['cohort']['window_end']

    continued = json.loads(_run_process(
        'authorized', ledger_path, queue_path,
        started['tenant_id'], started['cycle_id']).stdout)
    assert continued['status'] == 'CONTINUED'
    assert continued['source_io_count'] == 6
    assert continued['seen'] == [
        ['development', 'development-1'], ['development', 'development-2'],
        ['evaluation', 'evaluation-1'], ['evaluation', 'evaluation-2'],
    ]
    result = continued['result']
    assert result['status'] == 'MEASURED'
    assert result['relationship']['status'] == 'VALIDATED_SEMANTIC_ROLE'
    assert result['question']['source_finding_was_prospective'] is False
    assert result['question']['unit'] == 'USD'
    assert '-unit-USD-' in result['question']['target_definition']
    assert result['development']['actuals'] == [True, True]
    assert [item['normalized_total'] for item in
            result['development']['operational_outcomes']] == ['150', '130']
    assert [item['normalized_total'] for item in
            result['evaluation']['operational_outcomes']] == ['100', '140']
    assert all(len(item['record_provenance']) == 2 for item in
               (*result['development']['operational_outcomes'],
                *result['evaluation']['operational_outcomes']))
    assert all('related_evidence_id' in record
               for item in result['evaluation']['operational_outcomes']
               for record in item['record_provenance'])
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
    assert result['unfamiliar_environment_evaluation']['representation_difference'] == {
        'development': 'flat', 'evaluation': 'related_header_detail',
        'relationship_role': 'sales_header',
    }
    assert result['unfamiliar_environment_evaluation']['irrelevant_field_only'] is False
    assert result['unfamiliar_environment_evaluation'][
        'separate_access_grants_are_independence_proof'] is False
    assert result['predictive_improvement'] is False
    assert result['failed_prediction_cases'] == ['evaluation-1']
    assert result['interrupted_cycle_recovery']['prediction_id'] == started['prediction_id']
    assert result['interrupted_cycle_recovery']['resumed_from_checkpoint'] is True
    assert result['interrupted_cycle_recovery']['authority_restored'] is False
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
    with sqlite3.connect(ledger_path) as database:
        original = database.execute('''
            SELECT p.payload, o.payload FROM predictions p
            JOIN prediction_outcomes o ON p.tenant=o.tenant AND p.identity=o.identity
            WHERE p.identity=?
        ''', (started['prediction_id'],)).fetchone()
    assert original is not None
    assert json.loads(original[0])['evidence_ids'] == started['evidence_ids']
    assert json.loads(original[1])['actual'] is True
    revisions = PredictionLedger(ledger_path).revisions(started['tenant_id'])
    assert len(revisions) == 1
    assert revisions[0].revision_id == result['revision']['revision_id']


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


def test_irrelevant_field_is_not_a_meaningful_representation_difference(tmp_path):
    development = Restaurant().populate()
    evaluation = Restaurant(
        seed=73, source='https://irrelevant-field.test', structural_variant=True,
    ).populate()

    def forbidden(_queries):
        pytest.fail('same structure reached acquisition')

    with pytest.raises(ValueError, match='materially different operational representation'):
        run_learning_cycle(
            development.assessment(), evaluation.assessment(),
            ledger_path=tmp_path / 'predictions.db',
            queue_path=tmp_path / 'events.db', acquire_development=forbidden,
            acquire_evaluation=forbidden, clock=lambda: START,
        )


def test_missing_or_ambiguous_header_relationship_stays_unknown(tmp_path):
    development = Restaurant().populate().assessment()

    def forbidden(_queries):
        pytest.fail('unsupported join reached acquisition')

    evaluations = (
        Restaurant(seed=73, source='https://missing-header.test', header_detail=True)
        .populate(omit=((1, 'sales_header'),)).assessment(),
        Restaurant(seed=73, source='https://ambiguous-header.test', header_detail=True,
                   ambiguous_header=True).populate().assessment(),
    )
    for index, evaluation in enumerate(evaluations):
        assert select_prediction_question(evaluation).status == 'UNKNOWN'
        result = run_learning_cycle(
            development, evaluation, ledger_path=tmp_path / f'predictions-{index}.db',
            queue_path=tmp_path / f'events-{index}.db', acquire_development=forbidden,
            acquire_evaluation=forbidden, clock=lambda: START,
        )
        assert result['status'] == 'UNKNOWN'
        assert not (tmp_path / f'predictions-{index}.db').exists()


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


if __name__ == '__main__':
    _process_main(sys.argv[1:])
