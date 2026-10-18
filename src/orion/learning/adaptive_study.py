"""Proposal-only decision-risk study selection with hard governed preflight.

This research policy uses the existing 0.2/0.8 selective-decision thresholds
as losses. It cannot acquire evidence, grant access, revise a model, or act.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from uuid import UUID

from ..discovery.planner import DiscoveryAuthorization, DiscoveryPlan
from ..understanding.metadata import MetadataUnderstanding
from .epistemic_selection import (
    QuestionModel,
    eligible_questions,
    expected_information_gain,
    posterior,
)


def _belief(prior: Sequence[float]) -> tuple[float, float]:
    if (len(prior) != 2 or any(type(value) not in (float, int)
                                or not math.isfinite(value) or value < 0 or value > 1
                                for value in prior)
            or not math.isclose(sum(prior), 1.0, abs_tol=1e-9)):
        raise ValueError("binary belief must be a finite distribution")
    return float(prior[0]), float(prior[1])


def decision_loss(prior: Sequence[float]) -> float:
    """Minimum loss for candidate, no anomaly or abstain at frozen thresholds."""
    no_issue, issue = _belief(prior)
    return min(no_issue, issue, 0.2)


def expected_decision_gain(prior: Sequence[float], question: QuestionModel) -> float:
    belief = _belief(prior)
    if len(question.likelihoods) != 2:
        raise ValueError("two hypotheses required")
    expected_loss = 0.0
    for index, answer in enumerate(question.answers):
        chance = sum(belief[hypothesis] * question.likelihoods[hypothesis][index]
                     for hypothesis in (0, 1))
        if chance:
            expected_loss += chance * decision_loss(posterior(belief, question, answer))
    return max(0.0, decision_loss(belief) - expected_loss)


@dataclass(frozen=True, slots=True)
class AdaptiveQuestionScore:
    question_id: str
    target: str
    expected_decision_gain: float
    expected_information_gain_bits: float
    utility: float
    rationale: str
    plan: DiscoveryPlan


def rank_adaptive_questions(
    prior: Sequence[float],
    questions: Sequence[QuestionModel],
    *,
    understanding: MetadataUnderstanding,
    authorization: DiscoveryAuthorization,
    calibration_verifier: Callable[[UUID], bool],
    max_request_cost: float,
    max_privacy_risk: float,
    max_operational_risk: float,
) -> tuple[AdaptiveQuestionScore, ...]:
    """Rank only preflighted questions; give zero authority to learned scores."""
    belief = _belief(prior)
    ranked = []
    for question, plan in eligible_questions(
        questions, understanding=understanding, authorization=authorization,
        calibration_verifier=calibration_verifier, max_request_cost=max_request_cost,
        max_privacy_risk=max_privacy_risk, max_operational_risk=max_operational_risk,
    ):
        gain = expected_decision_gain(belief, question)
        information = expected_information_gain(belief, question)
        utility = question.relevance * gain / question.request_cost
        ranked.append(AdaptiveQuestionScore(
            question.question_id, question.target, gain, information, utility,
            (f"verified decision-risk reduction {gain:.6f}; expected information "
             f"{information:.6f} bits; request cost {question.request_cost:.6f}"),
            plan,
        ))
    return tuple(sorted(ranked, key=lambda row: (-row.utility,
                                                -row.expected_information_gain_bits,
                                                row.question_id)))
