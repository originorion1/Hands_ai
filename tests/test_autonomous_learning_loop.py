import pytest

from orion.learning.autonomous_loop import (
    AuthorizationEnvelope,
    EvidenceCoverage,
    LearningCheckpoint,
    LearningMemory,
    LearningObjective,
    StudyOpportunity,
    StudyOutcome,
    StudyStopReason,
    authorize_intent,
    discover_opportunities,
    generate_intent,
    resume_checkpoint,
    run_autonomous_loop,
)
from orion.understanding.metadata import MetadataUnderstanding, StructuralEntity, StructuralField


def model_a():
    fields = (StructuralField("DocumentA", "field_alpha", "Data", None, "ReferenceA", True, False, False, False), StructuralField("DocumentA", "field_gamma", "Data", None, None, False, False, False, False), StructuralField("DocumentA", "layout", "Section Break", None, None, False, True, False, False))
    return MetadataUnderstanding("tenant-a", (StructuralEntity("DocumentA", None, False, False, False, fields, ()),))


def model_b():
    return MetadataUnderstanding("tenant-a", (StructuralEntity("RecordB", None, False, False, False, (StructuralField("RecordB", "value_beta", "Data", None, None, False, False, False, False),), ()),))


def model_single():
    return MetadataUnderstanding("tenant-a", (StructuralEntity("Solo", None, False, False, False, (StructuralField("Solo", "only", "Data", None, None, False, False, False, False),), ()),))


def objective():
    return LearningObjective("objective-1", "reduce first-level human data entry", ("less manual entry",), ("no automatic action",))


def envelope(entity="DocumentA", cycles=2, records=10):
    fields = ("field_alpha", "field_gamma") if entity == "DocumentA" else (("value_beta",) if entity == "RecordB" else ("only",))
    return AuthorizationEnvelope("tenant-a", objective_id="objective-1", allowed_metadata_entities=frozenset({entity, "ReferenceA"}), allowed_record_entities=frozenset({entity}), allowed_record_fields=((entity, fields),), max_cycles=cycles, max_cumulative_records=records, max_records_per_proposal=5)


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
    assert authorize_intent(intent, envelope(), model_a())
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


def test_baseline_and_resumed_coverage_are_merged():
    baseline = (EvidenceCoverage("DocumentA", "field_alpha", observations_seen=100, valid_observations=100),)
    memory = LearningMemory(coverage=(EvidenceCoverage("DocumentA", "field_alpha", observations_seen=10, valid_observations=10, study_count=1),))
    def runner(req):
        return StudyOutcome(req.intent.entity, req.intent.fields, 0, 0, 0.0, 0.0, "none", "INCONCLUSIVE")
    result = run_autonomous_loop(objective(), model_a(), baseline, envelope(cycles=1), runner, memory=memory)
    state = next(c for c in result.memory.coverage if c.entity == "DocumentA" and c.field == "field_alpha")
    assert state.observations_seen == 110


def test_conflicting_or_duplicate_aggregate_state_fails_closed():
    duplicate = (EvidenceCoverage("DocumentA", "field_alpha", observations_seen=10),)
    with pytest.raises(ValueError):
        run_autonomous_loop(objective(), model_a(), duplicate, envelope(cycles=1), lambda _: None, memory=LearningMemory(coverage=duplicate))


def test_structured_objective_changes_generic_ranking():
    coverage = (EvidenceCoverage("DocumentA", "field_alpha", observations_seen=5, prior_prediction_coverage=0.1), EvidenceCoverage("DocumentA", "field_gamma", observations_seen=0))
    coverage_first = LearningObjective("objective-1", "x", aim_weights=(("reduce_human_input", 0.0), ("increase_evidence_coverage", 3.0), ("increase_predictability", 0.0), ("reduce_uncertainty_error", 0.0)))
    predict_first = LearningObjective("objective-1", "x", aim_weights=(("reduce_human_input", 0.0), ("increase_evidence_coverage", 0.0), ("increase_predictability", 3.0), ("reduce_uncertainty_error", 0.0)))
    assert discover_opportunities(coverage_first, model_a(), coverage)[0].fields != discover_opportunities(predict_first, model_a(), coverage)[0].fields


def test_zero_records_is_record_evidence_and_metadata_gap_is_unresolved_relation():
    ops = discover_opportunities(objective(), model_a(), (EvidenceCoverage("DocumentA", "field_alpha"),))
    assert next(o for o in ops if o.fields == ("field_alpha",)).study_kind == "record_evidence"
    assert next(o for o in ops if o.entity == "ReferenceA").study_kind == "metadata_gap"


def test_field_scope_is_explicit_and_understanding_is_tenant_bound():
    scoped = AuthorizationEnvelope("tenant-a", objective_id="objective-1", allowed_record_entities=frozenset({"DocumentA"}), allowed_record_fields=(("DocumentA", ("field_alpha",)),))
    intent = generate_intent(StudyOpportunity("DocumentA", ("field_gamma",), 1, (), "x"), "tenant-a", 1)
    with pytest.raises(ValueError): authorize_intent(intent, scoped, model_a())
