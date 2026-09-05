import json
from dataclasses import replace
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from orion.contracts import EvidenceKind, ObservationMode
from orion.discovery.erpnext_identity_record_evidence import (
    run_erpnext_identity_record_evidence,
)
from orion.discovery.erpnext_identity_record_sample import (
    ERPNextIdentityRecordSampleAdapter,
    ERPNextIdentityRecordSampleError,
)
from orion.learning.autonomous_loop import (
    AuthorizationEnvelope,
    StudyIntent,
    authorize_intent,
)
from orion.understanding.metadata import (
    MetadataUnderstanding,
    StructuralEntity,
    StructuralField,
)

TENANT = "synthetic-tenant"
ENTITY = "Synthetic Profile"
FIELD = "display_code"
IDENTITY = "profile-007"
FIELDS = (FIELD, "name")


class FakeResponse:
    def __init__(self, payload=None, *, body=None, url=None):
        self.body = json.dumps(payload).encode() if body is None else body
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]

    def geturl(self):
        return self.url


def adapter(*, opener, sample_size=1, record_identity=IDENTITY, **changes):
    arguments = {
        "base_url": "https://synthetic.invalid",
        "tenant_id": TENANT,
        "api_key": "memory-key",
        "api_secret": "memory-secret",
        "resource": ENTITY,
        "record_identity": record_identity,
        "fields": FIELDS,
        "sample_size": sample_size,
        "opener": opener,
    }
    arguments.update(changes)
    return ERPNextIdentityRecordSampleAdapter(**arguments)


def row(name=IDENTITY, value=0, **changes):
    result = {FIELD: value, "name": name}
    result.update(changes)
    return result


def test_identity_record_sample_is_one_exact_bounded_get_without_pagination():
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        return FakeResponse({"data": [row()]}, url=request.full_url)

    observations = adapter(opener=opener).discover()
    request, timeout = calls[0]
    parsed = urlsplit(request.full_url)
    query = parse_qs(parsed.query)

    assert len(calls) == 1
    assert request.method == "GET"
    assert timeout == 20
    assert parsed.path == "/api/resource/Synthetic%20Profile"
    assert json.loads(query["fields"][0]) == [FIELD, "name"]
    assert json.loads(query["filters"][0]) == [["name", "=", IDENTITY]]
    assert "company" not in request.full_url
    assert "docstatus" not in request.full_url
    assert query["order_by"] == ["name desc"]
    assert query["limit_page_length"] == ["1"]
    assert "limit_start" not in query
    assert len(observations) == 1
    observation = observations[0]
    assert observation.mode is ObservationMode.READ_ONLY
    assert observation.evidence.kind is EvidenceKind.API
    assert observation.evidence.tenant_id == TENANT
    assert observation.evidence.payload == {"resource": ENTITY, "record": row()}


@pytest.mark.parametrize("sample_size", (0, 2, True, 1.0))
def test_identity_record_sample_rejects_non_exact_bound_before_opener(sample_size):
    calls = []

    with pytest.raises(ValueError, match="exactly 1"):
        adapter(
            opener=lambda *args, **kwargs: calls.append((args, kwargs)),
            sample_size=sample_size,
        )

    assert calls == []


@pytest.mark.parametrize("record_identity", ("", " ", " padded", "padded ", "bad\nname"))
def test_identity_record_sample_rejects_unsafe_identity_before_opener(record_identity):
    calls = []

    with pytest.raises(ValueError, match="record_identity"):
        adapter(
            opener=lambda *args, **kwargs: calls.append((args, kwargs)),
            record_identity=record_identity,
        )

    assert calls == []


@pytest.mark.parametrize(
    "payload,match",
    (
        ({"data": [row(), row(name="profile-008")]}, "record bound"),
        ({"data": [row(), row()]}, "duplicate document identity"),
        ({"data": [row(extra=True)]}, "unrequested fields"),
        ({"data": [{"name": IDENTITY}]}, "missing requested"),
        ({"data": [{FIELD: "value"}]}, "missing requested"),
        ({"data": [row(name=" ")]}, "invalid document identity"),
        ({"data": [row(name="profile-008")]}, "identity boundary"),
        ({"data": ["not-a-mapping"]}, "JSON objects"),
    ),
)
def test_identity_record_sample_rejects_invalid_rows(payload, match):
    def opener(request, timeout):
        return FakeResponse(payload, url=request.full_url)

    with pytest.raises(ERPNextIdentityRecordSampleError, match=match):
        adapter(opener=opener).discover()


@pytest.mark.parametrize(
    "response,match",
    (
        (FakeResponse(body=b"not-json"), "invalid JSON"),
        (FakeResponse([]), "JSON object"),
        (FakeResponse({"data": {}}), "data must be a list"),
    ),
)
def test_identity_record_sample_rejects_malformed_responses(response, match):
    def opener(request, timeout):
        response.url = request.full_url
        return response

    with pytest.raises(ERPNextIdentityRecordSampleError, match=match):
        adapter(opener=opener).discover()


def test_identity_record_sample_rejects_redirect_and_network_failures():
    def redirect(request, timeout):
        return FakeResponse({"data": []}, url="https://other.invalid")

    with pytest.raises(ERPNextIdentityRecordSampleError, match="redirect"):
        adapter(opener=redirect).discover()

    for failure in (
        URLError("synthetic failure"),
        TimeoutError("synthetic timeout"),
        HTTPError("https://synthetic.invalid", 500, "failure", None, None),
    ):
        def network_failure(request, timeout, failure=failure):
            raise failure

        with pytest.raises(ERPNextIdentityRecordSampleError, match="failed"):
            adapter(opener=network_failure).discover()


def test_identity_record_sample_rejects_oversize_response():
    def opener(request, timeout):
        return FakeResponse(body=b"x" * 33, url=request.full_url)

    with pytest.raises(ERPNextIdentityRecordSampleError, match="response exceeds"):
        adapter(opener=opener, max_response_bytes=32).discover()


def structural_understanding(
    *,
    submittable=False,
    child=False,
    single=False,
    include_scope_field=False,
    selected_type="Data",
):
    fields = [
        StructuralField(
            ENTITY,
            FIELD,
            selected_type,
            None,
            "Related" if selected_type == "Table" else None,
            True,
            False,
            False,
            False,
        ),
    ]
    if include_scope_field:
        fields.append(
            StructuralField(
                ENTITY,
                "company",
                "Link",
                None,
                "Organization",
                False,
                False,
                False,
                False,
            )
        )
    return MetadataUnderstanding(
        TENANT,
        (
            StructuralEntity(
                ENTITY, None, submittable, child, single, tuple(fields), ()
            ),
        ),
    )


def envelope(*, records=1):
    return AuthorizationEnvelope(
        TENANT,
        "objective-1",
        allowed_record_entities=frozenset({ENTITY}),
        allowed_record_fields=((ENTITY, (FIELD,)),),
        max_records_per_proposal=records,
    )


def governed_request(*, records=1, understanding=None, authorization=None):
    understanding = understanding or structural_understanding()
    authorization = authorization or envelope(records=records)
    intent = StudyIntent(
        TENANT,
        ENTITY,
        (FIELD,),
        "record_evidence",
        records,
        "synthetic hypothesis",
        "aggregate evidence",
        "synthetic rationale",
    )
    return authorize_intent(intent, authorization, understanding)


def run_composition(
    understanding,
    *,
    records=1,
    record_identity=IDENTITY,
    opener,
    evidence_sink=None,
    request=None,
    authorization=None,
):
    authorization = authorization or envelope(records=records)
    request = request or governed_request(
        records=records,
        understanding=understanding,
        authorization=authorization,
    )
    return run_erpnext_identity_record_evidence(
        request,
        envelope=authorization,
        understanding=understanding,
        base_url="https://synthetic.invalid",
        record_identity=record_identity,
        api_key="memory-key",
        api_secret="memory-secret",
        opener=opener,
        evidence_sink=evidence_sink,
    )


def test_governed_identity_composition_passes_exact_runner_scope_and_sink():
    calls = []
    sink_calls = []
    understanding = structural_understanding()

    def opener(request, timeout):
        calls.append(request)
        return FakeResponse({"data": [row(value=False)]}, url=request.full_url)

    first = run_composition(
        understanding,
        opener=opener,
        evidence_sink=lambda *args: sink_calls.append(args),
    )
    second = run_composition(understanding, opener=opener)
    parsed = urlsplit(calls[0].full_url)
    query = parse_qs(parsed.query)

    assert parsed.path == "/api/resource/Synthetic%20Profile"
    assert json.loads(query["fields"][0]) == [FIELD, "name"]
    assert json.loads(query["filters"][0]) == [["name", "=", IDENTITY]]
    assert query["limit_page_length"] == ["1"]
    assert first == second
    assert first.observations_acquired == 1
    assert first.valid_count == 1
    assert first.prediction_evaluated is False
    assert first.recommendation_allowed is False
    assert first.promotion_allowed is False
    assert first.execution_allowed is False
    assert sink_calls[0][0] == governed_request(understanding=understanding)
    assert len(sink_calls[0][1]) == 1


@pytest.mark.parametrize(
    "understanding,match",
    (
        (structural_understanding(submittable=True), "non-submittable"),
        (structural_understanding(child=True), "child table"),
        (structural_understanding(single=True), "not be single"),
        (structural_understanding(include_scope_field=True), "company field"),
        (structural_understanding(selected_type="Table"), "collection fields"),
    ),
)
def test_governed_identity_composition_rejects_wrong_structure_before_opener(
    understanding, match
):
    calls = []

    with pytest.raises(ValueError, match=match):
        run_composition(
            understanding,
            opener=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

    assert calls == []


def test_governed_identity_composition_does_not_require_structural_name_field():
    understanding = structural_understanding()

    outcome = run_composition(
        understanding,
        opener=lambda request, timeout: FakeResponse(
            {"data": [row(value="synthetic")]}, url=request.full_url
        ),
    )

    assert outcome.observations_acquired == 1


def test_governed_identity_composition_rejects_wrong_bound_and_identity_before_opener():
    calls = []
    sink_calls = []
    understanding = structural_understanding()

    with pytest.raises(ValueError, match="exactly one"):
        run_composition(
            understanding,
            records=2,
            opener=lambda *args, **kwargs: calls.append((args, kwargs)),
            evidence_sink=lambda *args: sink_calls.append(args),
        )

    with pytest.raises(ValueError, match="record_identity"):
        run_composition(
            understanding,
            record_identity=" ",
            opener=lambda *args, **kwargs: calls.append((args, kwargs)),
            evidence_sink=lambda *args: sink_calls.append(args),
        )

    assert calls == []
    assert sink_calls == []


def test_governed_identity_composition_reauthorizes_before_opener_and_sink():
    calls = []
    sink_calls = []
    understanding = structural_understanding()
    authorized = governed_request(understanding=understanding)
    changed_intent = replace(authorized.intent, requested_records=2)
    malformed_request = replace(authorized, intent=changed_intent)

    with pytest.raises(ValueError, match="record budget"):
        run_composition(
            understanding,
            request=malformed_request,
            opener=lambda *args, **kwargs: calls.append((args, kwargs)),
            evidence_sink=lambda *args: sink_calls.append(args),
        )

    assert calls == []
    assert sink_calls == []


def test_governed_identity_composition_validation_precedes_sink():
    sink_calls = []

    def opener(request, timeout):
        return FakeResponse({"data": [row(extra=True)]}, url=request.full_url)

    with pytest.raises(ERPNextIdentityRecordSampleError, match="unrequested"):
        run_composition(
            structural_understanding(),
            opener=opener,
            evidence_sink=lambda *args: sink_calls.append(args),
        )

    assert sink_calls == []
