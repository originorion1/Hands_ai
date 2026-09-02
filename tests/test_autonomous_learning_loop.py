import pytest

from orion.learning.autonomous_loop import (
    USEFUL_GAIN_THRESHOLD,
    AuthorizationEnvelope,
    EvidenceCoverage,
    LearningCheckpoint,
    LearningMemory,
    LearningObjective,
    MetadataStudyState,
    StudyIntent,
    StudyOpportunity,
    StudyOutcome,
    StudyStopReason,
    authorize_intent,
    discover_opportunities,
    generate_intent,
    resume_checkpoint,
    run_autonomous_loop,
)
from orion.understanding.graph import GraphStore, RelationshipType
from orion.understanding.metadata import (
    MetadataUnderstanding,
    StructuralEntity,
    StructuralField,
    project_metadata_understanding,
    relationship_target,
)


def model_a():
    fields = (StructuralField("DocumentA", "field_alpha", "Link", None, "ReferenceA", True, False, False, False), StructuralField("DocumentA", "field_gamma", "Data", None, None, False, False, False, False), StructuralField("DocumentA", "layout", "Section Break", None, None, False, True, False, False))
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


def test_evidence_bearing_entity_wins_before_lexical_tie_break():
    understanding = MetadataUnderstanding(
        "tenant-a",
        (
            StructuralEntity(
                "Alpha",
                None,
                False,
                False,
                False,
                (
                    StructuralField(
                        "Alpha", "required", "Data", None, None,
                        True, False, False, False,
                    ),
                ),
                (),
            ),
            StructuralEntity(
                "Bravo",
                None,
                False,
                False,
                False,
                (
                    StructuralField(
                        "Bravo", "related", "Link", None, "Zulu",
                        True, False, False, False,
                    ),
                ),
                (),
            ),
            StructuralEntity(
                "Zulu",
                None,
                False,
                False,
                False,
                (
                    StructuralField(
                        "Zulu", "anchor", "Data", None, None,
                        False, True, False, False,
                    ),
                    StructuralField(
                        "Zulu", "required", "Data", None, None,
                        True, False, False, False,
                    ),
                ),
                (),
            ),
        ),
    )

    opportunities = discover_opportunities(
        objective(),
        understanding,
        (
            EvidenceCoverage(
                "Zulu", "anchor", observations_seen=1,
                valid_observations=1,
            ),
        ),
    )
    selected = opportunities[0]

    assert (selected.entity, selected.fields) == ("Zulu", ("required",))
    relevance = {
        item.entity: dict(item.score_components)["relevance"]
        for item in opportunities
    }
    assert relevance == {"Alpha": 0.0, "Bravo": 0.5, "Zulu": 1.0}


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


def test_context_relevance_cannot_keep_unproductive_loop_alive():
    context_only = LearningObjective(
        "objective-1",
        "neutral boundary regression",
        aim_weights=(
            ("reduce_human_input", 0.0),
            ("increase_evidence_coverage", 1.25),
            ("increase_predictability", 0.0),
            ("reduce_uncertainty_error", 0.0),
        ),
    )
    calls = []

    def no_gain(request):
        calls.append(request)
        return StudyOutcome(
            request.intent.entity,
            request.intent.fields,
            1,
            1,
            0.0,
            0.0,
            "none",
            "INCONCLUSIVE",
        )

    result = run_autonomous_loop(
        context_only,
        model_single(),
        (),
        envelope("Solo"),
        no_gain,
    )
    remaining = discover_opportunities(
        context_only,
        model_single(),
        result.memory.coverage,
        result.memory,
    )[0]
    relevance = dict(remaining.score_components)["relevance"]

    assert remaining.score > USEFUL_GAIN_THRESHOLD
    assert remaining.score - relevance <= USEFUL_GAIN_THRESHOLD
    assert len(calls) == 1
    assert result.stop_reason is StudyStopReason.NO_INFORMATION_GAIN


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


def test_missing_field_scope_and_missing_understanding_fail_closed():
    intent = generate_intent(StudyOpportunity("DocumentA", ("field_alpha",), 1.0, (), "x"), "tenant-a", 1)
    no_fields = AuthorizationEnvelope("tenant-a", "objective-1", allowed_record_entities=frozenset({"DocumentA"}))
    with pytest.raises(ValueError):
        authorize_intent(intent, no_fields, model_a())
    scoped = AuthorizationEnvelope("tenant-a", "objective-1", allowed_record_entities=frozenset({"DocumentA"}), allowed_record_fields=(("DocumentA", ("field_alpha",)),))
    with pytest.raises(ValueError):
        authorize_intent(intent, scoped, None)
    with pytest.raises(ValueError):
        authorize_intent(intent, scoped, object())


@pytest.mark.parametrize("target", ["*", "../x", "x?y", "x#y", " x", "x ", "x\x00y"])
def test_canonical_target_rejection(target):
    with pytest.raises((ValueError, TypeError)):
        StudyIntent("tenant-a", target, ("field_alpha",), "record_evidence", 1, "h", "e", "r")


def test_rejected_request_never_calls_runner():
    calls = []
    denied = AuthorizationEnvelope("tenant-a", "objective-1", allowed_record_entities=frozenset(), allowed_record_fields=())
    result = run_autonomous_loop(objective(), model_a(), (), denied, lambda request: calls.append(request))
    assert result.stop_reason is StudyStopReason.NO_AUTHORIZED_OPPORTUNITY and calls == []


def test_repeated_positive_learning_is_bounded():
    def runner(request):
        return StudyOutcome(request.intent.entity, request.intent.fields, 1, 1, 0.8, 0.2, "low", "SUPPORTED")
    result = run_autonomous_loop(objective(), model_single(), (), envelope("Solo", cycles=3, records=10), runner)
    state = result.memory.coverage[0]
    assert 0 <= state.prior_prediction_coverage <= 1


def test_evidence_only_learning_preserves_prediction_state_and_missing_count():
    from orion.learning.autonomous_loop import _learn

    intent = StudyIntent(
        "tenant-a", "Solo", ("only",), "record_evidence", 5, "h", "e", "r"
    )
    request = authorize_intent(intent, envelope("Solo"), model_single())
    baseline = EvidenceCoverage(
        "Solo",
        "only",
        observations_seen=4,
        valid_observations=3,
        missing_count=1,
        prior_prediction_attempts=2,
        prior_prediction_coverage=0.4,
        prior_error=0.2,
    )
    outcome = StudyOutcome(
        "Solo",
        ("only",),
        3,
        2,
        0.9,
        0.8,
        "high",
        "SUPPORTED",
        prediction_evaluated=False,
    )

    learned = _learn(LearningMemory(coverage=(baseline,)), request, outcome)
    state = learned.coverage[0]

    assert (state.observations_seen, state.valid_observations, state.missing_count) == (
        7,
        5,
        2,
    )
    assert (
        state.prior_prediction_attempts,
        state.prior_prediction_coverage,
        state.prior_error,
    ) == (2, 0.4, 0.2)
    assert state.study_count == 1
    assert learned.attempted == (("Solo", "only"),)


def test_all_missing_repeated_evidence_only_learning_is_bounded_and_deterministic():
    from orion.learning.autonomous_loop import _learn

    intent = StudyIntent(
        "tenant-a", "Solo", ("only",), "record_evidence", 2, "h", "e", "r"
    )
    request = authorize_intent(intent, envelope("Solo"), model_single())
    outcome = StudyOutcome(
        "Solo",
        ("only",),
        2,
        0,
        1.0,
        1.0,
        "high",
        "SUPPORTED",
        prediction_evaluated=False,
    )

    first = _learn(LearningMemory(), request, outcome)
    repeated = _learn(first, request, outcome)
    replayed = _learn(_learn(LearningMemory(), request, outcome), request, outcome)
    state = repeated.coverage[0]

    assert repeated == replayed
    assert (state.observations_seen, state.valid_observations, state.missing_count) == (
        4,
        0,
        4,
    )
    assert (
        state.prior_prediction_attempts,
        state.prior_prediction_coverage,
        state.prior_error,
    ) == (0, 0.0, None)
    assert state.study_count == 2


def test_prediction_evaluated_learning_remains_backward_compatible():
    from orion.learning.autonomous_loop import _learn

    intent = StudyIntent(
        "tenant-a", "Solo", ("only",), "record_evidence", 2, "h", "e", "r"
    )
    request = authorize_intent(intent, envelope("Solo"), model_single())
    baseline = EvidenceCoverage(
        "Solo",
        "only",
        observations_seen=2,
        valid_observations=1,
        missing_count=1,
        prior_prediction_attempts=1,
        prior_prediction_coverage=0.4,
        prior_error=0.2,
    )
    outcome = StudyOutcome(
        "Solo", ("only",), 2, 1, 0.5, 0.5, "medium", "SUPPORTED"
    )

    state = _learn(
        LearningMemory(coverage=(baseline,)), request, outcome
    ).coverage[0]

    assert (state.observations_seen, state.valid_observations) == (4, 2)
    assert state.missing_count == 1
    assert state.prior_prediction_attempts == 2
    assert state.prior_prediction_coverage == pytest.approx(0.7)
    assert state.prior_error == 0.2


def test_metadata_learning_resolves_and_penalizes():
    intent = StudyIntent("tenant-a", "ReferenceA", (), "metadata_gap", 0, "h", "e", "r")
    auth = AuthorizationEnvelope("tenant-a", "objective-1", allowed_metadata_entities=frozenset({"ReferenceA"}), allowed_record_entities=frozenset({"DocumentA"}), allowed_record_fields=(("DocumentA", ("field_alpha",)),))
    request = authorize_intent(intent, auth, model_a())
    from orion.learning.autonomous_loop import _learn
    memory = _learn(LearningMemory(), request, StudyOutcome("ReferenceA", (), 0, 0, 0.0, 0.0, "high", "SUPPORTED", study_kind="metadata_gap"))
    assert memory.metadata[0].resolved is True


def test_metadata_budget_and_unauthorized_target():
    calls = []
    auth = AuthorizationEnvelope("tenant-a", "objective-1", allowed_metadata_entities=frozenset(), max_metadata_targets=1)
    result = run_autonomous_loop(objective(), model_a(), (), auth, lambda request: calls.append(request))
    assert not calls and result.stop_reason in {StudyStopReason.NO_AUTHORIZED_OPPORTUNITY, StudyStopReason.EXHAUSTED}


def test_runner_firewall_rejects_mismatched_kind_and_nonfinite_values():
    def bad(request):
        return StudyOutcome(request.intent.entity, request.intent.fields, 1, 1, float("nan"), 0.0, "high", "SUPPORTED", study_kind="metadata_gap")
    with pytest.raises(ValueError):
        run_autonomous_loop(objective(), model_single(), (), envelope("Solo"), bad)
    with pytest.raises(ValueError):
        StudyOutcome("Solo", ("only",), True, 0, 0.0, 0.0, "high", "SUPPORTED")


def test_checkpoint_rejects_narrowed_record_scope_and_coverage():
    memory = LearningMemory(attempted=(("DocumentA", "field_alpha"),), coverage=(EvidenceCoverage("DocumentA", "field_alpha"),))
    checkpoint = LearningCheckpoint(1, "tenant-a", "objective-1", 1, memory)
    narrowed = AuthorizationEnvelope("tenant-a", "objective-1", allowed_record_entities=frozenset({"DocumentA"}), allowed_record_fields=(("DocumentA", ("field_gamma",)),))
    with pytest.raises(ValueError):
        resume_checkpoint(checkpoint, narrowed)


def test_two_unrelated_real_schemas_use_same_planner():
    assert discover_opportunities(objective(), model_b(), ())[0].entity == "RecordB"
    assert discover_opportunities(objective(), model_single(), ())[0].entity == "Solo"


def relationship_model(fieldtype, options, *, include_target=False):
    source = StructuralEntity(
        "Choice",
        None,
        False,
        False,
        False,
        (
            StructuralField(
                "Choice",
                "value",
                fieldtype,
                None,
                options,
                False,
                False,
                False,
                False,
            ),
        ),
        (),
    )
    entities = (source,)
    if include_target:
        entities += (
            StructuralEntity(
                options,
                None,
                False,
                False,
                False,
                (),
                (),
            ),
        )
    return MetadataUnderstanding("tenant-a", entities)


@pytest.mark.parametrize("fieldtype", ["Table", "Table MultiSelect"])
def test_unresolved_table_relationship_is_a_metadata_gap(fieldtype):
    opportunities = discover_opportunities(
        objective(),
        relationship_model(fieldtype, "Missing Child"),
        (),
    )

    assert any(
        item.study_kind == "metadata_gap"
        and item.entity == "Missing Child"
        for item in opportunities
    )


@pytest.mark.parametrize("fieldtype", ["Link", "Table"])
def test_already_understood_relationship_target_is_not_a_metadata_gap(
    fieldtype,
):
    opportunities = discover_opportunities(
        objective(),
        relationship_model(
            fieldtype,
            "Understood Target",
            include_target=True,
        ),
        (),
    )

    assert not any(
        item.study_kind == "metadata_gap"
        and item.entity == "Understood Target"
        for item in opportunities
    )


def test_select_multiline_options_are_not_a_relationship_and_do_not_crash():
    understanding = relationship_model("Select", "Alpha\nBeta")

    assert relationship_target(understanding.entities[0].fields[0]) is None
    opportunities = discover_opportunities(objective(), understanding, ())
    assert not any(item.study_kind == "metadata_gap" for item in opportunities)


def test_data_arbitrary_options_are_not_a_relationship():
    understanding = relationship_model("Data", "Not A DocType Target")

    assert relationship_target(understanding.entities[0].fields[0]) is None
    opportunities = discover_opportunities(objective(), understanding, ())
    assert not any(item.study_kind == "metadata_gap" for item in opportunities)


@pytest.mark.parametrize("options", [object(), 42, ("not", "text")])
def test_malformed_non_relationship_options_are_ignored_safely(options):
    understanding = relationship_model("Data", options)

    assert relationship_target(understanding.entities[0].fields[0]) is None
    assert discover_opportunities(objective(), understanding, ())


def test_graph_projection_and_autonomous_planner_share_relationship_semantics():
    source = StructuralEntity(
        "Choice",
        None,
        False,
        False,
        False,
        (
            StructuralField(
                "Choice",
                "owner",
                "Link",
                None,
                "User",
                False,
                False,
                False,
                False,
            ),
            StructuralField(
                "Choice",
                "status",
                "Select",
                None,
                "Open\nClosed",
                False,
                False,
                False,
                False,
            ),
        ),
        (),
    )
    target = StructuralEntity(
        "User", None, False, False, False, (), ()
    )
    understanding = MetadataUnderstanding("tenant-a", (source, target))
    graph = GraphStore()

    report = project_metadata_understanding(graph, understanding)
    relationships = tuple(
        graph.get_relationship(item, tenant_id="tenant-a")
        for item in report.relationship_ids
    )
    opportunities = discover_opportunities(objective(), understanding, ())

    assert sum(
        item is not None
        and item.relationship_type is RelationshipType.RELATES_TO
        for item in relationships
    ) == 1
    assert not any(item.study_kind == "metadata_gap" for item in opportunities)


@pytest.mark.parametrize("fieldtype", ["Select", "Data"])
def test_non_relationship_options_do_not_create_relevance_edges(fieldtype):
    source = StructuralEntity(
        "Source", None, False, False, False,
        (StructuralField("Source", "choice", fieldtype, None, "Anchor", False, False, False, False),),
        (),
    )
    anchor = StructuralEntity(
        "Anchor", None, False, False, False,
        (StructuralField("Anchor", "seen", "Data", None, None, False, True, False, False),),
        (),
    )
    understanding = MetadataUnderstanding("tenant-a", (source, anchor))

    opportunity = next(
        item
        for item in discover_opportunities(
            objective(), understanding,
            (EvidenceCoverage("Anchor", "seen", 1, 1),),
        )
        if item.entity == "Source"
    )

    assert relationship_target(source.fields[0]) is None
    assert dict(opportunity.score_components)["relevance"] == 0.0


def test_cycles_and_multiple_anchors_use_nearest_distance_deterministically():
    def linked(name, target, *, anchor=False):
        fields = [
            StructuralField(name, "next", "Link", None, target, False, False, False, False)
        ]
        if anchor:
            fields.append(
                StructuralField(name, "seen", "Data", None, None, False, True, False, False)
            )
        return StructuralEntity(name, None, False, False, False, tuple(fields), ())

    understanding = MetadataUnderstanding(
        "tenant-a",
        (
            linked("A", "B", anchor=True),
            linked("B", "C"),
            linked("C", "A"),
            linked("D", "E"),
            linked("E", "F"),
            linked("F", "F", anchor=True),
        ),
    )
    coverage = (
        EvidenceCoverage("A", "seen", 1, 1),
        EvidenceCoverage("F", "seen", 1, 1),
    )

    first = discover_opportunities(objective(), understanding, coverage)
    second = discover_opportunities(objective(), understanding, coverage)
    relevance = {
        item.entity: dict(item.score_components)["relevance"]
        for item in first
        if item.study_kind == "record_evidence"
    }

    assert first == second
    assert relevance == {
        "A": 1.0, "B": 0.5, "C": 0.5,
        "D": 1 / 3, "E": 0.5, "F": 1.0,
    }


def test_no_anchor_scores_and_existing_components_are_preserved():
    understanding = MetadataUnderstanding(
        "tenant-a",
        (
            StructuralEntity(
                "Only", None, False, False, False,
                (
                    StructuralField("Only", "required", "Data", None, None, True, False, False, False),
                    StructuralField("Only", "optional", "Data", None, None, False, False, False, False),
                    StructuralField("Only", "attempted", "Data", None, None, False, False, False, False),
                ),
                (),
            ),
        ),
    )
    coverage = (EvidenceCoverage("Only", "attempted", study_count=1),)
    memory = LearningMemory(attempted=(("Only", "attempted"),))

    opportunities = discover_opportunities(
        objective(), understanding, coverage, memory
    )
    components = {
        item.fields[0]: dict(item.score_components) for item in opportunities
    }

    assert [item.fields for item in opportunities] == [
        ("required",), ("optional",), ("attempted",),
    ]
    assert {
        item.fields[0]: item.score for item in opportunities
    } == {"required": 6.0, "optional": 4.5, "attempted": 2.5}
    assert components["required"] == {
        "gap": 3.0, "importance": 2.0, "relevance": 0.0, "penalty": 0.0,
    }
    assert components["optional"]["importance"] == 0.5
    assert components["attempted"]["penalty"] == -2.0


def test_metadata_gap_inherits_only_source_relevance_and_penalty():
    understanding = relationship_model("Link", "Unknown Target")
    source_coverage = (EvidenceCoverage("Choice", "value", 1, 1),)
    attempted = LearningMemory(
        metadata=(MetadataStudyState("Unknown Target", study_count=2),)
    )

    relevant = next(
        item for item in discover_opportunities(
            objective(), understanding, source_coverage, attempted
        )
        if item.study_kind == "metadata_gap"
    )
    target_only = next(
        item for item in discover_opportunities(
            objective(), understanding,
            (EvidenceCoverage("Unknown Target", "invented", 1, 1),),
        )
        if item.study_kind == "metadata_gap"
    )

    assert dict(relevant.score_components) == {
        "metadata_gap": 1.0, "relevance": 1.0, "penalty": -1.0,
    }
    assert relevant.score == 1.0
    assert dict(target_only.score_components)["relevance"] == 0.0
