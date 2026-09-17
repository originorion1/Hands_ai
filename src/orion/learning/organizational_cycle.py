"""One bounded, evidence-backed organizational learning cycle.

This composes governed discovery with the canonical prediction ledger and
outcome-event handler. It acquires no data itself and grants no authority.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections import defaultdict
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
from .prediction_ledger import (
    BusinessCohort,
    ModelRevision,
    Outcome,
    Prediction,
    PredictionLedger,
)

BASELINE_MODEL = 'frozen-laplace-prior-v1'
REVISED_MODEL = 'resolved-laplace-revision-v1'
HORIZON = timedelta(days=1)
MAX_OUTCOME_RECORDS = 100


@dataclass(frozen=True, slots=True)
class OperationalLayout:
    tenant_id: str
    company: str
    resource: str
    value_field: str
    date_field: str
    unit: str
    evidence_ids: tuple[UUID, ...]
    completeness_limitations: tuple[str, ...]


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
    cohort: BusinessCohort

    @property
    def horizon_end(self) -> datetime:
        return self.cohort.window_end


@dataclass(frozen=True, slots=True)
class NormalizedOutcome:
    query: OutcomeQuery
    actual: bool
    total: Decimal
    evidence_ids: tuple[UUID, ...]
    observations: tuple[Observation, ...]
    record_provenance: tuple[Mapping[str, object], ...]


OutcomeAcquirer = Callable[[tuple[OutcomeQuery, ...]], tuple[Observation, ...]]


def _stable(prefix: str, *parts: str) -> str:
    value = hashlib.sha256('\0'.join(parts).encode()).hexdigest()[:24]
    return f'{prefix}-{value}'


def _cohort_key(cohort: BusinessCohort) -> str:
    return json.dumps(asdict(cohort), default=lambda value: value.isoformat(),
                      sort_keys=True, separators=(',', ':'))


def _unit_token(unit: str) -> str:
    slug = re.sub(r'[^A-Za-z0-9._-]+', '-', unit).strip('-') or 'unit'
    return f'{slug}-{hashlib.sha256(unit.encode()).hexdigest()[:8]}'


def select_operational_layout(assessment: Mapping[str, object]) -> OperationalLayout:
    """Derive the later-record normalization layout from validated discovery."""
    if not isinstance(assessment, Mapping):
        raise TypeError('assessment must be a mapping')
    candidates = []
    for row in assessment.get('normalized_records', ()):
        if not isinstance(row, Mapping) or not isinstance(row.get('cells'), Mapping):
            continue
        cells = row['cells']
        if {'gross_sales', 'business_event_date'} <= set(cells):
            sales, occurred = cells['gross_sales'], cells['business_event_date']
            if (sales.get('status'), occurred.get('status')) != (
                    'VALIDATED_SEMANTIC_ROLE', 'VALIDATED_SEMANTIC_ROLE'):
                continue
            candidates.append((
                str(row['resource']), str(sales['field']), str(occurred['field']),
                str(sales['unit']), tuple(UUID(str(value)) for value in
                                         (*sales['evidence_ids'], *occurred['evidence_ids'])),
            ))
    contracts = {(resource, value_field, date_field, unit)
                 for resource, value_field, date_field, unit, _ in candidates}
    if len(contracts) != 1:
        raise ValueError('exactly one validated operational sales layout required')
    resource, value_field, date_field, unit = next(iter(contracts))
    evidence = tuple(sorted({item for candidate in candidates for item in candidate[4]}))
    limitations = tuple(dict.fromkeys(
        str(value) for value in assessment.get('unknowns', ()) if str(value).strip()))
    tenant, company = assessment.get('tenant'), assessment.get('company')
    if not isinstance(tenant, str) or not tenant or not isinstance(company, str) or not company:
        raise ValueError('assessment tenant and company are required')
    return OperationalLayout(
        tenant, company, resource, value_field, date_field, unit, evidence, limitations)


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
    try:
        layout = select_operational_layout(assessment)
        finding = findings[0]
        threshold = Decimal(str(finding['value']))
        unit = str(finding['unit'])
        if not threshold.is_finite() or not unit or unit != layout.unit:
            raise ValueError
        references = {UUID(str(value)) for value in finding['evidence_ids']}
    except (InvalidOperation, KeyError, TypeError, ValueError):
        return PredictionQuestion(
            'UNKNOWN', 'prediction finding and discovered operational unit are inconsistent')
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
    evidence = tuple(sorted(references | relation_refs | set(layout.evidence_ids)))
    threshold_text = format(threshold, 'f')
    target = f'next-day-sample-sales-ge-{threshold_text}-unit-{_unit_token(unit)}-v1'
    return PredictionQuestion(
        'SUPPORTED', 'validated served-item relationship and sample-sales baseline available',
        tenant, company, target, unit, threshold_text, dict(relationship), evidence,
    )


def _topology(assessment: Mapping[str, object]) -> tuple[str, tuple[tuple[object, ...], ...]]:
    fields: dict[str, set[str]] = defaultdict(set)
    for claim in assessment.get('semantic_claims', ()):
        if isinstance(claim, Mapping) and isinstance(claim.get('resource'), str) \
                and isinstance(claim.get('field'), str):
            fields[claim['resource']].add(claim['field'])
    topology = []
    for summary in assessment.get('resource_summary', ()):
        if not isinstance(summary, Mapping):
            continue
        resource = str(summary.get('resource'))
        topology.append((
            len(fields[resource]), int(summary.get('observed_identity_count', 0)),
            tuple(sorted(str(value) for value in summary.get('validated_sample_roles', ()))),
        ))
    topology_tuple = tuple(sorted(topology))
    digest = hashlib.sha256(json.dumps(topology_tuple, separators=(',', ':')).encode()).hexdigest()
    return digest, topology_tuple


def _admitted_outcomes(
    observations: tuple[Observation, ...], queries: tuple[OutcomeQuery, ...],
    *, layout: OperationalLayout, threshold: Decimal,
) -> tuple[NormalizedOutcome, ...]:
    if (type(observations) is not tuple or not observations
            or len(observations) > MAX_OUTCOME_RECORDS):
        raise ValueError('authorized operational outcome batch must be non-empty and bounded')
    expected = {_cohort_key(query.cohort): query for query in queries}
    if len(expected) != len(queries):
        raise ValueError('duplicate outcome cohorts are forbidden')
    grouped: dict[str, list[tuple[Observation, Decimal]]] = defaultdict(list)
    record_ids = set()
    for observation in observations:
        if not isinstance(observation, Observation) or observation.mode is not ObservationMode.READ_ONLY:
            raise ValueError('canonical read-only outcome observation required')
        evidence = observation.evidence
        if (evidence.tenant_id != layout.tenant_id
                or evidence.kind is not EvidenceKind.EXPERIMENT
                or evidence.observed_at.tzinfo is None):
            raise ValueError('experimental outcome tenant and observed time are required')
        payload = evidence.payload
        if not isinstance(payload, Mapping) or set(payload) != {'resource', 'record', 'provenance'}:
            raise ValueError('outcome must pass canonical pilot admission')
        record, provenance = payload['record'], payload['provenance']
        if payload['resource'] != layout.resource or not isinstance(record, Mapping):
            raise ValueError('operational outcome resource does not match discovered layout')
        if layout.value_field not in record or layout.date_field not in record:
            raise ValueError('discovered operational fields are absent')
        if not isinstance(provenance, Mapping) or not provenance.get('authorization_id') \
                or not provenance.get('source_id') or not provenance.get('source_record_id'):
            raise ValueError('authorized record-level outcome provenance required')
        source_record_id = str(provenance['source_record_id'])
        if source_record_id in record_ids:
            raise ValueError('duplicate operational source record')
        record_ids.add(source_record_id)
        candidates = [(_cohort_key(query.cohort), query) for query in queries
                      if (query.cohort.entity_id, query.cohort.location_id,
                          record[layout.date_field])
                      == (layout.tenant_id, layout.company,
                          query.cohort.window_end.date().isoformat())]
        if len(candidates) != 1:
            raise ValueError('operational record does not match one committed business cohort')
        key, query = candidates[0]
        if evidence.observed_at < query.horizon_end:
            raise ValueError('operational outcome became observable before prediction horizon')
        try:
            value = Decimal(str(record[layout.value_field]))
        except InvalidOperation as error:
            raise ValueError('operational outcome value must be numeric') from error
        if not value.is_finite():
            raise ValueError('operational outcome value must be finite')
        grouped[key].append((observation, value))
    normalized = []
    for key, query in sorted(expected.items(), key=lambda item: item[1].case_id):
        records = grouped.get(key, ())
        if not records:
            raise ValueError('one or more committed cohort outcomes are unavailable')
        total = sum((value for _, value in records), Decimal(0))
        evidence_ids = tuple(sorted({*layout.evidence_ids,
                                     *(item.evidence.evidence_id for item, _ in records)}))
        provenance = tuple({
            'source_record_id': str(observation.evidence.payload['provenance']['source_record_id']),
            'evidence_id': str(observation.evidence.evidence_id),
            'normalized_value': format(value, 'f'), 'unit': layout.unit,
        } for observation, value in records)
        normalized.append(NormalizedOutcome(
            query, total >= threshold, total, evidence_ids,
            tuple(observation for observation, _ in records), provenance,
        ))
    return tuple(normalized)


def _provenance_domains(rows: Sequence[NormalizedOutcome]) -> set[tuple[str, str]]:
    return {(str(observation.evidence.payload['provenance']['authorization_id']),
             str(observation.evidence.payload['provenance']['source_id']))
            for row in rows for observation in row.observations}


def _resolve(
    ledger: PredictionLedger, queue: SQLiteEventQueue,
    rows: Sequence[NormalizedOutcome], prediction_ids: Mapping[str, tuple[str, ...]],
    *, tenant_id: str,
) -> dict[str, object]:
    receipts = {}
    count = 0
    for row in rows:
        cohort_key = _cohort_key(row.query.cohort)
        sources = {str(observation.evidence.payload['provenance']['source_id'])
                   for observation in row.observations}
        if len(sources) != 1:
            raise ValueError('one outcome cohort must come from one admitted source')
        observed_at = max(observation.evidence.observed_at for observation in row.observations)
        for prediction_id in prediction_ids[cohort_key]:
            count += 1
            event = EnterpriseEvent(
                _stable('event', prediction_id), tenant_id, next(iter(sources)),
                _stable('outcome', *(str(value) for value in row.evidence_ids)), observed_at,
                observed_at, _stable('cycle', row.query.target_definition),
                EnterpriseEventKind.PREDICTION_OUTCOME_OBSERVED,
                row.query.target_definition,
            )
            receipt = OutcomeReceipt(event, Outcome(
                tenant_id, prediction_id, observed_at, row.actual,
                row.evidence_ids, row.query.cohort,
            ))
            receipts[(event.tenant_id, event.idempotency_key)] = receipt
            queue.enqueue(event, route_enterprise_event(event))
    handler = OutcomeEventHandler(ledger, lambda tenant, key: receipts.get((tenant, key)))
    report = run_event_worker(queue, handler, EventWorkerBounds(count, 1), clock=ledger.clock)
    if report.completed != count or report.failed_attempts or report.dead_lettered:
        raise RuntimeError('outcome event processing did not complete')
    return asdict(report)


def _query(case_id: str, question: PredictionQuestion, issued_at: datetime) -> OutcomeQuery:
    if not (question.tenant_id and question.company and question.target_definition and question.unit):
        raise ValueError('supported question is incomplete')
    cohort = BusinessCohort(
        question.tenant_id, question.company, issued_at, issued_at + HORIZON)
    return OutcomeQuery(case_id, question.target_definition, question.unit, cohort)


def _outcome_report(rows: Sequence[NormalizedOutcome], layout: OperationalLayout) -> tuple[dict, ...]:
    return tuple({
        'case_id': row.query.case_id,
        'cohort': {**asdict(row.query.cohort),
                   'window_start': row.query.cohort.window_start.isoformat(),
                   'window_end': row.query.cohort.window_end.isoformat()},
        'normalized_total': format(row.total, 'f'), 'unit': layout.unit,
        'actual': row.actual, 'record_provenance': row.record_provenance,
        'completeness_limitations': layout.completeness_limitations,
    } for row in rows)


def run_learning_cycle(
    assessment: Mapping[str, object], evaluation_assessment: Mapping[str, object], *,
    ledger_path: str | Path, queue_path: str | Path,
    acquire_development: OutcomeAcquirer, acquire_evaluation: OutcomeAcquirer,
    clock: Callable[[], datetime],
) -> dict[str, object]:
    """Run one finite offline cycle; acquisition callbacks retain authorization."""
    question = select_prediction_question(assessment)
    evaluation_question = select_prediction_question(evaluation_assessment)
    if question.status == 'UNKNOWN' or evaluation_question.status == 'UNKNOWN':
        return {
            'version': 'organizational-learning-cycle-v2', 'status': 'UNKNOWN',
            'reason': (question.reason if question.status == 'UNKNOWN'
                       else evaluation_question.reason),
            'learning_cycle_integration': False,
            'unfamiliar_environment_evaluation': False,
            'predictive_improvement': None, 'execution_allowed': False,
            'allow_live_customer_access': False, 'LIVE_PILOT_READY': False,
        }
    if not callable(acquire_development) or not callable(acquire_evaluation) or not callable(clock):
        raise TypeError('two authorized acquisition ports and a clock are required')
    if not (question.tenant_id and question.company and question.target_definition
            and question.unit and question.threshold):
        raise ValueError('supported question is missing required fields')
    if ((evaluation_question.tenant_id, evaluation_question.company,
         evaluation_question.target_definition, evaluation_question.unit,
         evaluation_question.threshold)
            != (question.tenant_id, question.company, question.target_definition,
                question.unit, question.threshold)):
        raise ValueError('evaluation discovery does not support the frozen question contract')
    development_signature, development_topology = _topology(assessment)
    evaluation_signature, evaluation_topology = _topology(evaluation_assessment)
    if not development_topology or development_topology == evaluation_topology:
        raise ValueError('evaluation environment must have a structurally different topology')
    development_layout = select_operational_layout(assessment)
    evaluation_layout = select_operational_layout(evaluation_assessment)
    tenant, target = question.tenant_id, question.target_definition
    threshold = Decimal(question.threshold)
    ledger = PredictionLedger(ledger_path, clock=clock)
    queue = SQLiteEventQueue(queue_path)
    development_rows: list[NormalizedOutcome] = []
    development_workers = []
    development_ids = {}
    for index in (1, 2):
        issued = clock()
        query = _query(f'development-{index}', question, issued)
        cohort_key = _cohort_key(query.cohort)
        identity = _stable('prediction', tenant, target, BASELINE_MODEL, cohort_key)
        development_ids[cohort_key] = (identity,)
        ledger.record(Prediction(
            tenant, identity, target, BASELINE_MODEL, issued, issued, query.horizon_end,
            0.5, question.evidence_ids, question.unit, query.cohort,
        ))
        normalized = _admitted_outcomes(
            acquire_development((query,)), (query,), layout=development_layout,
            threshold=threshold,
        )
        development_rows.extend(normalized)
        development_workers.append(_resolve(
            ledger, queue, normalized, development_ids, tenant_id=tenant))
    development_domains = _provenance_domains(development_rows)
    labels = tuple(row.actual for row in development_rows)
    outcome_refs = tuple(sorted({value for row in development_rows for value in row.evidence_ids}))
    revised_probability = (1 + sum(labels)) / (2 + len(labels))
    decided = clock()
    revision = ModelRevision(
        tenant, _stable('revision', tenant, target, REVISED_MODEL), target, question.unit,
        int(HORIZON.total_seconds()), BASELINE_MODEL, REVISED_MODEL, 0.5,
        revised_probability, decided, outcome_refs,
    )
    ledger.record_revision(revision)

    evaluation_rows: list[NormalizedOutcome] = []
    evaluation_workers = []
    evaluation_ids = {}
    for index in (1, 2):
        issued = clock()
        query = _query(f'evaluation-{index}', evaluation_question, issued)
        cohort_key = _cohort_key(query.cohort)
        arms = []
        for model, probability in ((BASELINE_MODEL, 0.5),
                                   (REVISED_MODEL, revised_probability)):
            identity = _stable('prediction', tenant, target, model, cohort_key)
            arms.append(identity)
            ledger.record(Prediction(
                tenant, identity, target, model, issued, decided, query.horizon_end,
                probability, tuple(sorted(set(evaluation_question.evidence_ids)
                                          | set(outcome_refs))), question.unit, query.cohort,
            ))
        evaluation_ids[cohort_key] = tuple(arms)
        normalized = _admitted_outcomes(
            acquire_evaluation((query,)), (query,), layout=evaluation_layout,
            threshold=threshold,
        )
        evaluation_domains = _provenance_domains(normalized)
        if ({item[0] for item in development_domains} & {item[0] for item in evaluation_domains}
                or {item[1] for item in development_domains}
                & {item[1] for item in evaluation_domains}):
            raise ValueError('development and evaluation require distinct authorizations and sources')
        evaluation_rows.extend(normalized)
        evaluation_workers.append(_resolve(
            ledger, queue, normalized, evaluation_ids, tenant_id=tenant))
    evaluation_domains = _provenance_domains(evaluation_rows)
    comparison = ledger.paired_score(
        tenant, target_definition=target, unit=question.unit,
        prior_model_version=BASELINE_MODEL, revised_model_version=REVISED_MODEL,
        evaluation_after=decided,
    )
    failed = tuple(row.query.case_id for row in (*development_rows, *evaluation_rows)
                   if row.actual is False)
    reopened = recover_learning_assessment(
        ledger_path, tenant_id=tenant, target_definition=target, unit=question.unit,
        prior_model_version=BASELINE_MODEL, revised_model_version=REVISED_MODEL,
    )
    remaining_unknowns = tuple(dict.fromkeys((
        *development_layout.completeness_limitations,
        *evaluation_layout.completeness_limitations,
        'Synthetic evaluation does not prove real-organization generalization.',
        'Shared fixture and engine authorship is not independent evidence.',
    )))
    return {
        'version': 'organizational-learning-cycle-v2', 'status': 'MEASURED',
        'assessment_id': assessment.get('assessment_id'),
        'evaluation_assessment_id': evaluation_assessment.get('assessment_id'),
        'organization': {'tenant': tenant, 'company': question.company, 'synthetic': True},
        'relationship': dict(question.relationship or {}),
        'question': {'target_definition': target, 'threshold': question.threshold,
                     'unit': question.unit, 'horizon_seconds': int(HORIZON.total_seconds()),
                     'source_finding_was_prospective': False},
        'development': {
            'cases': len(development_rows), 'baseline_probability': 0.5,
            'actuals': labels,
            'brier': sum((0.5 - int(value)) ** 2 for value in labels) / len(labels),
            'authorization_domains': tuple(sorted(development_domains)),
            'operational_outcomes': _outcome_report(development_rows, development_layout),
            'workers': tuple(development_workers),
        },
        'revision': {**asdict(revision), 'decided_at': revision.decided_at.isoformat(),
                     'evidence_ids': tuple(map(str, revision.evidence_ids))},
        'evaluation': {
            **comparison, 'authorization_domains': tuple(sorted(evaluation_domains)),
            'operational_outcomes': _outcome_report(evaluation_rows, evaluation_layout),
            'workers': tuple(evaluation_workers),
        },
        'durable_completed_state_reopen': reopened,
        'learning_cycle_integration': True,
        'unfamiliar_environment_evaluation': {
            'works': True, 'development_topology_sha256': development_signature,
            'evaluation_topology_sha256': evaluation_signature,
            'topologies_differ': True,
            'separate_access_grants_are_independence_proof': False,
        },
        'predictive_improvement': comparison['prediction_improved'],
        'failed_prediction_cases': failed,
        'discovery_contradictions': tuple(assessment.get('contradictions', ())),
        'remaining_unknowns': remaining_unknowns,
        'shared_fixture_engine_authorship': True,
        'synthetic_generalization_proven': False,
        'deployment_qualification': 'UNRESOLVED',
        'action_authority': 'NONE', 'execution_allowed': False,
        'allow_live_customer_access': False, 'LIVE_PILOT_READY': False,
    }


def recover_committed_measurement(
    ledger_path: str | Path, *, tenant_id: str, target_definition: str,
    model_version: str,
) -> dict[str, object]:
    """Recover pending ledger state only; no source or authority port is restored."""
    return PredictionLedger(ledger_path).commitment_state(
        tenant_id, target_definition=target_definition, model_version=model_version)


def recover_learning_assessment(
    ledger_path: str | Path, *, tenant_id: str, target_definition: str, unit: str,
    prior_model_version: str, revised_model_version: str,
) -> dict[str, object]:
    """Reopen completed durable state in-process without claiming a process restart."""
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
        evaluation_after=revisions[0].decided_at,
    )
    return {
        'revision_id': revisions[0].revision_id, 'comparison': comparison,
        'process_restart_claimed': False, 'authority_restored': False,
        'acquisition_available': False, 'execution_allowed': False,
    }


def owner_report(assessment: Mapping[str, object]) -> str:
    """Render the machine result without recalculating or claiming generalization."""
    if assessment.get('status') != 'MEASURED':
        return f"ORION learning cycle: UNKNOWN — {assessment.get('reason', 'insufficient evidence')}"
    evaluation = assessment['evaluation']
    unfamiliar = assessment['unfamiliar_environment_evaluation']
    return '\n'.join((
        'ORION — synthetic organizational learning cycle',
        f"Learning-cycle integration: {str(assessment['learning_cycle_integration']).lower()}",
        f"Unfamiliar-environment evaluation: {str(unfamiliar['works']).lower()}",
        f"Predictive improvement: {str(assessment['predictive_improvement']).lower()}",
        f"Frozen baseline Brier: {evaluation['prior_brier']}",
        f"Revised-method Brier: {evaluation['revised_brier']}",
        'Remaining unknowns: ' + '; '.join(assessment['remaining_unknowns']),
        'Synthetic success does not prove real-organization generalization.',
        'No action authority. Deployment qualification remains unresolved.',
    ))


__all__ = [
    'BASELINE_MODEL',
    'HORIZON',
    'REVISED_MODEL',
    'OperationalLayout',
    'OutcomeQuery',
    'PredictionQuestion',
    'owner_report',
    'recover_committed_measurement',
    'recover_learning_assessment',
    'run_learning_cycle',
    'select_operational_layout',
    'select_prediction_question',
]
