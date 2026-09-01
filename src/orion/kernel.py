"""Minimal ORION kernel: orchestration only, no tenant-specific semantics."""

from __future__ import annotations

from .ports import DiscoveryAdapter, EvidenceStore


class TenantBoundaryViolation(RuntimeError):
    """Raised when discovery attempts to cross an authorized tenant boundary."""


class OrionKernel:
    """Coordinates discovery while keeping adapters and storage replaceable."""

    def __init__(self, *, evidence_store: EvidenceStore) -> None:
        self._evidence_store = evidence_store

    def discover(self, adapter: DiscoveryAdapter, *, tenant_id: str) -> int:
        """Ingest observations only when every item belongs to the authorized tenant."""
        if not tenant_id.strip():
            raise ValueError("tenant_id must be non-empty")

        observations = tuple(adapter.discover())

        # Validate the complete batch before mutating storage. A single mismatched
        # or unscoped observation rejects the entire discovery batch.
        for observation in observations:
            observed_tenant_id = observation.evidence.tenant_id
            if observed_tenant_id != tenant_id:
                raise TenantBoundaryViolation(
                    "discovery tenant boundary violation: "
                    f"expected {tenant_id!r}, got {observed_tenant_id!r}"
                )

        for observation in observations:
            self._evidence_store.append(observation.evidence)

        return len(observations)
