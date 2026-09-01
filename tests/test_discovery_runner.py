from datetime import UTC, datetime

from orion.discovery.demo_adapter import DemoDiscoveryAdapter
from orion.discovery.runner import DiscoveryRunner


def test_runner_executes_read_only_discovery_vertical_slice() -> None:
    runner = DiscoveryRunner(
        DemoDiscoveryAdapter(
            [
                {"type": "workflow", "name": "Purchase Invoice", "status": "active"},
                {"type": "role", "name": "Finance Manager"},
            ]
        )
    )

    result = runner.run(
        tenant_id="customer-a",
        observed_at=datetime(2026, 8, 28, 13, 0, tzinfo=UTC),
    )

    assert len(result.observations) == 2
    assert {item.evidence.tenant_id for item in result.observations} == {"customer-a"}
    assert {item.evidence.kind.value for item in result.observations} == {"system_observation"}
