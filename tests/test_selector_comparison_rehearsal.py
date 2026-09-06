"""Paired offline experiment: only the historical missing-evidence term changes."""

from collections import Counter

from orion.learning import autonomous_loop as loop
from orion.understanding.metadata import (
    MetadataUnderstanding,
    StructuralEntity,
    StructuralField,
)


def test_paired_missing_evidence_rehearsal_uses_identical_inputs(monkeypatch):
    names = ("alpha", "beta", "gamma")
    fields = tuple(
        StructuralField("SyntheticDocument", name, "Data", None, None,
                        False, False, False, False)
        for name in names
    )
    understanding = MetadataUnderstanding(
        "synthetic-tenant",
        (StructuralEntity("SyntheticDocument", None, False, False, False, fields, ()),),
    )
    objective = loop.LearningObjective("comparison", "compare synthetic field coverage")
    authorization = loop.AuthorizationEnvelope(
        "synthetic-tenant",
        objective_id="comparison",
        allowed_record_entities=frozenset({"SyntheticDocument"}),
        allowed_record_fields=(("SyntheticDocument", names),),
        max_cycles=20,
        max_records_per_proposal=100,
        max_cumulative_records=2000,
    )

    def missing(request):
        return loop.StudyOutcome(
            request.intent.entity, request.intent.fields, 100, 0,
            0.0, 0.0, "none", "INCONCLUSIVE", prediction_evaluated=False,
        )

    fixed = loop.run_autonomous_loop(objective, understanding, (), authorization, missing)
    # Exact former scoring contribution; never used by the production runner.
    with monkeypatch.context() as old:
        old.setattr(loop, "_missing_evidence_gap", lambda state: state.missing_count * 0.1)
        previous = loop.run_autonomous_loop(
            objective, understanding, (), authorization, missing,
        )

    old_counts = Counter(intent.fields[0] for intent in previous.intents)
    fixed_counts = Counter(intent.fields[0] for intent in fixed.intents)
    assert old_counts == {"alpha": 20}
    assert fixed_counts == {"alpha": 4, "beta": 4, "gamma": 4}
    assert previous.stop_reason is loop.StudyStopReason.CYCLE_LIMIT
    assert fixed.stop_reason is loop.StudyStopReason.NO_INFORMATION_GAIN
    for result in (previous, fixed):
        assert all(
            not outcome.execution_allowed
            and not outcome.recommendation_allowed
            and not outcome.promotion_allowed
            and outcome.hypothesis_state == "INCONCLUSIVE"
            for outcome in result.outcomes
        )
