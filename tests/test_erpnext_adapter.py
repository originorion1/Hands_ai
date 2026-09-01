import json

from orion.contracts import ObservationMode
from orion.discovery.erpnext_adapter import ERPNextDiscoveryAdapter


class FakeResponse:
    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self):
        return self._payload


def test_erpnext_adapter_normalizes_read_only_records():
    captured = {}

    def opener(request, timeout):
        captured["method"] = request.method
        captured["url"] = request.full_url
        captured["authorization"] = request.headers["Authorization"]
        captured["timeout"] = timeout
        return FakeResponse({"data": [{"name": "INV-001", "docstatus": 1}]})

    adapter = ERPNextDiscoveryAdapter(
        base_url="https://example.test",
        tenant_id="customer-a",
        api_key="key",
        api_secret="secret",
        resources=("Sales Invoice",),
        opener=opener,
    )

    observations = adapter.discover()

    assert len(observations) == 1
    assert observations[0].mode is ObservationMode.READ_ONLY
    assert observations[0].evidence.tenant_id == "customer-a"
    assert observations[0].evidence.payload["record"]["name"] == "INV-001"
    assert captured["method"] == "GET"
    assert captured["url"] == "https://example.test/api/resource/Sales%20Invoice"
    assert captured["timeout"] == 20
