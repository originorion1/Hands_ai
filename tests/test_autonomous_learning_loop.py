from types import SimpleNamespace

import pytest

from orion.learning.autonomous_loop import (
    AuthorizationEnvelope,
    EvidenceCoverage,
    LearningCheckpoint,
    LearningMemory,
    LearningObjective,
    StudyOutcome,
    StudyStopReason,
    authorize_intent,
    discover_opportunities,
    generate_intent,
    resume_checkpoint,
    run_autonomous_loop,
)


def model_a():
    return SimpleNamespace(entities=(SimpleNamespace(doctype="DocumentA", fields=(SimpleNamespace(fieldname="field_alpha", required=True, read_only=False, hidden=False, options="ReferenceA"), SimpleNamespace(fieldname="field_gamma", required=False, read_only=False, hidden=False, options=None), SimpleNamespace(fieldname="layout", required=False, read_only=True, hidden=False, options=None))),))


def model_b():
    return SimpleNamespace(entities=(SimpleNamespace(doctype="RecordB", fields=(SimpleNamespace(fieldname="value_beta", required=False, read_only=False, hidden=False, options=None),)),))


def model_single():
    return SimpleNamespace(entities=(SimpleNamespace(doctype="Solo", fields=(SimpleNamespace(fieldname="only", required=False, read_only=False, hidden=False, options=None),)),))


def objective():
    return LearningObjective("objective-1", "reduce first-level human data entry", ("less manual entry",), ("no automatic action",))


def envelope(entity="DocumentA", cycles=2, records=10):
    return AuthorizationEnvelope("tenant-a", objective_id="objective-1", allowed_metadata_entities=frozenset({entity}), allowed_record_entities=frozenset({entity}), max_cycles=cycles, max_cumulative_records=records, max_records_per_proposal=5)


def test_objective_and_planner_are_deterministic_and_generic():
    coverage = (EvidenceCoverage("DocumentA", "field_alpha"),)
    assert discover_opportunities(objective(), model_a(), coverage) == discover_opportunities(objective(), model_a(), coverage)
    assert discover_opportunities(objective(), model_a(), coverage)[0].fields == ("field_alpha",)
    assert discover_opportunities(objective(), model_b(), ()) [0].entity == "RecordB"


def test_required_unobserved_and_low_coverage_raise_priority():
    high = discover_opportunities(objective(), model_a(), (EvidenceCoverage("DocumentA", "field_alpha"),))
    low = discover_opportunities(objective(), model_a(), (EvidenceCoverage("DocumentA", "field_alpha", observations_seen=10, prior_prediction_coverage=1.0),))
    assert high[0].score > low[0].score


def test_authorization_rejects_wrong_scope_wildcards_and_budget():
    intent = generate_intent(discover_opportunities(objective(), model_a(), ())[0], "tenant-a", 5)
    assert authorize_intent(intent, envelope())
    with pytest.raises(ValueError): authorize_intent(intent, envelope("Other"))
    with pytest.raises(ValueError): authorize_intent(intent.__class__("tenant-a", "../bad", ("x",), "record_evidence", 1, "h", "e", "r"), envelope())
    with pytest.raises(ValueError): authorize_intent(intent.__class__("tenant-a", "DocumentA", ("field_alpha",), "record_evidence", 99, "h", "e", "r"), envelope())


def test_loop_chooses_next_target_without_manual_sequence_and_updates_memory():
    calls = []
    def runner(request):
        calls.append(request)
        return StudyOutcome(request.intent.entity, request.intent.fields, 2, 2, 1.0, 1.0, "high", "SUPPORTED")
    result = run_autonomous_loop(objective(), model_a(), (), envelope(cycles=2, records=10), runner)
    assert len(calls) == 2 and result.intents[0].entity == "DocumentA" and result.intents[1].fields != result.intents[0].fields
    assert result.memory.attempted


def test_loop_stops_explicitly_for_gain_budget_and_conflict():
    def low(request): return StudyOutcome(request.intent.entity, request.intent.fields, 1, 1, 0.0, 0.0, "none", "INCONCLUSIVE")
    low_run = run_autonomous_loop(objective(), model_a(), (), envelope(), low)
    assert low_run.stop_reason is StudyStopReason.CYCLE_LIMIT and len(low_run.intents) == 2
    def conflict(request): return StudyOutcome(request.intent.entity, request.intent.fields, 1, 0, 0.0, 0.0, "high", "INCONCLUSIVE", conflict=True)
    assert run_autonomous_loop(objective(), model_a(), (), envelope(), conflict).stop_reason is StudyStopReason.CONFLICT
    def many(req): return StudyOutcome(req.intent.entity, req.intent.fields, 5, 5, 1.0, 1.0, "high", "SUPPORTED")
    assert run_autonomous_loop(objective(), model_a(), (), envelope(cycles=3, records=5), many).stop_reason is StudyStopReason.EVIDENCE_BUDGET_LIMIT
    assert run_autonomous_loop(objective(), model_single(), (), envelope("Solo"), low).stop_reason is StudyStopReason.NO_INFORMATION_GAIN


def test_checkpoint_restores_memory_but_requires_fresh_authorization():
    memory = LearningMemory(attempted=(("DocumentA", "field_alpha"),))
    checkpoint = LearningCheckpoint(1, "tenant-a", "objective-1", 1, memory)
    assert resume_checkpoint(checkpoint, envelope()).attempted == memory.attempted
    with pytest.raises(ValueError): resume_checkpoint(checkpoint, envelope("RecordB"))


def test_safety_flags_never_grant_authority():
    outcome = StudyOutcome("DocumentA", ("field_alpha",), 1, 1, 1, 1, "high", "SUPPORTED")
    assert not outcome.recommendation_allowed and not outcome.promotion_allowed and not outcome.execution_allowed
