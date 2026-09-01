import pytest

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.kernel import OrionKernel, TenantBoundaryViolation
from orion.stores.memory import InMemoryEvidenceStore


class FakeAdapter:
    def __init__(self, *tenant_ids: str | None) -> None:
        self._tenant_ids = tenant_ids

    def discover(self):
        for tenant_id in self._tenant_ids:
            yield Observation(
                evidence=Evidence(
                    kind=EvidenceKind.METADATA,
                    source="test",
                    payload={"system": "example"},
                    tenant_id=tenant_id,
                )
            )


def test_kernel_ingests_discovery_evidence_for_authorized_tenant() -> None:
    store = InMemoryEvidenceStore()
    kernel = OrionKernel(evidence_store=store)

    assert kernel.discover(FakeAdapter("tenant-a"), tenant_id="tenant-a") == 1
    assert len(store.query(tenant_id="tenant-a")) == 1


def test_kernel_rejects_cross_tenant_evidence() -> None:
    store = InMemoryEvidenceStore()
    kernel = OrionKernel(evidence_store=store)

    with pytest.raises(TenantBoundaryViolation, match="tenant boundary violation"):
        kernel.discover(FakeAdapter("tenant-b"), tenant_id="tenant-a")

    assert store.query() == ()


def test_kernel_rejects_entire_mixed_tenant_batch_before_storage() -> None:
    store = InMemoryEvidenceStore()
    kernel = OrionKernel(evidence_store=store)

    with pytest.raises(TenantBoundaryViolation, match="tenant boundary violation"):
        kernel.discover(
            FakeAdapter("tenant-a", "tenant-b"),
            tenant_id="tenant-a",
        )

    assert store.query() == ()


def test_kernel_rejects_unscoped_evidence() -> None:
    store = InMemoryEvidenceStore()
    kernel = OrionKernel(evidence_store=store)

    with pytest.raises(TenantBoundaryViolation, match="tenant boundary violation"):
        kernel.discover(FakeAdapter(None), tenant_id="tenant-a")

    assert store.query() == ()
