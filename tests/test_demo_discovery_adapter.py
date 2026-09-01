from datetime import UTC, datetime

from orion.discovery.demo_adapter import DemoDiscoveryAdapter


def test_demo_adapter_is_deterministic_and_read_only() -> None:
    records = [
        {"type": "workflow", "name": "Purchase Invoice", "status": "active"},
        {"type": "role", "name": "Finance Manager"},
    ]
    adapter = DemoDiscoveryAdapter(records)
    observed_at = datetime(2026, 8, 28, 12, 0, tzinfo=UTC)

    first = adapter.discover(tenant_id="tenant-a", observed_at=observed_at)
    second = adapter.discover(tenant_id="tenant-a", observed_at=observed_at)

    assert first.objects == second.objects
    assert first.objects[0].name == "Purchase Invoice"
    assert first.objects[0].attributes["status"] == "active"
    assert records[0]["status"] == "active"
