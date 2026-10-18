"""Proposal-only finite Bayesian study selection under existing read authority.

Question likelihoods must come from separately verified calibration evidence.
This module has no acquisition, grant, persistence, promotion or execution port.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from ..discovery.governed_runner import preflight_governed_discovery
from ..discovery.planner import (
    DiscoveryAuthorization,
    DiscoveryPlan,
    DiscoveryPlanItem,
    DiscoveryTargetKind,
)
from ..understanding.metadata import MetadataUnderstanding


def _probability(value: float) -> float:
    if type(value) not in (float, int) or not math.isfinite(value) or not 0 <= value <= 1:
        raise ValueError("probability must be finite and in [0, 1]")
    return float(value)


def _distribution(values: Sequence[float]) -> tuple[float, ...]:
    result = tuple(_probability(value) for value in values)
    if len(result) < 2 or not math.isclose(sum(result), 1.0, abs_tol=1e-9):
        raise ValueError("distribution must contain at least two probabilities summing to one")
    return result


def _entropy(values: Sequence[float]) -> float:
    return -sum(value * math.log2(value) for value in values if value)


@dataclass(frozen=True, slots=True)
class QuestionModel:
    tenant_id: str
    question_id: str
    target: str
    provenance_ids: tuple[UUID, ...]
    answers: tuple[str, ...]
    likelihoods: tuple[tuple[float, ...], ...]
    calibration_evidence_ids: tuple[UUID, ...]
    request_cost: float = 1.0
    privacy_risk: float = 0.0
    operational_risk: float = 0.0
    relevance: float = 1.0

    def __post_init__(self) -> None:
        if not self.tenant_id or not self.question_id or not self.target:
            raise ValueError("question requires tenant, identity and target")
        if (not self.provenance_ids or not self.calibration_evidence_ids
                or len(set(self.provenance_ids)) != len(self.provenance_ids)
                or len(set(self.calibration_evidence_ids)) != len(self.calibration_evidence_ids)):
            raise ValueError("unique structural and calibration evidence required")
        if len(self.answers) < 2 or len(set(self.answers)) != len(self.answers):
            raise ValueError("question answers must be distinct")
        if len(self.likelihoods) < 2:
            raise ValueError("competing hypotheses required")
        for row in self.likelihoods:
            if len(row) != len(self.answers):
                raise ValueError("likelihood answer dimensions differ")
            _distribution(row)
        for name in ("request_cost", "privacy_risk", "operational_risk", "relevance"):
            value = getattr(self, name)
            if type(value) not in (float, int) or not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
        if self.request_cost == 0:
            raise ValueError("request cost must be positive")


@dataclass(frozen=True, slots=True)
class QuestionScore:
    question_id: str
    target: str
    expected_information_gain_bits: float
    request_cost: float
    privacy_risk: float
    operational_risk: float
    relevance: float
    utility: float
    rationale: str
    plan: DiscoveryPlan


def posterior(prior: Sequence[float], question: QuestionModel, answer: str) -> tuple[float, ...]:
    """Update belief from an observed answer; caller verifies source evidence."""
    belief = _distribution(prior)
    if len(belief) != len(question.likelihoods) or answer not in question.answers:
        raise ValueError("answer or hypothesis dimensions differ")
    index = question.answers.index(answer)
    joint = tuple(p * row[index] for p, row in zip(belief, question.likelihoods, strict=True))
    marginal = sum(joint)
    if marginal <= 0:
        raise ValueError("answer has zero likelihood under all hypotheses")
    return tuple(value / marginal for value in joint)


def expected_information_gain(prior: Sequence[float], question: QuestionModel) -> float:
    belief = _distribution(prior)
    if len(belief) != len(question.likelihoods):
        raise ValueError("hypothesis dimensions differ")
    conditional = 0.0
    for index, answer in enumerate(question.answers):
        probability = sum(p * row[index]
                          for p, row in zip(belief, question.likelihoods, strict=True))
        if probability:
            conditional += probability * _entropy(posterior(belief, question, answer))
    return max(0.0, _entropy(belief) - conditional)


def rank_authorized_questions(
    prior: Sequence[float],
    questions: Sequence[QuestionModel],
    *,
    understanding: MetadataUnderstanding,
    authorization: DiscoveryAuthorization,
    calibration_verifier: Callable[[UUID], bool],
    max_request_cost: float,
    max_privacy_risk: float,
    max_operational_risk: float,
) -> tuple[QuestionScore, ...]:
    """Rank eligible record questions; the governed gateway remains authoritative."""
    belief = _distribution(prior)
    ranked = []
    for question, plan in eligible_questions(
        questions, understanding=understanding, authorization=authorization,
        calibration_verifier=calibration_verifier,
        max_request_cost=max_request_cost, max_privacy_risk=max_privacy_risk,
        max_operational_risk=max_operational_risk,
    ):
        if len(question.likelihoods) != len(belief):
            raise ValueError("hypothesis dimensions differ")
        gain = expected_information_gain(belief, question)
        utility = question.relevance * gain / question.request_cost
        ranked.append(QuestionScore(
            question.question_id, question.target, gain, question.request_cost,
            question.privacy_risk, question.operational_risk, question.relevance,
            utility,
            (f"verified model: {gain:.6f} expected bits; relevance "
             f"{question.relevance:.6f}; request cost {question.request_cost:.6f}"),
            plan,
        ))
    return tuple(sorted(ranked, key=lambda row: (-row.utility, row.question_id)))


def eligible_questions(
    questions: Sequence[QuestionModel],
    *,
    understanding: MetadataUnderstanding,
    authorization: DiscoveryAuthorization,
    calibration_verifier: Callable[[UUID], bool],
    max_request_cost: float,
    max_privacy_risk: float,
    max_operational_risk: float,
) -> tuple[tuple[QuestionModel, DiscoveryPlan], ...]:
    """Apply hard governance, provenance and risk filters before any scoring."""
    if understanding.tenant_id != authorization.tenant_id:
        raise ValueError("tenant boundary mismatch")
    limits = (max_request_cost, max_privacy_risk, max_operational_risk)
    if any(type(value) not in (float, int) or not math.isfinite(value) or value < 0
           for value in limits):
        raise ValueError("risk and cost limits must be finite and nonnegative")
    if len({question.question_id for question in questions}) != len(questions):
        raise ValueError("duplicate question identity")
    eligible = []
    for question in questions:
        if question.tenant_id != authorization.tenant_id:
            raise ValueError("question crosses tenant boundary")
        if (question.request_cost > max_request_cost
                or question.privacy_risk > max_privacy_risk
                or question.operational_risk > max_operational_risk):
            continue
        plan = DiscoveryPlan(authorization.tenant_id, (DiscoveryPlanItem(
            DiscoveryTargetKind.RECORDS, question.target,
            "verified calibration model proposes a bounded read",
            question.provenance_ids,
        ),))
        # This is the same fail-closed preflight used before acquisition.
        try:
            preflight_governed_discovery(
                plan, authorization=authorization, understanding=understanding,
            )
        except ValueError:
            continue
        if not all(calibration_verifier(identity)
                   for identity in question.calibration_evidence_ids):
            raise ValueError("calibration evidence is unverified")
        eligible.append((question, plan))
    return tuple(eligible)
