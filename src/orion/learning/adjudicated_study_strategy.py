"""Versioned study calibration from externally verified, prospective outcomes.

This pure component never grants access or promotes its output. A trusted
caller must verify reviewer identity, outcome custody and source digests.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import math
import sqlite3
from collections import defaultdict
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from .prediction_ledger import Outcome, Prediction, PredictionLedger, _json

ANSWERS = ("flag", "clear", "missing")


def _digest(value: object) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                  allow_nan=False).encode()).hexdigest()


def _time(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamp must be timezone-aware")
    return value.astimezone(UTC)


def _sha(value: str) -> str:
    if not isinstance(value, str) or len(value) != 64 or any(
            character not in "0123456789abcdef" for character in value):
        raise ValueError("SHA-256 identity must be lowercase hex")
    return value


def _distribution(values: Sequence[float]) -> tuple[float, ...]:
    result = tuple(values)
    if (len(result) < 2 or any(type(value) not in (int, float)
                               or not math.isfinite(value) or not 0 <= value <= 1
                               for value in result)
            or not math.isclose(sum(result), 1.0, abs_tol=1e-9)):
        raise ValueError("invalid finite distribution")
    return tuple(float(value) for value in result)


@dataclass(frozen=True, slots=True)
class StudyObservation:
    feature: str
    answer: str
    evidence_id: UUID
    source_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.feature, str) or not self.feature:
            raise ValueError("feature identity required")
        if self.answer not in ANSWERS or not isinstance(self.evidence_id, UUID):
            raise ValueError("observation answer or evidence is invalid")
        _sha(self.source_sha256)


@dataclass(frozen=True, slots=True)
class AdjudicatedStudyCase:
    prediction: Prediction
    outcome: Outcome
    observations: tuple[StudyObservation, ...]
    reviewer_id: str
    reviewer_attestation_sha256: str

    def __post_init__(self) -> None:
        if (not isinstance(self.prediction, Prediction) or not isinstance(self.outcome, Outcome)
                or self.prediction.tenant_id != self.outcome.tenant_id
                or self.prediction.prediction_id != self.outcome.prediction_id):
            raise ValueError("adjudication prediction/outcome identity mismatch")
        if _time(self.outcome.observed_at) < _time(self.prediction.horizon_end):
            raise ValueError("outcome must follow prediction horizon")
        if not self.reviewer_id or not isinstance(self.reviewer_id, str):
            raise ValueError("reviewer identity required")
        _sha(self.reviewer_attestation_sha256)
        if (not self.observations or len({item.feature for item in self.observations})
                != len(self.observations)
                or any(item.evidence_id not in self.prediction.evidence_ids
                       for item in self.observations)):
            raise ValueError("unique pre-outcome observations must bind to prediction evidence")

    def review_digest(self) -> str:
        return _digest({
            "tenant_id": self.prediction.tenant_id,
            "prediction_id": self.prediction.prediction_id,
            "issued_at": _time(self.prediction.issued_at).isoformat(),
            "outcome_at": _time(self.outcome.observed_at).isoformat(),
            "outcome": self.outcome.actual,
            "outcome_evidence_ids": [str(value) for value in self.outcome.evidence_ids],
            "reviewer_id": self.reviewer_id,
            "observations": [
                (item.feature, item.answer, str(item.evidence_id), item.source_sha256)
                for item in self.observations
            ],
        })


@dataclass(frozen=True, slots=True)
class StudyStrategyVersion:
    tenant_id: str
    version_id: str
    parent_version_id: str | None
    prior: tuple[float, float]
    likelihoods: tuple[tuple[str, tuple[tuple[float, ...], tuple[float, ...]]], ...]
    adjudication_digests: tuple[str, ...]
    created_at: datetime

    def __post_init__(self) -> None:
        if not self.tenant_id:
            raise ValueError("tenant required")
        _sha(self.version_id)
        if self.parent_version_id is not None:
            _sha(self.parent_version_id)
        if len(_distribution(self.prior)) != 2:
            raise ValueError("binary prior required")
        if len({feature for feature, _ in self.likelihoods}) != len(self.likelihoods):
            raise ValueError("duplicate feature calibration")
        for feature, rows in self.likelihoods:
            if not feature or len(rows) != 2:
                raise ValueError("binary feature likelihood required")
            for row in rows:
                if len(_distribution(row)) != len(ANSWERS):
                    raise ValueError("answer likelihood dimensions differ")
        if len(set(self.adjudication_digests)) != len(self.adjudication_digests):
            raise ValueError("duplicate adjudication")
        for digest in self.adjudication_digests:
            _sha(digest)
        _time(self.created_at)


def base_strategy(tenant_id: str, prior: Sequence[float],
                  likelihoods: Mapping[str, Sequence[Sequence[float]]], *,
                  created_at: datetime) -> StudyStrategyVersion:
    belief = _distribution(prior)
    if len(belief) != 2:
        raise ValueError("binary prior required")
    rows = tuple((feature, tuple(_distribution(row) for row in likelihoods[feature]))
                 for feature in sorted(likelihoods))
    identity = _digest({"tenant": tenant_id, "prior": belief, "likelihoods": rows,
                        "type": "base"})
    return StudyStrategyVersion(tenant_id, identity, None, belief, rows, (), created_at)


def _committed_pair_matches(ledger: PredictionLedger, prediction: Prediction,
                            outcome: Outcome) -> bool:
    """Read the frozen ledger without changing its protected source contract."""
    with sqlite3.connect(ledger.path.resolve().as_uri() + "?mode=ro", uri=True) as connection:
        connection.execute("PRAGMA query_only=ON")
        row = connection.execute("""
            SELECT p.payload, o.payload FROM predictions p
            JOIN prediction_outcomes o
              ON p.tenant=o.tenant AND p.identity=o.identity
            WHERE p.tenant=? AND p.identity=?
        """, (prediction.tenant_id, prediction.prediction_id)).fetchone()
    return (row is not None and hmac.compare_digest(row[0], _json(prediction))
            and hmac.compare_digest(row[1], _json(outcome)))


def derive_adjudicated_strategy(
    previous: StudyStrategyVersion,
    cases: Sequence[AdjudicatedStudyCase],
    *,
    producer_id: str,
    now: datetime,
    ledger: PredictionLedger,
    reviewer_verifier: Callable[[AdjudicatedStudyCase, str], bool],
    source_verifier: Callable[[str, UUID, str], bool],
    outcome_verifier: Callable[[Outcome], bool],
) -> StudyStrategyVersion:
    """Create a new immutable version only from independently verified reviews.

    The verifier callbacks belong to the Kernel's custody and reviewer boundary.
    They must not be supplied by the selector or by untrusted source content.
    """
    if not isinstance(previous, StudyStrategyVersion) or not producer_id:
        raise ValueError("prior version and producer identity required")
    decided = _time(now)
    if len(cases) < 20:
        raise ValueError("at least 20 independently reviewed outcomes required")
    if (not isinstance(ledger, PredictionLedger) or not callable(reviewer_verifier)
            or not callable(source_verifier) or not callable(outcome_verifier)):
        raise TypeError("ledger, reviewer, source and outcome verifiers required")
    known = {feature for feature, _ in previous.likelihoods}
    counts = defaultdict(lambda: [[1, 1, 1], [1, 1, 1]])
    per_feature = defaultdict(lambda: [0, 0])
    outcomes = [0, 0]
    identities = set()
    attestations = []
    for case in cases:
        if not isinstance(case, AdjudicatedStudyCase):
            raise TypeError("adjudicated case required")
        prediction, outcome = case.prediction, case.outcome
        if (prediction.tenant_id != previous.tenant_id
                or prediction.model_version != previous.version_id
                or _time(prediction.issued_at) < _time(previous.created_at)
                or case.reviewer_id == producer_id
                or _time(outcome.observed_at) > decided
                or prediction.prediction_id in identities):
            raise ValueError("tenant, strategy, reviewer, time or replay boundary failed")
        identities.add(prediction.prediction_id)
        digest = case.review_digest()
        if not _committed_pair_matches(ledger, prediction, outcome):
            raise ValueError("prospective ledger resolution denied")
        if not reviewer_verifier(case, digest):
            raise ValueError("independent reviewer attestation denied")
        if not outcome_verifier(outcome):
            raise ValueError("outcome evidence custody denied")
        for observation in case.observations:
            if observation.feature not in known:
                raise ValueError("unverified feature cannot enter strategy")
            if not source_verifier(previous.tenant_id, observation.evidence_id,
                                   observation.source_sha256):
                raise ValueError("source evidence custody denied")
            label = int(outcome.actual)
            counts[observation.feature][label][ANSWERS.index(observation.answer)] += 1
            per_feature[observation.feature][label] += 1
        outcomes[int(outcome.actual)] += 1
        attestation = _digest((digest, case.reviewer_attestation_sha256))
        if attestation in previous.adjudication_digests:
            raise ValueError("reviewed outcome replay denied")
        attestations.append(attestation)
    if min(outcomes) < 5:
        raise ValueError("each outcome class requires five independent reviews")
    updated = []
    for feature, old_rows in previous.likelihoods:
        if min(per_feature[feature]) >= 5:
            rows = tuple(tuple(value / sum(row) for value in row)
                         for row in counts[feature])
        else:
            rows = old_rows
        updated.append((feature, rows))
    prior = ((outcomes[0] + 1) / (len(cases) + 2),
             (outcomes[1] + 1) / (len(cases) + 2))
    reviewed = tuple(sorted(attestations))
    identity = _digest({"tenant": previous.tenant_id, "parent": previous.version_id,
                        "prior": prior, "likelihoods": updated,
                        "reviews": reviewed})
    return StudyStrategyVersion(previous.tenant_id, identity, previous.version_id,
                                prior, tuple(updated), reviewed, decided)
