import json
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from orion.contracts import EvidenceKind, ObservationMode
from orion.discovery.erpnext_company_record_evidence import (
    run_erpnext_company_record_evidence,
)
from orion.discovery.erpnext_company_record_sample import (
    ERPNextCompanyRecordSampleAdapter,
    ERPNextCompanyRecordSampleError,
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
COMPANY = "Synthetic Company"
FIELD = "selected_value"
FIELDS = (FIELD, "name", "company")


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


def adapter(*, opener, sample_size=2):
    return ERPNextCompanyRecordSampleAdapter(
        base_url="https://synthetic.invalid",
        tenant_id=TENANT,
        api_key="memory-key",
        api_secret="memory-secret",
        resource=ENTITY,
        company=COMPANY,
        fields=FIELDS,
        sample_size=sample_size,
        opener=opener,
    )


def row(name="record-1", value=0, **changes):
    result = {FIELD: value, "name": name, "company": COMPANY}
    result.update(changes)
    return result


def test_company_record_sample_is_one_exact_bounded_get():
    calls = []

    def opener(request, timeout):
        calls.append((request, timeout))
        return FakeResponse({"data": [row()]}, url=request.full_url)

    first = adapter(opener=opener).discover()
    request, timeout = calls[0]
    query = parse_qs(urlsplit(request.full_url).query)

    assert len(calls) == 1
    assert request.method == "GET"
    assert timeout == 20
    assert json.loads(query["fields"][0]) == list(FIELDS)
    assert json.loads(query["filters"][0]) == [["company", "=", COMPANY]]
    assert "docstatus" not in request.full_url
    assert query["order_by"] == ["name desc"]
    assert query["limit_start"] == ["0"]
    assert query["limit_page_length"] == ["2"]
    assert len(first) == 1
    observation = first[0]
    assert observation.mode is ObservationMode.READ_ONLY
    assert observation.evidence.kind is EvidenceKind.API
    assert observation.evidence.tenant_id == TENANT
    assert observation.evidence.payload == {"resource": ENTITY, "record": row()}


@pytest.mark.parametrize(
    "payload,sample_size,match",
    (
        ({"data": [row(), row("record-2")]}, 1, "record bound"),
        ({"data": [row(extra=True)]}, 2, "unrequested fields"),
        ({"data": [{"name": "record-1", "company": COMPANY}]}, 2, "missing requested"),
        ({"data": [row(name=" ")]}, 2, "invalid document identity"),
        ({"data": [row(), row()]}, 2, "duplicate document identity"),
        ({"data": [row(company="Other Company")]}, 2, "company boundary"),
        ({"data": ["not-a-mapping"]}, 2, "JSON objects"),
    ),
)
def test_company_record_sample_rejects_invalid_rows(payload, sample_size, match):
    def opener(request, timeout):
        return FakeResponse(payload, url=request.full_url)

    with pytest.raises(ERPNextCompanyRecordSampleError, match=match):
        adapter(opener=opener, sample_size=sample_size).discover()


@pytest.mark.parametrize(
    "response,match",
    (
        (FakeResponse(body=b"not-json"), "invalid JSON"),
        (FakeResponse([]), "JSON object"),
        (FakeResponse({"data": {}}), "data must be a list"),
    ),
)
def test_company_record_sample_rejects_malformed_responses(response, match):
    def opener(request, timeout):
        response.url = request.full_url
        return response

    with pytest.raises(ERPNextCompanyRecordSampleError, match=match):
        adapter(opener=opener).discover()


def test_company_record_sample_rejects_redirect_and_network_failure():
    def redirect(request, timeout):
        return FakeResponse({"data": []}, url="https://other.invalid")

    with pytest.raises(ERPNextCompanyRecordSampleError, match="redirect"):
        adapter(opener=redirect).discover()

    def network_failure(request, timeout):
        raise URLError("synthetic failure")

    with pytest.raises(ERPNextCompanyRecordSampleError, match="failed"):
        adapter(opener=network_failure).discover()


def test_company_record_sample_rejects_oversize_response():
    def opener(request, timeout):
        return FakeResponse(body=b"x" * 33, url=request.full_url)

    sample = ERPNextCompanyRecordSampleAdapter(
        base_url="https://synthetic.invalid",
        tenant_id=TENANT,
        api_key="memory-key",
        api_secret="memory-secret",
        resource=ENTITY,
        company=COMPANY,
        fields=FIELDS,
        sample_size=2,
        max_response_bytes=32,
        opener=opener,
    )

    with pytest.raises(
        ERPNextCompanyRecordSampleError,
        match="response exceeds configured bound",
    ):
        sample.discover()


def test_company_record_sample_wraps_http_error():
    def opener(request, timeout):
        raise HTTPError(
            request.full_url,
            500,
            "synthetic failure",
            hdrs=None,
            fp=None,
        )

    with pytest.raises(ERPNextCompanyRecordSampleError, match="failed"):
        adapter(opener=opener).discover()


def structural_understanding(
    *,
    submittable=False,
    child=False,
    single=False,
    include_company=True,
    selected_type="Data",
):
    fields = [
        StructuralField(
            ENTITY, FIELD, selected_type, None,
            "Related" if selected_type == "Table" else None,
            True, False, False, False,
        ),
        StructuralField(
            ENTITY, "name", "Data", None, None,
            False, True, False, False,
        ),
    ]
    if include_company:
        fields.append(
            StructuralField(
                ENTITY, "company", "Link", None, "Organization",
                False, False, False, False,
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


def envelope(*, records=2):
    return AuthorizationEnvelope(
        TENANT,
        "objective-1",
        allowed_record_entities=frozenset({ENTITY}),
        allowed_record_fields=((ENTITY, (FIELD,)),),
        max_records_per_proposal=records,
    )


def governed_request(*, records=2, understanding=None):
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


def run_composition(understanding, *, records=2, opener, evidence_sink=None):
    return run_erpnext_company_record_evidence(
        governed_request(records=records, understanding=understanding),
        envelope=envelope(records=records),
        understanding=understanding,
        base_url="https://synthetic.invalid",
        company=COMPANY,
        api_key="memory-key",
        api_secret="memory-secret",
        opener=opener,
        evidence_sink=evidence_sink,
    )


def test_governed_company_composition_passes_exact_scope_and_sink():
    calls = []
    sink_calls = []
    understanding = structural_understanding()

    def opener(request, timeout):
        calls.append(request)
        return FakeResponse({"data": [row(value=False)]}, url=request.full_url)

    first = run_composition(
        understanding,
        records=1,
        opener=opener,
        evidence_sink=lambda *args: sink_calls.append(args),
    )
    second = run_composition(understanding, records=1, opener=opener)
    parsed = urlsplit(calls[0].full_url)
    query = parse_qs(parsed.query)

    assert parsed.path == "/api/resource/Synthetic%20Entity"
    assert json.loads(query["fields"][0]) == [FIELD, "name", "company"]
    assert query["limit_page_length"] == ["1"]
    assert first == second
    assert first.prediction_evaluated is False
    assert sink_calls[0][0] == governed_request(records=1)
    assert len(sink_calls[0][1]) == 1


@pytest.mark.parametrize(
    "understanding,match",
    (
        (structural_understanding(submittable=True), "non-submittable"),
        (structural_understanding(child=True), "child table"),
        (structural_understanding(single=True), "not be single"),
        (structural_understanding(include_company=False), "company field"),
        (structural_understanding(selected_type="Table"), "collection fields"),
    ),
)
def test_governed_company_composition_rejects_wrong_structure_before_opener(
    understanding, match
):
    calls = []

    with pytest.raises(ValueError, match=match):
        run_composition(
            understanding,
            opener=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

    assert calls == []


def test_governed_company_composition_rejects_capacity_before_opener():
    calls = []

    with pytest.raises(ValueError, match="between 1 and 25"):
        run_composition(
            structural_understanding(),
            records=26,
            opener=lambda *args, **kwargs: calls.append((args, kwargs)),
        )

    assert calls == []


def test_governed_company_composition_validation_precedes_sink():
    sink_calls = []

    def opener(request, timeout):
        return FakeResponse({"data": [row(extra=True)]}, url=request.full_url)

    with pytest.raises(ERPNextCompanyRecordSampleError, match="unrequested"):
        run_composition(
            structural_understanding(),
            opener=opener,
            evidence_sink=lambda *args: sink_calls.append(args),
        )

    assert sink_calls == []
