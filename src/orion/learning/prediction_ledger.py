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

    def __post_init__(self) -> None:
        for value in (self.tenant_id, self.prediction_id, self.target_definition, self.model_version):
            _identity(value)
        if not _time(self.evidence_cutoff) <= _time(self.issued_at) < _time(self.horizon_end):
            raise ValueError('prediction requires cutoff <= issuance < horizon')
        if (type(self.probability) not in (int, float)
                or not math.isfinite(self.probability) or not 0 <= self.probability <= 1):
            raise ValueError('probability must be finite and within [0, 1]')
        _references(self.evidence_ids)


@dataclass(frozen=True, slots=True)
class Outcome:
    tenant_id: str
    prediction_id: str
    observed_at: datetime
    actual: bool
    evidence_ids: tuple[UUID, ...]

    def __post_init__(self) -> None:
        _identity(self.tenant_id)
        _identity(self.prediction_id)
        _time(self.observed_at)
        if type(self.actual) is not bool:
            raise ValueError('actual must be bool')
        _references(self.evidence_ids)


def _json(record: Prediction | Outcome) -> str:
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
            ''')
            connection.commit()

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

    def score(self, tenant_id: str) -> dict[str, object]:
        """Report proper binary loss and fixed-threshold counts, not causal value."""
        _identity(tenant_id)
        with self._connect() as connection:
            rows = connection.execute('''
                SELECT p.payload, o.payload, p.recorded_at FROM predictions p
                LEFT JOIN prediction_outcomes o ON p.tenant=o.tenant AND p.identity=o.identity
                WHERE p.tenant=? ORDER BY p.identity
            ''', (tenant_id,)).fetchall()
        scored = [(json.loads(p), json.loads(o), datetime.fromisoformat(t))
                  for p, o, t in rows if o is not None]
        pairs = [(p['probability'], int(o['actual'])) for p, o, _ in scored]
        return {
            'predictions': len(rows), 'resolved': len(pairs), 'pending': len(rows) - len(pairs),
            'brier': sum((p-y)**2 for p, y in pairs) / len(pairs) if pairs else None,
            'true_positive': sum(p >= 0.5 and y == 1 for p, y in pairs),
            'false_positive': sum(p >= 0.5 and y == 0 for p, y in pairs),
            'true_negative': sum(p < 0.5 and y == 0 for p, y in pairs),
            'false_negative': sum(p < 0.5 and y == 1 for p, y in pairs),
            'lead_seconds': tuple((datetime.fromisoformat(p['horizon_end'])-t).total_seconds()
                                  for p, _, t in scored),
            'economic_value': None, 'execution_allowed': False,
        }
