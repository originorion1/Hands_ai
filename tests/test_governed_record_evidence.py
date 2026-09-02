import json
from urllib.parse import parse_qs, urlsplit

import pytest

from orion.contracts import Evidence, EvidenceKind, Observation, ObservationMode
from orion.discovery.erpnext_governed_record_evidence import (
    run_erpnext_submitted_company_record_evidence,
)
from orion.learning.autonomous_loop import (
    AuthorizationEnvelope,
    StudyIntent,
    authorize_intent,
)
from orion.learning.governed_record_evidence import run_governed_record_evidence
from orion.understanding.metadata import (
    MetadataUnderstanding,
    StructuralEntity,
    StructuralField,
)

TENANT = "synthetic-tenant"
ENTITY = "Synthetic Record"
FIELD = "selected_value"


def understanding(*, tenant=TENANT):
    return MetadataUnderstanding(
        tenant,
        (
            StructuralEntity(
                ENTITY,
                None,
                False,
                False,
                False,
                (
                    StructuralField(
                        ENTITY, FIELD, "Data", None, None,
                        False, False, False, False,
                    ),
                    StructuralField(
                        ENTITY, "other", "Data", None, None,
                        False, False, False, False,
                    ),
                ),
                (),
            ),
        ),
    )


def envelope(*, tenant=TENANT, fields=(FIELD,), records=5):
    return AuthorizationEnvelope(
        tenant,
        "objective-1",
        allowed_record_entities=frozenset({ENTITY}),
        allowed_record_fields=((ENTITY, fields),),
        max_records_per_proposal=records,
    )


def request(*, field=FIELD, records=5):
    intent = StudyIntent(
        TENANT,
        ENTITY,
        (field,),
        "record_evidence",
        records,
        "synthetic hypothesis",
        "aggregate evidence",
        "synthetic rationale",
    )
    return authorize_intent(
        intent,
        envelope(fields=(field,), records=records),
        understanding(),
    )


def observation(
    value=1,
    *,
    mode=ObservationMode.READ_ONLY,
    kind=EvidenceKind.API,
    tenant=TENANT,
    resource=ENTITY,
    payload=None,
):
    return Observation(
        evidence=Evidence(
            kind=kind,
            source="synthetic-reader",
            tenant_id=tenant,
            payload=(
                {"resource": resource, "record": {FIELD: value}}
                if payload is None
                else payload
            ),
        ),
        mode=mode,
    )


@pytest.mark.parametrize(
    "current_envelope,current_understanding",
    (
        (envelope(tenant="other-tenant"), understanding(tenant="other-tenant")),
        (AuthorizationEnvelope(TENANT, "objective-1"), understanding()),
        (envelope(fields=("other",)), understanding()),
        (envelope(records=4), understanding()),
    ),
)
def test_current_authorization_rejects_before_reader(
    current_envelope, current_understanding
):
    calls = []

    with pytest.raises(ValueError):
        run_governed_record_evidence(
            request(),
            envelope=current_envelope,
            understanding=current_understanding,
            reader=lambda: calls.append(True),
        )

    assert calls == []


def test_raw_and_metadata_requests_fail_before_reader():
    calls = []
    with pytest.raises(TypeError, match="AuthorizedStudyRequest"):
        run_governed_record_evidence(
            object(),
            envelope=envelope(),
            understanding=understanding(),
            reader=lambda: calls.append(True),
        )

    metadata_intent = StudyIntent(
        TENANT, "Unknown", (), "metadata_gap", 0, "h", "e", "r"
    )
    metadata_envelope = AuthorizationEnvelope(
        TENANT,
        "objective-1",
        allowed_metadata_entities=frozenset({"Unknown"}),
    )
    metadata_request = authorize_intent(
        metadata_intent, metadata_envelope, understanding()
    )
    with pytest.raises(ValueError, match="record_evidence"):
        run_governed_record_evidence(
            metadata_request,
            envelope=metadata_envelope,
            understanding=understanding(),
            reader=lambda: calls.append(True),
        )

    multi_intent = StudyIntent(
        TENANT,
        ENTITY,
        (FIELD, "other"),
        "record_evidence",
        1,
        "h",
        "e",
        "r",
    )
    multi_envelope = envelope(fields=(FIELD, "other"))
    multi_request = authorize_intent(
        multi_intent, multi_envelope, understanding()
    )
    with pytest.raises(ValueError, match="exactly one field"):
        run_governed_record_evidence(
            multi_request,
            envelope=multi_envelope,
            understanding=understanding(),
            reader=lambda: calls.append(True),
        )

    assert calls == []


@pytest.mark.parametrize(
    "bad_observation,match",
    (
        (observation(mode=ObservationMode.SHADOW), "READ_ONLY"),
        (observation(kind=EvidenceKind.METADATA), "API evidence"),
        (observation(tenant="other-tenant"), "tenant boundary"),
        (observation(resource="Other Record"), "resource"),
        (observation(payload=[]), "payload must be a mapping"),
        (observation(payload={"resource": ENTITY, "record": []}), "record payload"),
        (observation(payload={"resource": ENTITY, "record": {}}), "selected field"),
    ),
)
def test_malformed_observations_fail_closed(bad_observation, match):
    with pytest.raises((TypeError, ValueError), match=match):
        run_governed_record_evidence(
            request(),
            envelope=envelope(),
            understanding=understanding(),
            reader=lambda: (bad_observation,),
        )


def test_reader_bound_type_and_exception_fail_closed():
    with pytest.raises(ValueError, match="record bound"):
        run_governed_record_evidence(
            request(records=1),
            envelope=envelope(records=1),
            understanding=understanding(),
            reader=lambda: (observation(), observation()),
        )
    with pytest.raises(TypeError, match="Observation"):
        run_governed_record_evidence(
            request(),
            envelope=envelope(),
            understanding=understanding(),
            reader=lambda: (object(),),
        )

    def failed_reader():
        raise RuntimeError("synthetic reader failure")

    with pytest.raises(RuntimeError, match="synthetic reader failure"):
        run_governed_record_evidence(
            request(),
            envelope=envelope(),
            understanding=understanding(),
            reader=failed_reader,
        )


def test_missing_semantics_are_canonical_and_outcome_is_aggregate_only():
    from orion.learning import governed_record_evidence, offline_proposal

    observations = tuple(observation(value) for value in (None, "  ", 0, False))
    first = run_governed_record_evidence(
        request(),
        envelope=envelope(),
        understanding=understanding(),
        reader=lambda: observations,
    )
    second = run_governed_record_evidence(
        request(),
        envelope=envelope(),
        understanding=understanding(),
        reader=lambda: observations,
    )

    assert first == second
    assert (first.observations_acquired, first.valid_count) == (4, 2)
    assert first.prediction_evaluated is False
    assert first.coverage_change == first.uncertainty_reduction == 0.0
    assert first.hypothesis_state == "INCONCLUSIVE"
    assert not first.recommendation_allowed
    assert not first.promotion_allowed
    assert not first.execution_allowed
    assert not hasattr(first, "observations")
    assert (
        offline_proposal.is_missing_evidence
        is governed_record_evidence.is_missing_evidence
    )


class FakeResponse:
    def __init__(self, body):
        self.body = body
        self.url = ""

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None

    def geturl(self):
        return self.url

    def read(self, _limit):
        return self.body


def test_erpnext_composition_is_dynamic_bounded_and_get_only():
    calls = []
    body = json.dumps(
        {
            "data": [
                {
                    FIELD: 0,
                    "name": "synthetic-1",
                    "company": "Synthetic Company",
                    "docstatus": 1,
                }
            ]
        }
    ).encode()

    def opener(http_request, *, timeout):
        calls.append((http_request, timeout))
        response = FakeResponse(body)
        response.url = http_request.full_url
        return response

    outcome = run_erpnext_submitted_company_record_evidence(
        request(records=1),
        envelope=envelope(records=1),
        understanding=understanding(),
        base_url="https://synthetic.invalid",
        company="Synthetic Company",
        api_key="memory-key",
        api_secret="memory-secret",
        opener=opener,
    )
    http_request, _ = calls[0]
    query = parse_qs(urlsplit(http_request.full_url).query)

    assert http_request.method == "GET"
    assert ENTITY.replace(" ", "%20") in http_request.full_url
    assert json.loads(query["fields"][0]) == [
        FIELD, "name", "company", "docstatus",
    ]
    assert query["order_by"] == ["name desc"]
    assert outcome.valid_count == 1
    assert outcome.prediction_evaluated is False


def test_erpnext_capacity_fails_before_opener_without_clamping():
    calls = []

    with pytest.raises(ValueError, match="reader capacity"):
        run_erpnext_submitted_company_record_evidence(
            request(records=26),
            envelope=envelope(records=26),
            understanding=understanding(),
            base_url="https://synthetic.invalid",
            company="Synthetic Company",
            api_key="memory-key",
            api_secret="memory-secret",
            opener=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

    assert calls == []
