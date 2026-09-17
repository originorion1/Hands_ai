"""One bounded, evidence-backed organizational learning cycle.

This composes governed discovery with the canonical prediction ledger and
outcome-event handler.  It acquires no data itself and grants no authority.
"""

from __future__ import annotations

import hashlib
from collections.abc import Callable, Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path
from uuid import UUID

from ..contracts import EvidenceKind, Observation, ObservationMode
from ..events import (
    EnterpriseEvent,
    EnterpriseEventKind,
    EventWorkerBounds,
    SQLiteEventQueue,
    route_enterprise_event,
    run_event_worker,
)
from .event_outcome import OutcomeEventHandler, OutcomeReceipt
from .prediction_ledger import ModelRevision, Outcome, Prediction, PredictionLedger

BASELINE_MODEL = 'frozen-laplace-prior-v1'
REVISED_MODEL = 'resolved-laplace-revision-v1'
HORIZON = timedelta(days=1)


@dataclass(frozen=True, slots=True)
class PredictionQuestion:
    status: str
    reason: str
    tenant_id: str | None = None
    company: str | None = None
    target_definition: str | None = None
    unit: str | None = None
    threshold: str | None = None
    relationship: Mapping[str, object] | None = None
    evidence_ids: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class OutcomeQuery:
    case_id: str
    target_definition: str
    unit: str
    horizon_end: datetime


OutcomeAcquirer = Callable[[tuple[OutcomeQuery, ...]], tuple[Observation, ...]]


def _stable(prefix: str, *parts: str) -> str:
    value = hashlib.sha256('\0'.join(parts).encode()).hexdigest()[:24]
    return f'{prefix}-{value}'


def select_prediction_question(assessment: Mapping[str, object]) -> PredictionQuestion:
    """Select one supported question or return explicit insufficient-evidence UNKNOWN."""
    if not isinstance(assessment, Mapping):
        raise TypeError('assessment must be a mapping')
    if (assessment.get('execution_allowed') is not False
            or assessment.get('allow_live_customer_access') is not False):
        raise ValueError('assessment safety flags must remain false')
    findings = [item for item in assessment.get('findings', ())
                if isinstance(item, Mapping) and item.get('code') == 'next_day_sample_sales']
    relationships = [item for item in assessment.get('relationships', ())
                     if isinstance(item, Mapping)
                     and item.get('role') == 'served_item'
                     and item.get('status') == 'VALIDATED_SEMANTIC_ROLE']
    if len(findings) != 1 or not relationships:
        return PredictionQuestion(
            'UNKNOWN', 'insufficient validated sales prediction evidence and relationship')
    finding = findings[0]
    try:
        threshold = Decimal(str(finding['value']))
        unit = str(finding['unit'])
        if not threshold.is_finite() or not unit:
            raise ValueError
        references = tuple(sorted({UUID(str(value)) for value in finding['evidence_ids']}))
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return PredictionQuestion('UNKNOWN', 'prediction finding is incomplete or malformed')
    relationship = min(relationships, key=lambda item: (
        str(item.get('resource')), str(item.get('identity')),
        str(item.get('target_resource')), str(item.get('target_identity')),
    ))
    try:
        relation_refs = {UUID(str(value)) for value in relationship['evidence_ids']}
        tenant, company = str(assessment['tenant']), str(assessment['company'])
        if not tenant or not company:
            raise ValueError
    except (KeyError, TypeError, ValueError):
        return PredictionQuestion('UNKNOWN', 'relationship provenance is incomplete')
    evidence = tuple(sorted(set(references) | relation_refs))
    threshold_text = format(threshold, 'f')
    target = f'next-day-sample-sales-ge-{threshold_text}-USD-v1'
    return PredictionQuestion(
        'SUPPORTED', 'validated served-item relationship and sample-sales baseline available',
        tenant, company, target, unit, threshold_text, dict(relationship), evidence,
    )


def _admitted_outcomes(
    observations: tuple[Observation, ...], queries: tuple[OutcomeQuery, ...],
    *, tenant_id: str, company: str, threshold: Decimal,
) -> tuple[tuple[OutcomeQuery, bool, Observation], ...]:
    if type(observations) is not tuple or len(observations) != len(queries):
        raise ValueError('authorized outcome batch must match committed cases exactly')
    expected = {query.case_id: query for query in queries}
    admitted = []
    for observation in observations:
        if not isinstance(observation, Observation) or observation.mode is not ObservationMode.READ_ONLY:
            raise ValueError('canonical read-only outcome observation required')
        evidence = observation.evidence
        if (evidence.tenant_id != tenant_id or evidence.kind is not EvidenceKind.EXPERIMENT
                or evidence.observed_at.tzinfo is None):
            raise ValueError('outcome tenant and observed time are required')
        payload = evidence.payload
        if not isinstance(payload, Mapping) or set(payload) != {'resource', 'record', 'provenance'}:
            raise ValueError('outcome must pass canonical pilot admission')
        record, provenance = payload['record'], payload['provenance']
        required = {'case_id', 'company', 'observed_on', 'target_definition', 'unit', 'value'}
        if not isinstance(record, Mapping) or set(record) != required:
            raise ValueError('outcome record shape mismatch')
        case_id = record['case_id']
        if case_id not in expected:
            raise ValueError('outcome does not match a committed case')
        query = expected.pop(case_id)
        if (record['company'], record['target_definition'], record['unit'], record['observed_on']) != (
                company, query.target_definition, query.unit, query.horizon_end.date().isoformat()):
            raise ValueError('outcome scope, cohort, unit or horizon mismatch')
        if evidence.observed_at < query.horizon_end:
            raise ValueError('outcome became observable before prediction horizon')
        if not isinstance(provenance, Mapping) or not provenance.get('authorization_id') \
                or not provenance.get('source_id'):
            raise ValueError('authorized outcome provenance required')
        try:
            value = Decimal(str(record['value']))
        except InvalidOperation as error:
            raise ValueError('outcome value must be numeric') from error
        if not value.is_finite():
            raise ValueError('outcome value must be finite')
        admitted.append((query, value >= threshold, observation))
    if expected:
        raise ValueError('one or more committed outcomes are unavailable')
    return tuple(sorted(admitted, key=lambda item: item[0].case_id))


def _provenance_domains(rows: Sequence[tuple[OutcomeQuery, bool, Observation]]) -> set[tuple[str, str]]:
    return {(str(item[2].evidence.payload['provenance']['authorization_id']),
             str(item[2].evidence.payload['provenance']['source_id'])) for item in rows}


def _resolve(
    ledger: PredictionLedger, queue: SQLiteEventQueue,
    rows: Sequence[tuple[OutcomeQuery, bool, Observation]],
    prediction_ids: Mapping[str, tuple[str, ...]], *, tenant_id: str,
) -> dict[str, object]:
    receipts = {}
    count = 0
    for query, actual, observation in rows:
        for prediction_id in prediction_ids[query.case_id]:
            count += 1
            event = EnterpriseEvent(
                _stable('event', prediction_id), tenant_id,
                str(observation.evidence.payload['provenance']['source_id']),
                str(observation.evidence.evidence_id), observation.evidence.observed_at,
                observation.evidence.observed_at, _stable('cycle', query.target_definition),
                EnterpriseEventKind.PREDICTION_OUTCOME_OBSERVED, query.target_definition,
            )
            receipt = OutcomeReceipt(event, Outcome(
                tenant_id, prediction_id, observation.evidence.observed_at,
                actual, (observation.evidence.evidence_id,),
            ))
            receipts[(event.tenant_id, event.idempotency_key)] = receipt
            queue.enqueue(event, route_enterprise_event(event))
    handler = OutcomeEventHandler(ledger, lambda tenant, key: receipts.get((tenant, key)))
    report = run_event_worker(queue, handler, EventWorkerBounds(count, 1), clock=ledger.clock)
    if report.completed != count or report.failed_attempts or report.dead_lettered:
        raise RuntimeError('outcome event processing did not complete')
    return asdict(report)


def run_learning_cycle(
    assessment: Mapping[str, object], *, ledger_path: str | Path,
    queue_path: str | Path, acquire_development: OutcomeAcquirer,
    acquire_evaluation: OutcomeAcquirer, clock: Callable[[], datetime],
) -> dict[str, object]:
    """Run one finite offline cycle; acquisition callbacks retain authorization."""
    question = select_prediction_question(assessment)
    if question.status == 'UNKNOWN':
        return {
            'version': 'organizational-learning-cycle-v1', 'status': 'UNKNOWN',
            'reason': question.reason, 'learning_loop_works': False,
            'prediction_improved': None, 'execution_allowed': False,
            'allow_live_customer_access': False, 'LIVE_PILOT_READY': False,
        }
    if not callable(acquire_development) or not callable(acquire_evaluation) or not callable(clock):
        raise TypeError('two authorized acquisition ports and a clock are required')
    if not (question.tenant_id and question.company and question.target_definition
            and question.unit and question.threshold):
        raise ValueError('supported question is missing required fields')
    tenant, target = question.tenant_id, question.target_definition
    issued = clock()
    development = tuple(OutcomeQuery(f'development-{index}', target, question.unit,
                                     issued + HORIZON) for index in (1, 2))
    ledger = PredictionLedger(ledger_path, clock=clock)
    development_ids = {}
    for query in development:
        identity = _stable('prediction', tenant, query.case_id, BASELINE_MODEL)
        development_ids[query.case_id] = (identity,)
        ledger.record(Prediction(
            tenant, identity, target, BASELINE_MODEL, issued, issued, query.horizon_end,
            0.5, question.evidence_ids, question.unit,
        ))
    development_rows = _admitted_outcomes(
        acquire_development(development), development, tenant_id=tenant,
        company=question.company, threshold=Decimal(question.threshold),
    )
    queue = SQLiteEventQueue(queue_path)
    development_worker = _resolve(
        ledger, queue, development_rows, development_ids, tenant_id=tenant)
    labels = tuple(actual for _, actual, _ in development_rows)
    outcome_refs = tuple(sorted({row[2].evidence.evidence_id for row in development_rows}))
    revised_probability = (1 + sum(labels)) / (2 + len(labels))
    decided = clock()
    revision = ModelRevision(
        tenant, _stable('revision', tenant, target, REVISED_MODEL), target, question.unit,
        int(HORIZON.total_seconds()), BASELINE_MODEL, REVISED_MODEL, 0.5,
        revised_probability, decided, outcome_refs,
    )
    ledger.record_revision(revision)

    evaluation_issued = clock()
    evaluation = tuple(OutcomeQuery(f'evaluation-{index}', target, question.unit,
                                    evaluation_issued + HORIZON) for index in (1, 2))
    evaluation_ids = {}
    for query in evaluation:
        arms = []
        for model, probability in ((BASELINE_MODEL, 0.5),
                                   (REVISED_MODEL, revised_probability)):
            identity = _stable('prediction', tenant, query.case_id, model)
            arms.append(identity)
            ledger.record(Prediction(
                tenant, identity, target, model, evaluation_issued, decided,
                query.horizon_end, probability,
                tuple(sorted(set(question.evidence_ids) | set(outcome_refs))),
                question.unit, query.case_id,
            ))
        evaluation_ids[query.case_id] = tuple(arms)
    evaluation_rows = _admitted_outcomes(
        acquire_evaluation(evaluation), evaluation, tenant_id=tenant,
        company=question.company, threshold=Decimal(question.threshold),
    )
    development_domains = _provenance_domains(development_rows)
    evaluation_domains = _provenance_domains(evaluation_rows)
    if ({item[0] for item in development_domains} & {item[0] for item in evaluation_domains}
            or {item[1] for item in development_domains} & {item[1] for item in evaluation_domains}):
        raise ValueError('development and evaluation require distinct authorizations and sources')
    evaluation_worker = _resolve(
        ledger, queue, evaluation_rows, evaluation_ids, tenant_id=tenant)
    comparison = ledger.paired_score(
        tenant, target_definition=target, unit=question.unit,
        prior_model_version=BASELINE_MODEL, revised_model_version=REVISED_MODEL,
    )
    failed = tuple(query.case_id for query, actual, _ in (*development_rows, *evaluation_rows)
                   if actual is False)
    recovered = recover_learning_assessment(
        ledger_path, tenant_id=tenant, target_definition=target, unit=question.unit,
        prior_model_version=BASELINE_MODEL, revised_model_version=REVISED_MODEL,
    )
    return {
        'version': 'organizational-learning-cycle-v1', 'status': 'MEASURED',
        'assessment_id': assessment.get('assessment_id'),
        'organization': {'tenant': tenant, 'company': question.company,
                         'source': assessment.get('source'), 'synthetic': True},
        'relationship': dict(question.relationship or {}),
        'question': {'target_definition': target, 'threshold': question.threshold,
                     'unit': question.unit, 'horizon_seconds': int(HORIZON.total_seconds()),
                     'source_finding_was_prospective': False},
        'development': {
            'cases': len(development_rows), 'baseline_probability': 0.5,
            'actuals': labels,
            'brier': sum((0.5 - int(value)) ** 2 for value in labels) / len(labels),
            'authorization_domains': tuple(sorted(development_domains)),
            'worker': development_worker,
        },
        'revision': {**asdict(revision), 'decided_at': revision.decided_at.isoformat(),
                     'evidence_ids': tuple(map(str, revision.evidence_ids))},
        'evaluation': {**comparison,
                       'authorization_domains': tuple(sorted(evaluation_domains)),
                       'worker': evaluation_worker},
        'failed_prediction_cases': failed,
        'discovery_contradictions': tuple(assessment.get('contradictions', ())),
        'fresh_process_recovery': recovered,
        'learning_loop_works': True,
        'prediction_improved': comparison['prediction_improved'],
        'shared_fixture_engine_authorship': True,
        'synthetic_generalization_proven': False,
        'deployment_qualification': 'UNRESOLVED',
        'action_authority': 'NONE', 'execution_allowed': False,
        'allow_live_customer_access': False, 'LIVE_PILOT_READY': False,
    }


def recover_learning_assessment(
    ledger_path: str | Path, *, tenant_id: str, target_definition: str, unit: str,
    prior_model_version: str, revised_model_version: str,
) -> dict[str, object]:
    """Reopen durable learning state without an acquisition or authority port."""
    ledger = PredictionLedger(ledger_path)
    revisions = [item for item in ledger.revisions(tenant_id)
                 if (item.target_definition, item.unit, item.prior_model_version,
                     item.revised_model_version) == (target_definition, unit,
                                                     prior_model_version,
                                                     revised_model_version)]
    if len(revisions) != 1:
        raise ValueError('exactly one matching durable revision required')
    comparison = ledger.paired_score(
        tenant_id, target_definition=target_definition, unit=unit,
        prior_model_version=prior_model_version, revised_model_version=revised_model_version,
    )
    return {
        'revision_id': revisions[0].revision_id, 'comparison': comparison,
        'authority_restored': False, 'acquisition_available': False,
        'execution_allowed': False,
    }


def owner_report(assessment: Mapping[str, object]) -> str:
    """Render the machine result without recalculating or claiming generalization."""
    if assessment.get('status') != 'MEASURED':
        return f"ORION learning cycle: UNKNOWN — {assessment.get('reason', 'insufficient evidence')}"
    evaluation = assessment['evaluation']
    return '\n'.join((
        'ORION — synthetic organizational learning cycle',
        f"Learning loop works: {str(assessment['learning_loop_works']).lower()}",
        f"Prediction improved on later unseen outcomes: {str(assessment['prediction_improved']).lower()}",
        f"Frozen baseline Brier: {evaluation['prior_brier']}",
        f"Revised-method Brier: {evaluation['revised_brier']}",
        'Synthetic success does not prove real-organization generalization.',
        'No action authority. Deployment qualification remains unresolved.',
    ))


__all__ = [
    'BASELINE_MODEL',
    'HORIZON',
    'REVISED_MODEL',
    'OutcomeQuery',
    'PredictionQuestion',
    'owner_report',
    'recover_learning_assessment',
    'run_learning_cycle',
    'select_prediction_question',
]
