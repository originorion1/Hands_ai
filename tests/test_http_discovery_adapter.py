from datetime import datetime, timezone

import pytest

from orion.discovery.bound import BoundReadOnlyDiscoveryAdapter
from orion.discovery.http_adapter import (
    DiscoveryTransportError,
    ReadOnlyHttpDiscoveryAdapter,
)


def test_http_adapter_normalizes_json_without_write_capability() -> None:
    responses = {
        "https://example.test/workflows": b'{"data":[{"id":"wf-1","name":"Purchase Invoice","status":"active"}]}'
    }

    adapter = ReadOnlyHttpDiscoveryAdapter(
        base_url="https://example.test",
        paths=["/workflows"],
        fetcher=responses.__getitem__,
    )
    bound = BoundReadOnlyDiscoveryAdapter(
        adapter,
        tenant_id="customer-a",
        observed_at=datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc),
    )

    observations = tuple(bound.discover())
    assert len(observations) == 1
    assert observations[0].evidence.payload["name"] == "Purchase Invoice"
    assert observations[0].evidence.tenant_id == "customer-a"
    assert observations[0].mode.value == "read_only"


def test_http_adapter_rejects_non_relative_paths() -> None:
    with pytest.raises(ValueError, match="relative"):
        ReadOnlyHttpDiscoveryAdapter(
            base_url="https://example.test",
            paths=["https://evil.test/write"],
            fetcher=lambda _: b"{}",
        )


def test_http_adapter_rejects_non_json() -> None:
    adapter = ReadOnlyHttpDiscoveryAdapter(
        base_url="https://example.test",
        paths=["/metadata"],
        fetcher=lambda _: b"not-json",
    )
    with pytest.raises(DiscoveryTransportError, match="non-JSON"):
        adapter.discover(
            tenant_id="customer-a",
            observed_at=datetime(2026, 8, 28, 14, 0, tzinfo=timezone.utc),
        )
