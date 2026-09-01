"""Bind discovery context to an adapter so the Kernel sees one stable port."""

from __future__ import annotations

from datetime import datetime

from .http_adapter import ReadOnlyHttpDiscoveryAdapter


class BoundReadOnlyDiscoveryAdapter:
    """Adapts a contextual discovery source to the Kernel DiscoveryAdapter port."""

    def __init__(
        self,
        source: ReadOnlyHttpDiscoveryAdapter,
        *,
        tenant_id: str,
        observed_at: datetime,
    ) -> None:
        self._source = source
        self._tenant_id = tenant_id
        self._observed_at = observed_at

    def discover(self):
        return self._source.discover(
            tenant_id=self._tenant_id,
            observed_at=self._observed_at,
        ).to_observations()
