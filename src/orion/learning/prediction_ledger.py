"""Prospective binary prediction measurement; no predictor or action authority.

Evidence references must be verified by the caller against its tenant-local
evidence store. This ledger checks time and identity, not source authenticity.
"""

from __future__ import annotations

import json
import math
import sqlite3
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from ..contracts import utc_now


def _time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('time must be timezone-aware')
    return value.astimezone(UTC)


def _identity(value: str) -> None:
    if not isinstance(value, str) or not value.strip() or len(value) > 256:
        raise ValueError('identity must be non-empty and bounded')


def _references(value: tuple[UUID, ...]) -> None:
    if not isinstance(value, tuple) or not value or not all(isinstance(x, UUID) for x in value):
        raise ValueError('evidence must be a non-empty immutable UUID tuple')
    if len(set(value)) != len(value):
        raise ValueError('evidence references must be unique')


@dataclass(frozen=True, slots=True)
class BusinessCohort:
    """Exact entity/location/time population represented by one prediction."""

    entity_id: str
    location_id: str
    window_start: datetime
    window_end: datetime

    def __post_init__(self) -> None:
        _identity(self.entity_id)
        _identity(self.location_id)
        if _time(self.window_start) >= _time(self.window_end):
            raise ValueError('cohort window must be non-empty')


@dataclass(frozen=True, slots=True)
class Prediction:
    tenant_id: str
    prediction_id: str
    target_definition: str
    model_version: str
    issued_at: datetime
    evidence_cutoff: datetime
    horizon_end: datetime
    probability: float
    evidence_ids: tuple[UUID, ...]
    unit: str = 'binary'
    cohort: BusinessCohort | None = None

    def __post_init__(self) -> None:
        for value in (self.tenant_id, self.prediction_id, self.target_definition,
                      self.model_version, self.unit):
            _identity(value)
        if self.cohort is not None:
            if not isinstance(self.cohort, BusinessCohort):
                raise TypeError('cohort must be BusinessCohort')
            if _time(self.horizon_end) != _time(self.cohort.window_end):
                raise ValueError('prediction horizon must equal cohort window end')
        if not _time(self.evidence_cutoff) <= _time(self.issued_at) < _time(self.horizon_end):
            raise ValueError('prediction requires cutoff <= issuance < horizon')
        if (type(self.probability) not in (int, float)
                or not math.isfinite(self.probability) or not 0 <= self.probability <= 1):
            raise ValueError('probability must be finite and within [0, 1]')
        _references(self.evidence_ids)


@dataclass(frozen=True, slots=True)
class ModelRevision:
    """An immutable method change justified only by resolved prior outcomes."""

    tenant_id: str
    revision_id: str
    target_definition: str
    unit: str
    horizon_seconds: int
    prior_model_version: str
    revised_model_version: str
    prior_probability: float
    revised_probability: float
    decided_at: datetime
    evidence_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        for value in (self.tenant_id, self.revision_id, self.target_definition, self.unit,
                      self.prior_model_version, self.revised_model_version):
            _identity(value)
        if self.prior_model_version == self.revised_model_version:
            raise ValueError('revision requires a new model version')
        if type(self.horizon_seconds) is not int or self.horizon_seconds < 1:
            raise ValueError('revision requires a positive horizon')
        for probability in (self.prior_probability, self.revised_probability):
            if (type(probability) not in (int, float) or not math.isfinite(probability)
                    or not 0 <= probability <= 1):
                raise ValueError('revision probability must be finite and within [0, 1]')
        _time(self.decided_at)
        _references(self.evidence_ids)


@dataclass(frozen=True, slots=True)
class Outcome:
    tenant_id: str
    prediction_id: str
    observed_at: datetime
    actual: bool
    evidence_ids: tuple[UUID, ...]
    cohort: BusinessCohort | None = None

    def __post_init__(self) -> None:
        _identity(self.tenant_id)
        _identity(self.prediction_id)
        _time(self.observed_at)
        if type(self.actual) is not bool:
            raise ValueError('actual must be bool')
        if self.cohort is not None and not isinstance(self.cohort, BusinessCohort):
            raise TypeError('cohort must be BusinessCohort')
        _references(self.evidence_ids)


def _json(record: Prediction | Outcome | ModelRevision) -> str:
    def encode(value):
        if isinstance(value, datetime):
            return _time(value).isoformat()
        if isinstance(value, UUID):
            return str(value)
        raise TypeError('unsupported ledger value')
    return json.dumps(asdict(record), default=encode, sort_keys=True, separators=(',', ':'))


class PredictionLedger:
    """Local append-only API with transactional exact replay checks.

    Local database/clock access is trusted. This is not a tamper-proof audit
    service and does not sandbox an injected predictor or authenticate evidence.
    """

    def __init__(self, path: str | Path, *, clock: Callable[[], datetime] = utc_now) -> None:
        self.path = Path(path)
        self.clock = clock
        with self._connect() as connection:
            connection.executescript('''
                CREATE TABLE IF NOT EXISTS predictions (
                    tenant TEXT NOT NULL, identity TEXT NOT NULL, payload TEXT NOT NULL,
                    recorded_at TEXT NOT NULL, PRIMARY KEY (tenant, identity)
                );
                CREATE TABLE IF NOT EXISTS prediction_outcomes (
                    tenant TEXT NOT NULL, identity TEXT NOT NULL, payload TEXT NOT NULL,
                    recorded_at TEXT NOT NULL, PRIMARY KEY (tenant, identity),
                    FOREIGN KEY (tenant, identity) REFERENCES predictions(tenant, identity)
                );
                CREATE TABLE IF NOT EXISTS model_revisions (
                    tenant TEXT NOT NULL, identity TEXT NOT NULL, payload TEXT NOT NULL,
                    recorded_at TEXT NOT NULL, PRIMARY KEY (tenant, identity)
                );
            ''')
            connection.commit()

    def record_revision(self, revision: ModelRevision) -> None:
        """Commit a revision only when its evidence is in resolved prior outcomes."""
        if not isinstance(revision, ModelRevision):
            raise TypeError('revision must be ModelRevision')
        payload = _json(revision)
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            existing = connection.execute(
                'SELECT payload FROM model_revisions WHERE tenant=? AND identity=?',
                (revision.tenant_id, revision.revision_id),
            ).fetchone()
            if existing:
                if existing[0] != payload:
                    raise ValueError('revision replay conflict')
                return
            rows = connection.execute('''
                SELECT p.payload, o.payload FROM predictions p
                JOIN prediction_outcomes o
                  ON p.tenant=o.tenant AND p.identity=o.identity
                WHERE p.tenant=?
            ''', (revision.tenant_id,)).fetchall()
            eligible = []
            available_evidence = set()
            for prediction_json, outcome_json in rows:
                prediction, outcome = json.loads(prediction_json), json.loads(outcome_json)
                horizon = (datetime.fromisoformat(prediction['horizon_end'])
                           - datetime.fromisoformat(prediction['issued_at'])).total_seconds()
                if ((prediction['target_definition'], prediction.get('unit', 'binary'),
                     prediction['model_version'], horizon)
                        == (revision.target_definition, revision.unit,
                            revision.prior_model_version, revision.horizon_seconds)):
                    eligible.append((prediction, outcome))
                    available_evidence.update(outcome['evidence_ids'])
            if not eligible:
                raise ValueError('revision requires resolved prior-model outcomes')
            if any(prediction['probability'] != revision.prior_probability
                   for prediction, _ in eligible):
                raise ValueError('revision prior probability does not match frozen method')
            if any(datetime.fromisoformat(outcome['observed_at']) > _time(revision.decided_at)
                   for _, outcome in eligible):
                raise ValueError('revision decision cannot precede its outcomes')
            if not {str(value) for value in revision.evidence_ids} <= available_evidence:
                raise ValueError('revision evidence is not from resolved prior outcomes')
            now = _time(self.clock())
            if _time(revision.decided_at) > now:
                raise ValueError('revision decision cannot be in the future')
            connection.execute('INSERT INTO model_revisions VALUES (?, ?, ?, ?)',
                               (revision.tenant_id, revision.revision_id,
                                payload, now.isoformat()))
            connection.commit()

    def revisions(self, tenant_id: str) -> tuple[ModelRevision, ...]:
        _identity(tenant_id)
        with self._connect() as connection:
            rows = connection.execute(
                'SELECT payload FROM model_revisions WHERE tenant=? ORDER BY identity',
                (tenant_id,),
            ).fetchall()
        return tuple(ModelRevision(
            tenant_id=value['tenant_id'], revision_id=value['revision_id'],
            target_definition=value['target_definition'], unit=value['unit'],
            horizon_seconds=value['horizon_seconds'],
            prior_model_version=value['prior_model_version'],
            revised_model_version=value['revised_model_version'],
            prior_probability=value['prior_probability'],
            revised_probability=value['revised_probability'],
            decided_at=datetime.fromisoformat(value['decided_at']),
            evidence_ids=tuple(UUID(item) for item in value['evidence_ids']),
        ) for (payload,) in rows for value in (json.loads(payload),))

    def _connect(self):
        # Closing the context is explicit: sqlite connection contexts only commit.
        from contextlib import closing
        connection = sqlite3.connect(self.path, timeout=5)
        connection.execute('PRAGMA foreign_keys=ON')
        connection.execute('PRAGMA synchronous=FULL')
        return closing(connection)

    def record(self, prediction: Prediction) -> None:
        if not isinstance(prediction, Prediction):
            raise TypeError('prediction must be Prediction')
        payload = _json(prediction)
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            existing = connection.execute(
                'SELECT payload FROM predictions WHERE tenant=? AND identity=?',
                (prediction.tenant_id, prediction.prediction_id),
            ).fetchone()
            if existing:
                if existing[0] != payload:
                    raise ValueError('prediction replay conflict')
                return
            if prediction.cohort is not None:
                cohort = json.loads(payload)['cohort']
                rows = connection.execute(
                    'SELECT payload FROM predictions WHERE tenant=?',
                    (prediction.tenant_id,),
                ).fetchall()
                for (candidate_json,) in rows:
                    candidate = json.loads(candidate_json)
                    if ((candidate['target_definition'], candidate['model_version'],
                         candidate.get('cohort'))
                            == (prediction.target_definition, prediction.model_version, cohort)):
                        raise ValueError('duplicate business cohort for model and target')
            now = _time(self.clock())
            if not _time(prediction.issued_at) <= now < _time(prediction.horizon_end):
                raise ValueError('prediction must be recorded prospectively before horizon')
            connection.execute('INSERT INTO predictions VALUES (?, ?, ?, ?)',
                               (prediction.tenant_id, prediction.prediction_id,
                                payload, now.isoformat()))
            connection.commit()

    def resolve(self, outcome: Outcome) -> None:
        if not isinstance(outcome, Outcome):
            raise TypeError('outcome must be Outcome')
        with self._connect() as connection:
            connection.execute('BEGIN IMMEDIATE')
            row = connection.execute(
                'SELECT payload FROM predictions WHERE tenant=? AND identity=?',
                (outcome.tenant_id, outcome.prediction_id),
            ).fetchone()
            if row is None:
                raise ValueError('unknown prediction in tenant scope')
            prediction = json.loads(row[0])
            actual_cohort = json.loads(_json(outcome)).get('cohort')
            if prediction.get('cohort') != actual_cohort:
                raise ValueError('outcome business cohort does not match prediction')
            now = _time(self.clock())
            if not datetime.fromisoformat(prediction['horizon_end']) <= _time(outcome.observed_at) <= now:
                raise ValueError('outcome must be observed after horizon and not in future')
            payload = _json(outcome)
            existing = connection.execute(
                'SELECT payload FROM prediction_outcomes WHERE tenant=? AND identity=?',
                (outcome.tenant_id, outcome.prediction_id),
            ).fetchone()
            if existing:
                if existing[0] != payload:
                    raise ValueError('outcome replay conflict')
                return
            connection.execute('INSERT INTO prediction_outcomes VALUES (?, ?, ?, ?)',
                               (outcome.tenant_id, outcome.prediction_id, payload, now.isoformat()))
            connection.commit()

    def score(
        self, tenant_id: str, *, target_definition: str | None = None,
        model_version: str | None = None,
    ) -> dict[str, object]:
        """Score one cohort; ambiguous implicit pooling fails closed.

        Target definitions must version horizon/label semantics. Model versions
        must identify the evaluated arm. This is not a paired comparison test.
        """
        _identity(tenant_id)
        if (target_definition is None) != (model_version is None):
            raise ValueError('cohort requires both target_definition and model_version')
        if target_definition is not None:
            _identity(target_definition)
            _identity(model_version)
        with self._connect() as connection:
            rows = connection.execute('''
                SELECT p.payload, o.payload, p.recorded_at FROM predictions p
                LEFT JOIN prediction_outcomes o ON p.tenant=o.tenant AND p.identity=o.identity
                WHERE p.tenant=? ORDER BY p.identity
            ''', (tenant_id,)).fetchall()
        decoded = [(json.loads(p), json.loads(o) if o is not None else None,
                    datetime.fromisoformat(t)) for p, o, t in rows]
        if target_definition is not None:
            decoded = [(p, o, t) for p, o, t in decoded
                       if (p['target_definition'], p['model_version'])
                       == (target_definition, model_version)]
        elif len({(p['target_definition'], p['model_version']) for p, _, _ in decoded}) > 1:
            raise ValueError('multiple prediction cohorts; select target_definition and model_version')
        scored = [(p, o, t) for p, o, t in decoded if o is not None]
        contracts = {(p.get('unit', 'binary'),
                      (datetime.fromisoformat(p['horizon_end'])
                       - datetime.fromisoformat(p['issued_at'])).total_seconds())
                     for p, _, _ in decoded}
        if len(contracts) > 1:
            raise ValueError('cohort mixes unit or horizon contracts')
        pairs = [(p['probability'], int(o['actual'])) for p, o, _ in scored]
        return {
            'predictions': len(decoded), 'resolved': len(pairs),
            'pending': len(decoded) - len(pairs),
            'brier': sum((p-y)**2 for p, y in pairs) / len(pairs) if pairs else None,
            'true_positive': sum(p >= 0.5 and y == 1 for p, y in pairs),
            'false_positive': sum(p >= 0.5 and y == 0 for p, y in pairs),
            'true_negative': sum(p < 0.5 and y == 0 for p, y in pairs),
            'false_negative': sum(p < 0.5 and y == 1 for p, y in pairs),
            'lead_seconds': tuple((datetime.fromisoformat(p['horizon_end'])-t).total_seconds()
                                  for p, _, t in scored),
            'economic_value': None, 'execution_allowed': False,
            'unit': next(iter(contracts))[0] if contracts else None,
            'horizon_seconds': next(iter(contracts))[1] if contracts else None,
        }

    def commitment_state(
        self, tenant_id: str, *, target_definition: str, model_version: str,
    ) -> dict[str, object]:
        """Return durable pending measurement state without acquisition authority."""
        for value in (tenant_id, target_definition, model_version):
            _identity(value)
        with self._connect() as connection:
            rows = connection.execute('''
                SELECT p.payload, o.identity FROM predictions p
                LEFT JOIN prediction_outcomes o
                  ON p.tenant=o.tenant AND p.identity=o.identity
                WHERE p.tenant=? ORDER BY p.identity
            ''', (tenant_id,)).fetchall()
        decoded = ((json.loads(payload), outcome_id) for payload, outcome_id in rows)
        selected = [(prediction, outcome_id) for prediction, outcome_id in decoded
                    if (prediction['target_definition'], prediction['model_version'])
                    == (target_definition, model_version)]
        cohorts = tuple(item[0].get('cohort') for item in selected)
        return {
            'predictions': len(selected),
            'pending': sum(outcome_id is None for _, outcome_id in selected),
            'cohorts': cohorts,
            'authority_restored': False,
            'acquisition_available': False,
            'execution_allowed': False,
        }

    def paired_score(
        self, tenant_id: str, *, target_definition: str, unit: str,
        prior_model_version: str, revised_model_version: str,
        evaluation_after: datetime | None = None,
    ) -> dict[str, object]:
        """Compare frozen methods only on their identical resolved evaluation cases."""
        for value in (tenant_id, target_definition, unit, prior_model_version,
                      revised_model_version):
            _identity(value)
        if evaluation_after is not None:
            evaluation_after = _time(evaluation_after)
        with self._connect() as connection:
            rows = connection.execute('''
                SELECT p.payload, o.payload FROM predictions p
                JOIN prediction_outcomes o
                  ON p.tenant=o.tenant AND p.identity=o.identity
                WHERE p.tenant=? ORDER BY p.identity
            ''', (tenant_id,)).fetchall()
        arms: dict[str, dict[str, tuple[dict, dict]]] = {
            prior_model_version: {}, revised_model_version: {},
        }
        for prediction_json, outcome_json in rows:
            prediction, outcome = json.loads(prediction_json), json.loads(outcome_json)
            if ((prediction['target_definition'], prediction.get('unit', 'binary'))
                    != (target_definition, unit)
                    or prediction['model_version'] not in arms
                    or prediction.get('cohort') is None
                    or (evaluation_after is not None
                        and datetime.fromisoformat(prediction['issued_at']) < evaluation_after)):
                continue
            key = json.dumps(prediction['cohort'], sort_keys=True, separators=(',', ':'))
            if key in arms[prediction['model_version']]:
                raise ValueError('duplicate model arm for evaluation case')
            arms[prediction['model_version']][key] = (prediction, outcome)
        if not arms[prior_model_version] or set(arms[prior_model_version]) != set(arms[revised_model_version]):
            raise ValueError('paired comparison requires identical resolved evaluation cases')
        prior_errors, revised_errors, evidence = [], [], []
        horizons = set()
        for key in sorted(arms[prior_model_version]):
            prior, prior_outcome = arms[prior_model_version][key]
            revised, revised_outcome = arms[revised_model_version][key]
            prior_horizon = (datetime.fromisoformat(prior['horizon_end'])
                             - datetime.fromisoformat(prior['issued_at'])).total_seconds()
            revised_horizon = (datetime.fromisoformat(revised['horizon_end'])
                               - datetime.fromisoformat(revised['issued_at'])).total_seconds()
            if (prior_horizon != revised_horizon or prior_outcome['actual'] != revised_outcome['actual']
                    or prior_outcome['evidence_ids'] != revised_outcome['evidence_ids']):
                raise ValueError('paired case outcome or horizon mismatch')
            horizons.add(prior_horizon)
            actual = int(prior_outcome['actual'])
            prior_errors.append((prior['probability'] - actual) ** 2)
            revised_errors.append((revised['probability'] - actual) ** 2)
            evidence.extend(prior_outcome['evidence_ids'])
        if len(horizons) != 1:
            raise ValueError('paired comparison mixes horizons')
        prior_brier = sum(prior_errors) / len(prior_errors)
        revised_brier = sum(revised_errors) / len(revised_errors)
        return {
            'cases': len(prior_errors),
            'cohorts': tuple(json.loads(key) for key in sorted(arms[prior_model_version])),
            'target_definition': target_definition, 'unit': unit,
            'horizon_seconds': next(iter(horizons)), 'prior_brier': prior_brier,
            'revised_brier': revised_brier, 'prediction_improved': revised_brier < prior_brier,
            'outcome_evidence_ids': tuple(sorted(set(evidence))),
            'execution_allowed': False,
        }


def synthetic_demo() -> dict[str, object]:
    """Exercise durable measurement with invented labels, never a live forecast.

    The fixed clock deliberately simulates a day passing. Each invocation uses
    a temporary database, reopens it before resolving, then removes it. Evidence
    UUIDs are fixture references, not authenticated restaurant evidence.
    """
    from datetime import timedelta
    from tempfile import TemporaryDirectory

    issued = datetime(2026, 1, 1, tzinfo=UTC)
    horizon = issued + timedelta(days=1)
    evidence = (UUID('00000000-0000-4000-8000-000000000001'),)
    outcome_evidence = (UUID('00000000-0000-4000-8000-000000000002'),)
    tenant = 'synthetic-restaurant'
    cases = (('p1', 0.8, True), ('p2', 0.8, False),
             ('p3', 0.2, False), ('p4', 0.2, True), ('p5', 0.5, None))
    with TemporaryDirectory(prefix='orion-prediction-demo-') as directory:
        path = Path(directory) / 'predictions.db'
        ledger = PredictionLedger(path, clock=lambda: issued)
        for identity, probability, _ in cases:
            ledger.record(Prediction(
                tenant, identity, 'synthetic-stockout-within-24h-v1', 'fixture-v1',
                issued, issued, horizon, probability, evidence,
            ))
        reopened = PredictionLedger(path, clock=lambda: horizon)
        for identity, _, actual in cases:
            if actual is not None:
                reopened.resolve(Outcome(tenant, identity, horizon, actual, outcome_evidence))
        return {
            'data_source': 'synthetic-fixture',
            'persistence_reopened': True,
            **reopened.score(tenant),
        }


if __name__ == '__main__':
    print(json.dumps(synthetic_demo(), indent=2, sort_keys=True))
