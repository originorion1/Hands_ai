"""Ports: capabilities the kernel expects from adapters and stores."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Protocol

from .contracts import Evidence, Observation, ProposedAction, ShadowResult


class DiscoveryAdapter(Protocol):
    """Authorized observation surface for an external system."""

    def discover(self) -> Iterable[Observation]: ...


class EvidenceStore(Protocol):
    def append(self, evidence: Evidence) -> None: ...

    def query(self, *, tenant_id: str | None = None) -> Sequence[Evidence]: ...


class ShadowExecutor(Protocol):
    """Non-production executor. Implementations must not expose write paths."""

    def simulate(self, actions: Sequence[ProposedAction]) -> ShadowResult: ...
