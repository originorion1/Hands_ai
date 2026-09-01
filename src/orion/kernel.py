"""Minimal ORION kernel: orchestration only, no tenant-specific semantics."""

from __future__ import annotations

from .ports import DiscoveryAdapter, EvidenceStore


class OrionKernel:
    """Coordinates discovery while keeping adapters and storage replaceable."""

    def __init__(self, *, evidence_store: EvidenceStore) -> None:
        self._evidence_store = evidence_store

    def discover(self, adapter: DiscoveryAdapter) -> int:
        """Ingest authorized observations and return the number accepted."""
        accepted = 0
        for observation in adapter.discover():
            self._evidence_store.append(observation.evidence)
            accepted += 1
        return accepted
