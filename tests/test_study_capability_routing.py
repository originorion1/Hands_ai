import json
from urllib.parse import parse_qs, urlsplit

import pytest

from orion.contracts import Evidence, EvidenceKind, Observation, ObservationMode
from orion.discovery.erpnext_study_router import run_erpnext_governed_study
from orion.learning.autonomous_loop import (
    AuthorizationEnvelope,
    LearningObjective,
    StudyIntent,
    StudyOpportunity,
    StudyOutcome,
    StudyStopReason,
    UnsupportedStudyCapabilityError,
    authorize_intent,
    generate_intent,
)
from orion.learning.study_capability import (
    StudyCapability,
    derive_study_capability,
    run_governed_study_cycles,
    run_routed_governed_record_evidence,
)
from orion.understanding.metadata import (
    MetadataUnderstanding,
    StructuralEntity,
    StructuralField,
)

TENANT = "synthetic-tenant"
COMPANY = "Synthetic Company"


def field(
    entity: str,
    name: str,
    fieldtype: str = "Data",
    *,
    options: str | None = None,
    hidden: bool = False,
    read_only: bool = False,
) -> StructuralField:
    return StructuralField(
        entity,
        name,
        fieldtype,
        None,
        options,
        False,
        read_only,
        hidden,
        False,
    )


def entity(
    name: str,
    selected: StructuralField,
    *,
    submittable: bool = False,
    child: bool = False,
    single: bool = False,
    company_scoped: bool = True,
) -> StructuralEntity:
    fields = (selected, field(name, "company")) if company_scoped else (selected,)
    return StructuralEntity(name, None, submittable, child, single, fields, ())


def understanding(*entities: StructuralEntity) -> MetadataUnderstanding:
    return MetadataUnderstanding(TENANT, entities)


def intent(
    entity_name: str,
    field_name: str = "value",
    *,
    records: int = 2,
    study_kind: str = "record_evidence",
) -> StudyIntent:
    fields = () if study_kind == "metadata_gap" else (field_name,)
    requested = 0 if study_kind == "metadata_gap" else records
    return StudyIntent(
        TENANT,
        entity_name,
        fields,
        study_kind,
        requested,
        "synthetic hypothesis",
        "synthetic evidence",
        "synthetic rationale",
    )


def envelope(
    scopes: tuple[tuple[str, tuple[str, ...]], ...],
    *,
    cycles: int = 3,
    per_study: int = 2,
    cumulative: int = 6,
) -> AuthorizationEnvelope:
    return AuthorizationEnvelope(
        TENANT,
        "objective",
        allowed_metadata_entities=frozenset({"Missing Entity"}),
        allowed_record_entities=frozenset(name for name, _ in scopes),
        allowed_record_fields=scopes,
        max_cycles=cycles,
        max_records_per_proposal=per_study,
        max_cumulative_records=cumulative,
    )


def observation(entity_name: str, field_name: str, value: object, index: int = 1):
    return Observation(
        Evidence(
            kind=EvidenceKind.API,
            source="synthetic-read-only",
            tenant_id=TENANT,
            payload={
                "resource": entity_name,
                "record": {"name": f"record-{index}", field_name: value},
            },
        ),
        ObservationMode.READ_ONLY,
    )


def test_capability_derivation_uses_structure_not_entity_names():
    ordinary = entity("Neutral A", field("Neutral A", "value"))
    submitted = entity(
        "Neutral B", field("Neutral B", "value"), submittable=True
    )
    collection = entity(
        "Neutral C",
        field("Neutral C", "items", "Table", options="Neutral Child"),
    )
    child = entity("Neutral D", field("Neutral D", "value"), child=True)
    single = entity("Neutral E", field("Neutral E", "value"), single=True)
    hidden = entity("Neutral F", field("Neutral F", "value", hidden=True))
    read_only = entity(
        "Neutral G", field("Neutral G", "value", read_only=True)
    )
    duplicate_one = entity("Neutral H", field("Neutral H", "value"))
    duplicate_two = entity("Neutral H", field("Neutral H", "value"))
    model = understanding(
        ordinary,
        submitted,
        collection,
        child,
        single,
        hidden,
        read_only,
        duplicate_one,
        duplicate_two,
    )

    assert derive_study_capability(intent("Neutral A"), model) is StudyCapability.ORDINARY_RECORD
    assert (
        derive_study_capability(intent("Neutral B"), model)
        is StudyCapability.SUBMITTED_DOCUMENT
    )
    assert (
        derive_study_capability(intent("Neutral C", "items"), model)
        is StudyCapability.COLLECTION_RELATIONSHIP
    )
    assert (
        derive_study_capability(
            intent("Missing Entity", study_kind="metadata_gap"), model
        )
        is StudyCapability.METADATA_STUDY
    )
    assert derive_study_capability(intent("Neutral D"), model) is StudyCapability.UNSUPPORTED
    assert derive_study_capability(intent("Neutral E"), model) is StudyCapability.UNSUPPORTED
    assert derive_study_capability(intent("Neutral F"), model) is StudyCapability.UNSUPPORTED
    assert derive_study_capability(intent("Neutral G"), model) is StudyCapability.UNSUPPORTED
    assert derive_study_capability(intent("Neutral H"), model) is StudyCapability.UNSUPPORTED
    assert derive_study_capability(intent("Unknown"), model) is StudyCapability.UNSUPPORTED


def test_routed_reader_receives_exact_authorized_scope_without_substitution():
    model = understanding(entity("Neutral A", field("Neutral A", "value")))
    limits = envelope((("Neutral A", ("value",)),), per_study=7, cumulative=7)
    request = authorize_intent(intent("Neutral A", records=7), limits, model)
    calls = []

    def reader(resource, fields, requested_records):
        calls.append((resource, fields, requested_records))
        return (observation(resource, fields[0], 0),)

    outcome = run_routed_governed_record_evidence(
        request,
        envelope=limits,
        understanding=model,
        readers={StudyCapability.ORDINARY_RECORD: reader},
    )

    assert calls == [("Neutral A", ("value",), 7)]
    assert outcome.prediction_evaluated is False
    assert outcome.coverage_change == outcome.uncertainty_reduction == 0.0
    assert not outcome.recommendation_allowed
    assert not outcome.promotion_allowed
    assert not outcome.execution_allowed


def test_authorization_and_unsupported_semantics_fail_before_reader():
    ordinary = entity("Neutral A", field("Neutral A", "value"))
    collection = entity(
        "Neutral C",
        field("Neutral C", "items", "Table", options="Neutral Child"),
    )
    model = understanding(ordinary, collection)
    denied = envelope((("Neutral A", ("other",)),))
    calls = []

    with pytest.raises(ValueError, match="explicitly authorized"):
        run_routed_governed_record_evidence(
            authorize_intent(intent("Neutral A"), envelope((("Neutral A", ("value",)),)), model),
            envelope=denied,
            understanding=model,
            readers={StudyCapability.ORDINARY_RECORD: lambda *args: calls.append(args)},
        )

    collection_limits = envelope((("Neutral C", ("items",)),))
    collection_request = authorize_intent(
        intent("Neutral C", "items"), collection_limits, model
    )
    with pytest.raises(UnsupportedStudyCapabilityError, match="collection"):
        run_routed_governed_record_evidence(
            collection_request,
            envelope=collection_limits,
            understanding=model,
            readers={StudyCapability.ORDINARY_RECORD: lambda *args: calls.append(args)},
        )

    assert calls == []


def test_generate_intent_does_not_clamp_requested_bound():
    generated = generate_intent(
        StudyOpportunity("Neutral A", ("value",), 1.0, (), "synthetic"),
        TENANT,
        101,
    )
    assert generated.requested_records == 101


def test_multi_cycle_route_reassesses_and_enforces_cumulative_budget():
    alpha = entity("Alpha", field("Alpha", "value"), company_scoped=False)
    beta = entity("Beta", field("Beta", "value"), company_scoped=False)
    model = understanding(alpha, beta)
    objective = LearningObjective("objective", "synthetic bounded study")
    scopes = (("Alpha", ("value",)), ("Beta", ("value",)))
    calls = []

    def reader(resource, fields, requested_records):
        calls.append((resource, fields, requested_records))
        return tuple(
            observation(resource, fields[0], index, index)
            for index in range(1, requested_records + 1)
        )

    result = run_governed_study_cycles(
        objective,
        model,
        (),
        envelope(scopes, cycles=3, per_study=2, cumulative=2),
        record_readers={StudyCapability.ORDINARY_RECORD: reader},
    )

    assert calls == [("Alpha", ("value",), 2)]
    assert result.stop_reason is StudyStopReason.EVIDENCE_BUDGET_LIMIT
    assert result.memory.coverage[0].prior_prediction_attempts == 0
    assert result.memory.coverage[0].prior_prediction_coverage == 0.0

    calls.clear()
    changing = run_governed_study_cycles(
        objective,
        model,
        (),
        envelope(scopes, cycles=2, per_study=1, cumulative=2),
        record_readers={StudyCapability.ORDINARY_RECORD: reader},
    )
    assert [call[0] for call in calls] == ["Alpha", "Beta"]
    assert [item.entity for item in changing.intents] == ["Alpha", "Beta"]
    assert changing.stop_reason is StudyStopReason.CYCLE_LIMIT
    assert changing == run_governed_study_cycles(
        objective,
        model,
        (),
        envelope(scopes, cycles=2, per_study=1, cumulative=2),
        record_readers={StudyCapability.ORDINARY_RECORD: lambda resource, fields, bound: (
            observation(resource, fields[0], 1),
        )},
    )


def test_unsupported_capability_stops_bounded_loop():
    child = entity(
        "Neutral Child", field("Neutral Child", "value"), child=True
    )
    model = understanding(child)
    result = run_governed_study_cycles(
        LearningObjective("objective", "synthetic bounded study"),
        model,
        (),
        envelope((("Neutral Child", ("value",)),)),
        record_readers={},
    )
    assert result.stop_reason is StudyStopReason.UNSUPPORTED_CAPABILITY
    assert result.outcomes == ()


def test_metadata_gap_uses_metadata_capability_without_record_reader():
    relation = entity(
        "Neutral A",
        field("Neutral A", "items", "Table", options="Missing Entity"),
        company_scoped=False,
    )
    calls = []

    def metadata_runner(request):
        calls.append(request)
        return StudyOutcome(
            request.intent.entity,
            (),
            0,
            0,
            0.0,
            0.0,
            "medium",
            "SUPPORTED",
            study_kind="metadata_gap",
            prediction_evaluated=False,
        )

    result = run_governed_study_cycles(
        LearningObjective("objective", "synthetic metadata study"),
        understanding(relation),
        (),
        envelope(()),
        record_readers={},
        metadata_runner=metadata_runner,
    )

    assert len(calls) == 1
    assert calls[0].intent.entity == "Missing Entity"
    assert result.stop_reason is StudyStopReason.EXHAUSTED
    assert result.memory.metadata[0].resolved is True


class FakeResponse:
    def __init__(self, payload):
        self.body = json.dumps(payload).encode()
        self.url = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def geturl(self):
        return self.url

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]


def test_erpnext_router_uses_exact_identity_for_non_company_entity():
    selected = entity(
        "Neutral Entity",
        field("Neutral Entity", "value"),
        company_scoped=False,
    )
    model = understanding(selected)
    limits = envelope((("Neutral Entity", ("value",)),), per_study=1, cumulative=1)
    request = authorize_intent(intent("Neutral Entity", records=1), limits, model)
    calls = []

    def opener(http_request, timeout):
        calls.append(http_request)
        response = FakeResponse({"data": [{"name": "record-1", "value": 0}]})
        response.url = http_request.full_url
        return response

    outcome = run_erpnext_governed_study(
        request,
        envelope=limits,
        understanding=model,
        base_url="https://synthetic.invalid",
        api_key="memory-key",
        api_secret="memory-secret",
        record_identity="record-1",
        opener=opener,
    )
    query = parse_qs(urlsplit(calls[0].full_url).query)

    assert len(calls) == 1
    assert calls[0].method == "GET"
    assert json.loads(query["fields"][0]) == ["value", "name"]
    assert json.loads(query["filters"][0]) == [["name", "=", "record-1"]]
    assert query["limit_page_length"] == ["1"]
    assert "limit_start" not in query
    assert "company" not in calls[0].full_url
    assert "docstatus" not in calls[0].full_url
    assert outcome.valid_count == 1


def test_erpnext_router_rejects_non_company_entity_without_exact_identity():
    selected = entity(
        "Neutral Entity",
        field("Neutral Entity", "value"),
        company_scoped=False,
    )
    model = understanding(selected)
    limits = envelope((("Neutral Entity", ("value",)),), per_study=1, cumulative=1)
    request = authorize_intent(intent("Neutral Entity", records=1), limits, model)
    calls = []

    with pytest.raises(UnsupportedStudyCapabilityError, match="exact identity"):
        run_erpnext_governed_study(
            request,
            envelope=limits,
            understanding=model,
            base_url="https://synthetic.invalid",
            api_key="memory-key",
            api_secret="memory-secret",
            opener=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

    assert calls == []


def test_erpnext_identity_route_rejects_non_exact_bound_before_opener():
    selected = entity(
        "Neutral Entity",
        field("Neutral Entity", "value"),
        company_scoped=False,
    )
    model = understanding(selected)
    limits = envelope((("Neutral Entity", ("value",)),), per_study=2, cumulative=2)
    request = authorize_intent(intent("Neutral Entity", records=2), limits, model)
    calls = []

    with pytest.raises(ValueError, match="exactly one"):
        run_erpnext_governed_study(
            request,
            envelope=limits,
            understanding=model,
            base_url="https://synthetic.invalid",
            record_identity="record-1",
            api_key="memory-key",
            api_secret="memory-secret",
            opener=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

    assert calls == []


@pytest.mark.parametrize(
    "selected,company,record_identity,match",
    (
        (
            entity("Neutral Entity", field("Neutral Entity", "value")),
            COMPANY,
            "record-1",
            "does not accept identity scope",
        ),
        (
            entity(
                "Neutral Entity",
                field("Neutral Entity", "value"),
                company_scoped=False,
            ),
            COMPANY,
            "record-1",
            "structural company field",
        ),
        (
            entity(
                "Neutral Entity",
                field("Neutral Entity", "value"),
                submittable=True,
            ),
            COMPANY,
            "record-1",
            "does not accept identity scope",
        ),
        (
            entity(
                "Neutral Entity",
                field("Neutral Entity", "value"),
                submittable=True,
            ),
            None,
            None,
            "exact company scope",
        ),
        (
            entity(
                "Neutral Entity",
                field("Neutral Entity", "value"),
                submittable=True,
                company_scoped=False,
            ),
            None,
            None,
            "structural company scope",
        ),
    ),
)
def test_erpnext_router_rejects_incompatible_scope_before_opener(
    selected, company, record_identity, match
):
    model = understanding(selected)
    limits = envelope((("Neutral Entity", ("value",)),), per_study=1, cumulative=1)
    request = authorize_intent(intent("Neutral Entity", records=1), limits, model)
    calls = []

    with pytest.raises(UnsupportedStudyCapabilityError, match=match):
        run_erpnext_governed_study(
            request,
            envelope=limits,
            understanding=model,
            base_url="https://synthetic.invalid",
            company=company,
            record_identity=record_identity,
            api_key="memory-key",
            api_secret="memory-secret",
            opener=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

    assert calls == []


@pytest.mark.parametrize("submittable", [False, True])
def test_erpnext_router_selects_structurally_compatible_get(submittable):
    selected = entity(
        "Neutral Entity",
        field("Neutral Entity", "value"),
        submittable=submittable,
    )
    model = understanding(selected)
    limits = envelope((("Neutral Entity", ("value",)),), per_study=1, cumulative=1)
    request = authorize_intent(intent("Neutral Entity", records=1), limits, model)
    calls = []

    def opener(http_request, timeout):
        calls.append(http_request)
        record = {"name": "record-1", "company": COMPANY, "value": 1}
        if submittable:
            record["docstatus"] = 1
        response = FakeResponse({"data": [record]})
        response.url = http_request.full_url
        return response

    outcome = run_erpnext_governed_study(
        request,
        envelope=limits,
        understanding=model,
        base_url="https://synthetic.invalid",
        company=COMPANY,
        api_key="memory-key",
        api_secret="memory-secret",
        opener=opener,
    )
    query = parse_qs(urlsplit(calls[0].full_url).query)

    assert len(calls) == 1
    assert outcome.valid_count == 1
    assert ("docstatus" in json.loads(query["fields"][0])) is submittable
