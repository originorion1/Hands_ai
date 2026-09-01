import json
from urllib.error import URLError
from urllib.parse import parse_qs, urlsplit

import pytest

from orion.contracts import ObservationMode
from orion.discovery.erpnext_adapter import ERPNextDiscoveryAdapter


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


def test_erpnext_adapter_normalizes_read_only_records():
    captured = {}

    def opener(request, timeout):
        captured["method"] = request.method
        captured["url"] = request.full_url
        captured["authorization"] = request.headers["Authorization"]
        captured["timeout"] = timeout
        return FakeResponse(
            {"data": [{"name": "INV-001", "docstatus": 1}]},
            url=request.full_url,
        )

    adapter = ERPNextDiscoveryAdapter(
        base_url="https://example.test",
        tenant_id="customer-a",
        api_key="key",
        api_secret="secret",
        resources=("Sales Invoice",),
        opener=opener,
    )

    observations = adapter.discover()

    parsed = urlsplit(captured["url"])
    query = parse_qs(parsed.query)

    assert len(observations) == 1
    assert observations[0].mode is ObservationMode.READ_ONLY
    assert observations[0].evidence.tenant_id == "customer-a"
    assert observations[0].evidence.payload["record"]["name"] == "INV-001"

    assert captured["method"] == "GET"
    assert parsed.scheme == "https"
    assert parsed.netloc == "example.test"
    assert parsed.path == "/api/resource/Sales%20Invoice"
    assert query == {
        "limit_start": ["0"],
        "limit_page_length": ["100"],
    }
    assert captured["authorization"] == "token key:secret"
    assert captured["timeout"] == 20


def test_erpnext_adapter_paginates_until_short_page():
    urls = []
    responses = [
        {"data": [{"name": "INV-001"}, {"name": "INV-002"}]},
        {"data": [{"name": "INV-003"}]},
    ]

    def opener(request, timeout):
        urls.append(request.full_url)
        return FakeResponse(
            responses.pop(0),
            url=request.full_url,
        )

    adapter = ERPNextDiscoveryAdapter(
        base_url="https://example.test",
        tenant_id="customer-a",
        api_key="key",
        api_secret="secret",
        resources=("Sales Invoice",),
        page_size=2,
        max_pages=3,
        opener=opener,
    )

    observations = adapter.discover()

    assert [
        observation.evidence.payload["record"]["name"]
        for observation in observations
    ] == ["INV-001", "INV-002", "INV-003"]

    starts = [
        parse_qs(urlsplit(url).query)["limit_start"][0]
        for url in urls
    ]
    assert starts == ["0", "2"]


def test_erpnext_adapter_fails_if_pagination_bound_prevents_completeness():
    calls = 0

    def opener(request, timeout):
        nonlocal calls
        calls += 1
        return FakeResponse(
            {"data": [{"name": f"INV-{calls:03d}"}]},
            url=request.full_url,
        )

    adapter = ERPNextDiscoveryAdapter(
        base_url="https://example.test",
        tenant_id="customer-a",
        api_key="key",
        api_secret="secret",
        resources=("Sales Invoice",),
        page_size=1,
        max_pages=2,
        opener=opener,
    )

    with pytest.raises(RuntimeError, match="pagination limit"):
        adapter.discover()

    assert calls == 2


@pytest.mark.parametrize(
    "base_url",
    [
        "http://example.test",
        "https://user:password@example.test",
        "https://example.test/erp",
        "https://example.test?redirect=elsewhere",
    ],
)
def test_erpnext_adapter_rejects_unsafe_base_url(base_url):
    with pytest.raises(ValueError, match="base_url"):
        ERPNextDiscoveryAdapter(
            base_url=base_url,
            tenant_id="customer-a",
            api_key="key",
            api_secret="secret",
        )


@pytest.mark.parametrize(
    "field",
    ["tenant_id", "api_key", "api_secret"],
)
def test_erpnext_adapter_requires_identity_and_credentials(field):
    kwargs = {
        "base_url": "https://example.test",
        "tenant_id": "customer-a",
        "api_key": "key",
        "api_secret": "secret",
    }
    kwargs[field] = ""

    with pytest.raises(ValueError, match=field):
        ERPNextDiscoveryAdapter(**kwargs)


@pytest.mark.parametrize(
    "resource",
    [
        "../User",
        "DocType?fields=*",
        "DocType#fragment",
    ],
)
def test_erpnext_adapter_rejects_unsafe_resource_names(resource):
    with pytest.raises(ValueError, match="resource"):
        ERPNextDiscoveryAdapter(
            base_url="https://example.test",
            tenant_id="customer-a",
            api_key="key",
            api_secret="secret",
            resources=(resource,),
        )


def test_erpnext_adapter_rejects_oversized_response():
    def opener(request, timeout):
        return FakeResponse(
            {"data": [{"name": "X" * 500}]},
            url=request.full_url,
        )

    adapter = ERPNextDiscoveryAdapter(
        base_url="https://example.test",
        tenant_id="customer-a",
        api_key="key",
        api_secret="secret",
        max_response_bytes=64,
        opener=opener,
    )

    with pytest.raises(RuntimeError, match="response exceeds"):
        adapter.discover()


def test_erpnext_adapter_rejects_malformed_data_container():
    def opener(request, timeout):
        return FakeResponse(
            {"data": "not-a-list"},
            url=request.full_url,
        )

    adapter = ERPNextDiscoveryAdapter(
        base_url="https://example.test",
        tenant_id="customer-a",
        api_key="key",
        api_secret="secret",
        opener=opener,
    )

    with pytest.raises(TypeError, match="data must be a list"):
        adapter.discover()


def test_erpnext_adapter_rejects_non_object_rows():
    def opener(request, timeout):
        return FakeResponse(
            {"data": ["not-an-object"]},
            url=request.full_url,
        )

    adapter = ERPNextDiscoveryAdapter(
        base_url="https://example.test",
        tenant_id="customer-a",
        api_key="key",
        api_secret="secret",
        opener=opener,
    )

    with pytest.raises(TypeError, match="rows must be JSON objects"):
        adapter.discover()


def test_erpnext_adapter_fails_closed_on_network_error():
    def opener(request, timeout):
        raise URLError("network unavailable")

    adapter = ERPNextDiscoveryAdapter(
        base_url="https://example.test",
        tenant_id="customer-a",
        api_key="key",
        api_secret="secret",
        opener=opener,
    )

    with pytest.raises(RuntimeError, match="read-only discovery failed"):
        adapter.discover()


def test_erpnext_adapter_rejects_redirected_response():
    def opener(request, timeout):
        return FakeResponse(
            {"data": []},
            url="https://other.example.test/api/resource/DocType",
        )

    adapter = ERPNextDiscoveryAdapter(
        base_url="https://example.test",
        tenant_id="customer-a",
        api_key="key",
        api_secret="secret",
        opener=opener,
    )

    with pytest.raises(RuntimeError, match="redirects are not allowed"):
        adapter.discover()
