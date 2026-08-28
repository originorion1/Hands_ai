from datetime import datetime, timezone
from uuid import uuid4

import pytest

from orion.discovery.snapshot import DiscoveredObject, DiscoverySnapshot


def test_snapshot_normalizes_objects_to_observations() -> None:
    snapshot = DiscoverySnapshot(
        tenant_id="customer-a",
        source_system="demo-system",
        observed_at=datetime(2026, 8, 28, 10, 0, tzinfo=timezone.utc),
        objects=(
            DiscoveredObject(
                object_id=uuid4(),
                object_type="system",
                name="Demo",
                attributes={"version": "1"},
            ),
        ),
    )

    observations = snapshot.to_observations()
    assert len(observations) == 1
    assert observations[0].evidence.tenant_id == "customer-a"
    assert observations[0].evidence.source == "demo-system"
    assert observations[0].evidence.payload["name"] == "Demo"


def test_snapshot_rejects_naive_timestamp() -> None:
    snapshot = DiscoverySnapshot(
        tenant_id="customer-a",
        source_system="demo-system",
        observed_at=datetime(2026, 8, 28, 10, 0),
        objects=(),
    )
    with pytest.raises(ValueError, match="timezone-aware"):
        snapshot.to_observations()
