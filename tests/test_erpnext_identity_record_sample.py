import json
from urllib.error import URLError
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
ENTITY = "Synthetic Entity"
IDENTITY = "record-1"
FIELD = "selected_value"
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


def adapter(*, opener, identity=IDENTITY, sample_size=1):
    return ERPNextIdentityRecordSampleAdapter(
        base_url="https://synthetic.invalid",
        tenant_id=TENANT,
        api_key="memory-key",
        api_secret="memory-secret",
        resource=ENTITY,
        record_identity=identity,
        fields=FIELDS,
        sample_size=sample_size,
        opener=opener,
    )


def row(name=IDENTITY, value=0, **changes):
    result = {FIELD: value, "name": name}
    result.update(changes)
    return result


def test_identity_sample_is_one_exact_bounded_get():
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
    assert parsed.path == "/api/resource/Synthetic%20Entity"
    assert json.loads(query["fields"][0]) == list(FIELDS)
    assert json.loads(query["filters"][0]) == [["name", "=", IDENTITY]]
    assert "company" not in query["filters"][0]
    assert "docstatus" not in query["filters"][0]
    assert query["order_by"] == ["name desc"]
    assert query["limit_start"] == ["0"]
    assert query["limit_page_length"] == ["1"]
    assert len(observations) == 1
    observation = observations[0]
    assert observation.mode is ObservationMode.READ_ONLY
    assert observation.evidence.kind is EvidenceKind.API
    assert observation.evidence.tenant_id == TENANT
    assert observation.evidence.payload == {"resource": ENTITY, "record": row()}


@pytest.mark.parametrize("sample_size", [0, 2, 25])
def test_identity_sample_requires_exactly_one_record_before_opener(sample_size):
    calls = []

    with pytest.raises(ValueError, match="between 1 and 1"):
        adapter(
            opener=lambda *args, **kwargs: calls.append((args, kwargs)),
            sample_size=sample_size,
        )

    assert calls == []


@pytest.mark.parametrize("identity", ["", " ", " record-1", "record-1 ", "record\n1"])
def test_identity_sample_rejects_invalid_identity_before_opener(identity):
    calls = []

    with pytest.raises(ValueError):
        adapter(
            opener=lambda *args, **kwargs: calls.append((args, kwargs)),
            identity=identity,
        )

    assert calls == []


@pytest.mark.parametrize(
    "payload,match",
    (
        ({"data": [row(), row()]}, "record bound"),
        ({"data": [row(extra=True)]}, "unrequested fields"),
        ({"data": [{"name": IDENTITY}]}, "missing requested"),
        ({"data": [row(name=" ")]}, "invalid document identity"),
        ({"data": [row(name="other-record")]}, "identity boundary"),
        ({"data": ["not-a-mapping"]}, "JSON objects"),
    ),
)
def test_identity_sample_rejects_invalid_rows(payload, match):
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
def test_identity_sample_rejects_malformed_responses(response, match):
    def opener(request, timeout):
        response.url = request.full_url
        return response

    with pytest.raises(ERPNextIdentityRecordSampleError, match=match):
        adapter(opener=opener).discover()


def test_identity_sample_rejects_redirect_and_network_failure():
    def redirect(request, timeout):
        return FakeResponse({"data": []}, url="https://other.invalid")

    with pytest.raises(ERPNextIdentityRecordSampleError, match="redirect"):
        adapter(opener=redirect).discover()

    def network_failure(request, timeout):
        raise URLError("synthetic failure")

    with pytest.raises(ERPNextIdentityRecordSampleError, match="failed"):
        adapter(opener=network_failure).discover()


def structural_understanding(
    *,
    submittable=False,
    child=False,
    single=False,
    include_company=False,
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
        )
    ]
    if include_company:
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
                ENTITY,
                None,
                submittable,
                child,
                single,
                tuple(fields),
                (),
            ),
        ),
    )


def envelope(*, records=1):
    return AuthorizationEnvelope(
        TENANT,
        "objective-1",
        allowed_record_entities=frozenset({ENTITY}),
        allowed_record_fields=((ENTITY, (FIELD,)),),
        max_records_per_proposal=max(1, records),
    )


def governed_request(*, records=1, understanding=None):
    understanding = understanding or structural_understanding()
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
    return authorize_intent(intent, envelope(records=records), understanding)


def run_composition(
    understanding,
    *,
    records=1,
    identity=IDENTITY,
    opener,
    evidence_sink=None,
):
    return run_erpnext_identity_record_evidence(
        governed_request(records=records, understanding=understanding),
        envelope=envelope(records=records),
        understanding=understanding,
        base_url="https://synthetic.invalid",
        record_identity=identity,
        api_key="memory-key",
        api_secret="memory-secret",
        opener=opener,
        evidence_sink=evidence_sink,
    )


def test_governed_identity_composition_passes_exact_scope_and_sink():
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
    query = parse_qs(urlsplit(calls[0].full_url).query)

    assert json.loads(query["fields"][0]) == [FIELD, "name"]
    assert json.loads(query["filters"][0]) == [["name", "=", IDENTITY]]
    assert query["limit_page_length"] == ["1"]
    assert first == second
    assert first.prediction_evaluated is False
    assert first.recommendation_allowed is False
    assert first.promotion_allowed is False
    assert first.execution_allowed is False
    assert sink_calls[0][0] == governed_request()
    assert len(sink_calls[0][1]) == 1


@pytest.mark.parametrize(
    "understanding,match",
    (
        (structural_understanding(submittable=True), "non-submittable"),
        (structural_understanding(child=True), "child table"),
        (structural_understanding(single=True), "not be single"),
        (structural_understanding(include_company=True), "without company scope"),
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


def test_governed_identity_composition_rejects_non_one_bound_before_opener():
    calls = []
    understanding = structural_understanding()

    with pytest.raises(ValueError, match="exactly one record"):
        run_composition(
            understanding,
            records=2,
            opener=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

    assert calls == []


def test_governed_identity_composition_rejects_invalid_identity_before_opener():
    calls = []

    with pytest.raises(ValueError):
        run_composition(
            structural_understanding(),
            identity=" ",
            opener=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

    assert calls == []


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
