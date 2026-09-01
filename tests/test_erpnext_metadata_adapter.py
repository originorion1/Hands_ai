import json
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from orion.contracts import EvidenceKind, ObservationMode
from orion.discovery.erpnext_metadata_adapter import ERPNextMetadataAdapter


class FakeResponse:
    def __init__(self, payload, *, url=None):
        self._payload = json.dumps(payload).encode()
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


def test_metadata_adapter_normalizes_read_only_metadata():
    captured = {}

    def opener(request, timeout):
        captured["method"] = request.method
        captured["url"] = request.full_url
        captured["authorization"] = request.headers["Authorization"]
        captured["timeout"] = timeout

        return FakeResponse(
            {
                "message": {
                    "docs": [
                        {
                            "name": "Company",
                            "module": "Setup",
                        }
                    ]
                }
            },
            url=request.full_url,
        )

    adapter = ERPNextMetadataAdapter(
        base_url="https://example.test",
        tenant_id="customer-a",
        api_key="key",
        api_secret="secret",
        doctypes=("Company",),
        opener=opener,
    )

    observations = adapter.discover()

    assert len(observations) == 1

    observation = observations[0]

    assert observation.mode is ObservationMode.READ_ONLY
    assert observation.evidence.kind is EvidenceKind.METADATA
    assert observation.evidence.tenant_id == "customer-a"
    assert observation.evidence.source == "erpnext-metadata-read-only"
    assert observation.evidence.payload["doctype"] == "Company"

    metadata = observation.evidence.payload["metadata"]
    assert metadata["message"]["docs"][0]["name"] == "Company"

    parsed = urlsplit(captured["url"])
    query = parse_qs(parsed.query)

    assert captured["method"] == "GET"
    assert parsed.scheme == "https"
    assert parsed.netloc == "example.test"
    assert (
        parsed.path
        == "/api/method/frappe.desk.form.load.getdoctype"
    )
    assert query == {"doctype": ["Company"]}
    assert captured["authorization"] == "token key:secret"
    assert captured["timeout"] == 20


def test_metadata_adapter_reads_only_explicit_doctypes():
    requested = []

    def opener(request, timeout):
        query = parse_qs(urlsplit(request.full_url).query)
        doctype = query["doctype"][0]
        requested.append(doctype)

        return FakeResponse(
            {"message": {"docs": [{"name": doctype}]}},
            url=request.full_url,
        )

    adapter = ERPNextMetadataAdapter(
        base_url="https://example.test",
        tenant_id="customer-a",
        api_key="key",
        api_secret="secret",
        doctypes=("Company", "Customer"),
        opener=opener,
    )

    observations = adapter.discover()

    assert requested == ["Company", "Customer"]
    assert len(observations) == 2
    assert [
        observation.evidence.payload["doctype"]
        for observation in observations
    ] == ["Company", "Customer"]


@pytest.mark.parametrize(
    "doctype",
    [
        "../User",
        "Company?anything=*",
        "Company#fragment",
        " Company",
    ],
)
def test_metadata_adapter_rejects_unsafe_doctype_names(doctype):
    with pytest.raises(ValueError, match="resource"):
        ERPNextMetadataAdapter(
            base_url="https://example.test",
            tenant_id="customer-a",
            api_key="key",
            api_secret="secret",
            doctypes=(doctype,),
        )


def test_metadata_adapter_rejects_duplicate_doctypes():
    with pytest.raises(ValueError, match="unique"):
        ERPNextMetadataAdapter(
            base_url="https://example.test",
            tenant_id="customer-a",
            api_key="key",
            api_secret="secret",
            doctypes=("Company", "Company"),
        )


def test_metadata_adapter_rejects_excessive_doctype_count():
    doctypes = tuple(f"Type {index}" for index in range(101))

    with pytest.raises(ValueError, match="count"):
        ERPNextMetadataAdapter(
            base_url="https://example.test",
            tenant_id="customer-a",
            api_key="key",
            api_secret="secret",
            doctypes=doctypes,
        )


def test_metadata_adapter_rejects_oversized_response():
    def opener(request, timeout):
        return FakeResponse(
            {"message": {"value": "X" * 500}},
            url=request.full_url,
        )

    adapter = ERPNextMetadataAdapter(
        base_url="https://example.test",
        tenant_id="customer-a",
        api_key="key",
        api_secret="secret",
        doctypes=("Company",),
        max_response_bytes=64,
        opener=opener,
    )

    with pytest.raises(RuntimeError, match="response exceeds"):
        adapter.discover()


def test_metadata_adapter_rejects_non_object_response():
    def opener(request, timeout):
        return FakeResponse(
            ["not-an-object"],
            url=request.full_url,
        )

    adapter = ERPNextMetadataAdapter(
        base_url="https://example.test",
        tenant_id="customer-a",
        api_key="key",
        api_secret="secret",
        doctypes=("Company",),
        opener=opener,
    )

    with pytest.raises(TypeError, match="JSON object"):
        adapter.discover()


def test_metadata_adapter_fails_closed_on_network_error():
    def opener(request, timeout):
        raise URLError("network unavailable")

    adapter = ERPNextMetadataAdapter(
        base_url="https://example.test",
        tenant_id="customer-a",
        api_key="key",
        api_secret="secret",
        doctypes=("Company",),
        opener=opener,
    )

    with pytest.raises(RuntimeError, match="metadata discovery failed"):
        adapter.discover()


def test_metadata_adapter_rejects_redirected_response():
    def opener(request, timeout):
        return FakeResponse(
            {"message": {}},
            url=(
                "https://other.example.test/"
                "api/method/frappe.desk.form.load.getdoctype"
                "?doctype=Company"
            ),
        )

    adapter = ERPNextMetadataAdapter(
        base_url="https://example.test",
        tenant_id="customer-a",
        api_key="key",
        api_secret="secret",
        doctypes=("Company",),
        opener=opener,
    )

    with pytest.raises(RuntimeError, match="redirects are not allowed"):
        adapter.discover()
