"""Runnable, read-only discovery vertical slice."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .demo_adapter import DemoDiscoveryAdapter
from .snapshot import DiscoverySnapshot


@dataclass(frozen=True, slots=True)
class DiscoveryRun:
    snapshot: DiscoverySnapshot

    @property
    def observations(self):
        return self.snapshot.to_observations()


class DiscoveryRunner:
    """Coordinates an adapter without giving it execution capabilities."""

    def __init__(self, adapter: DemoDiscoveryAdapter) -> None:
        self._adapter = adapter

    def run(self, *, tenant_id: str, observed_at: datetime) -> DiscoveryRun:
        return DiscoveryRun(
            snapshot=self._adapter.discover(
                tenant_id=tenant_id,
                observed_at=observed_at,
            )
        )
