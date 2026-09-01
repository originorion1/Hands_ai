import json
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from orion.contracts import ObservationMode
from orion.discovery.erpnext_historical_sample import (
    ERPNextHistoricalSampleAdapter,
    ERPNextHistoricalSampleError,
)


class FakeResponse:
    def __init__(
        self,
        payload,
        *,
        url=None,
    ):
        self._payload = json.dumps(
            payload
        ).encode()
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        if size < 0:
            return self._payload

        return self._payload[:size]

    def geturl(self):
        return self._url


FIELDS = (
    "name",
    "company",
    "supplier",
    "posting_date",
    "grand_total",
    "docstatus",
)


def make_adapter(
    *,
    opener,
    fields=FIELDS,
    sample_size=5,
):
    return ERPNextHistoricalSampleAdapter(
        base_url="https://example.test",
        tenant_id="customer-a",
        api_key="key",
        api_secret="secret",
        resource="Purchase Invoice",
        company="Example Company",
        fields=fields,
        sample_size=sample_size,
        opener=opener,
    )


def test_historical_sample_is_single_bounded_get():
    captured = {
        "calls": 0,
    }

    def opener(request, timeout):
        captured["calls"] += 1
        captured["method"] = request.method
        captured["url"] = request.full_url
        captured["authorization"] = (
            request.headers["Authorization"]
        )
        captured["timeout"] = timeout

        return FakeResponse(
            {
                "data": [
                    {
                        "name": "PINV-001",
                        "company": "Example Company",
                        "supplier": "Supplier A",
                        "posting_date": "2026-08-31",
                        "grand_total": 125.5,
                        "docstatus": 1,
                    }
                ]
            },
            url=request.full_url,
        )

    observations = (
        make_adapter(
            opener=opener,
        ).discover()
    )

    assert captured["calls"] == 1
    assert captured["method"] == "GET"
    assert captured["timeout"] == 20
    assert (
        captured["authorization"]
        == "token key:secret"
    )

    parsed = urlsplit(
        captured["url"]
    )
    query = parse_qs(
        parsed.query
    )

    assert (
        parsed.path
        == "/api/resource/Purchase%20Invoice"
    )

    assert json.loads(
        query["fields"][0]
    ) == list(FIELDS)

    assert json.loads(
        query["filters"][0]
    ) == [
        [
            "company",
            "=",
            "Example Company",
        ],
        [
            "docstatus",
            "=",
            1,
        ],
    ]

    assert query["order_by"] == [
        "posting_date desc, name desc"
    ]
    assert query["limit_start"] == [
        "0"
    ]
    assert query["limit_page_length"] == [
        "5"
    ]

    assert len(observations) == 1

    observation = observations[0]

    assert (
        observation.mode
        is ObservationMode.READ_ONLY
    )
    assert (
        observation.evidence.tenant_id
        == "customer-a"
    )
    assert (
        observation.evidence.payload[
            "resource"
        ]
        == "Purchase Invoice"
    )


@pytest.mark.parametrize(
    "field",
    [
        "*",
        "supplier.name",
        "../supplier",
        "bad field",
        "name?x=1",
    ],
)
def test_historical_sample_rejects_unsafe_fields(
    field,
):
    with pytest.raises(
        ValueError,
        match="field",
    ):
        make_adapter(
            opener=lambda *_args, **_kwargs: None,
            fields=(
                "name",
                "company",
                "docstatus",
                field,
            ),
        )


def test_historical_sample_requires_audit_fields():
    with pytest.raises(
        ValueError,
        match="audit fields",
    ):
        make_adapter(
            opener=lambda *_args, **_kwargs: None,
            fields=(
                "name",
                "company",
            ),
        )


def test_historical_sample_rejects_duplicate_fields():
    with pytest.raises(
        ValueError,
        match="unique",
    ):
        make_adapter(
            opener=lambda *_args, **_kwargs: None,
            fields=(
                "name",
                "company",
                "docstatus",
                "name",
            ),
        )


def test_historical_sample_rejects_too_many_rows():
    def opener(request, timeout):
        rows = [
            {
                "name": f"PINV-{index}",
                "company": "Example Company",
                "supplier": "Supplier A",
                "posting_date": "2026-08-31",
                "grand_total": 1,
                "docstatus": 1,
            }
            for index in range(6)
        ]

        return FakeResponse(
            {"data": rows},
            url=request.full_url,
        )

    with pytest.raises(
        ERPNextHistoricalSampleError,
        match="record bound",
    ):
        make_adapter(
            opener=opener,
            sample_size=5,
        ).discover()


def test_historical_sample_rejects_cross_company_row():
    def opener(request, timeout):
        return FakeResponse(
            {
                "data": [
                    {
                        "name": "PINV-001",
                        "company": "Other Company",
                        "supplier": "Supplier A",
                        "posting_date": "2026-08-31",
                        "grand_total": 1,
                        "docstatus": 1,
                    }
                ]
            },
            url=request.full_url,
        )

    with pytest.raises(
        ERPNextHistoricalSampleError,
        match="company boundary",
    ):
        make_adapter(
            opener=opener,
        ).discover()


def test_historical_sample_rejects_draft_row():
    def opener(request, timeout):
        return FakeResponse(
            {
                "data": [
                    {
                        "name": "PINV-001",
                        "company": "Example Company",
                        "supplier": "Supplier A",
                        "posting_date": "2026-08-31",
                        "grand_total": 1,
                        "docstatus": 0,
                    }
                ]
            },
            url=request.full_url,
        )

    with pytest.raises(
        ERPNextHistoricalSampleError,
        match="non-submitted",
    ):
        make_adapter(
            opener=opener,
        ).discover()


def test_historical_sample_rejects_unrequested_fields():
    def opener(request, timeout):
        return FakeResponse(
            {
                "data": [
                    {
                        "name": "PINV-001",
                        "company": "Example Company",
                        "supplier": "Supplier A",
                        "posting_date": "2026-08-31",
                        "grand_total": 1,
                        "docstatus": 1,
                        "secret_extra": "unexpected",
                    }
                ]
            },
            url=request.full_url,
        )

    with pytest.raises(
        ERPNextHistoricalSampleError,
        match="unrequested fields",
    ):
        make_adapter(
            opener=opener,
        ).discover()


def test_historical_sample_rejects_oversized_response():
    def opener(request, timeout):
        return FakeResponse(
            {
                "data": [
                    {
                        "name": "X" * 500,
                        "company": "Example Company",
                        "supplier": "Supplier A",
                        "posting_date": "2026-08-31",
                        "grand_total": 1,
                        "docstatus": 1,
                    }
                ]
            },
            url=request.full_url,
        )

    adapter = ERPNextHistoricalSampleAdapter(
        base_url="https://example.test",
        tenant_id="customer-a",
        api_key="key",
        api_secret="secret",
        resource="Purchase Invoice",
        company="Example Company",
        fields=FIELDS,
        sample_size=5,
        max_response_bytes=64,
        opener=opener,
    )

    with pytest.raises(
        ERPNextHistoricalSampleError,
        match="response exceeds",
    ):
        adapter.discover()


def test_historical_sample_fails_closed_on_network_error():
    def opener(request, timeout):
        raise URLError(
            "network unavailable"
        )

    with pytest.raises(
        ERPNextHistoricalSampleError,
        match="historical sample failed",
    ):
        make_adapter(
            opener=opener,
        ).discover()


def test_historical_sample_rejects_redirect():
    def opener(request, timeout):
        return FakeResponse(
            {"data": []},
            url=(
                "https://other.example.test/"
                "api/resource/Purchase%20Invoice"
            ),
        )

    with pytest.raises(
        ERPNextHistoricalSampleError,
        match="redirects are not allowed",
    ):
        make_adapter(
            opener=opener,
        ).discover()


def test_historical_sample_rejects_missing_requested_field():
    def opener(request, timeout):
        return FakeResponse(
            {
                "data": [
                    {
                        "name": "PINV-001",
                        "company": "Example Company",
                        "posting_date": "2026-08-31",
                        "grand_total": 1,
                        "docstatus": 1,
                    }
                ]
            },
            url=request.full_url,
        )

    with pytest.raises(
        ERPNextHistoricalSampleError,
        match="missing requested fields",
    ):
        make_adapter(
            opener=opener,
        ).discover()


def test_historical_sample_rejects_duplicate_document_identity():
    def opener(request, timeout):
        row = {
            "name": "PINV-001",
            "company": "Example Company",
            "supplier": "Supplier A",
            "posting_date": "2026-08-31",
            "grand_total": 1,
            "docstatus": 1,
        }

        return FakeResponse(
            {
                "data": [
                    dict(row),
                    dict(row),
                ]
            },
            url=request.full_url,
        )

    with pytest.raises(
        ERPNextHistoricalSampleError,
        match="duplicate document identity",
    ):
        make_adapter(
            opener=opener,
        ).discover()


def test_historical_sample_order_fields_must_be_requested():
    with pytest.raises(
        ValueError,
        match="requested sample fields",
    ):
        ERPNextHistoricalSampleAdapter(
            base_url="https://example.test",
            tenant_id="customer-a",
            api_key="key",
            api_secret="secret",
            resource="Purchase Invoice",
            company="Example Company",
            fields=(
                "name",
                "company",
                "supplier",
                "docstatus",
            ),
            order_by="posting_date desc, name desc",
            opener=lambda *_args, **_kwargs: None,
        )


@pytest.mark.parametrize(
    "order_by",
    [
        "posting_date sideways",
        "posting_date desc,",
        "posting_date",
        "posting_date desc, posting_date asc",
        "posting_date desc, supplier.name asc",
    ],
)
def test_historical_sample_rejects_unsafe_ordering(
    order_by,
):
    with pytest.raises(
        ValueError,
    ):
        ERPNextHistoricalSampleAdapter(
            base_url="https://example.test",
            tenant_id="customer-a",
            api_key="key",
            api_secret="secret",
            resource="Purchase Invoice",
            company="Example Company",
            fields=FIELDS,
            order_by=order_by,
            opener=lambda *_args, **_kwargs: None,
        )
