"""Offline synthetic source that implements the existing Kernel discovery port."""

from __future__ import annotations

from orion.contracts import Evidence, EvidenceKind, Observation


class SyntheticConnector:
    """Produce one tenant-bound observation without credentials or network I/O."""

    def __init__(self, *, tenant_id: str) -> None:
        if not isinstance(tenant_id, str) or not tenant_id.strip():
            raise ValueError("tenant_id required")
        self._tenant_id = tenant_id

    def discover(self) -> tuple[Observation, ...]:
        return (Observation(Evidence(
            kind=EvidenceKind.SYSTEM_OBSERVATION,
            source="synthetic-connector-v1",
            payload={"synthetic": True},
            tenant_id=self._tenant_id,
        )),)
