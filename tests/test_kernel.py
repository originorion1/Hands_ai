from types import MappingProxyType

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


def test_failed_connector_iteration_cannot_publish_partial_evidence() -> None:
    class FailingAdapter:
        def discover(self):
            yield from FakeAdapter("tenant-a").discover()
            raise RuntimeError("synthetic connector fault")

    store = InMemoryEvidenceStore()
    kernel = OrionKernel(evidence_store=store)
    with pytest.raises(RuntimeError, match="synthetic connector fault"):
        kernel.discover(FailingAdapter(), tenant_id="tenant-a")
    assert store.query() == ()


def test_kernel_uses_atomic_store_commit_and_preserves_prior_evidence_on_failure() -> None:
    class FailingBatchStore(InMemoryEvidenceStore):
        def append(self, evidence):
            raise AssertionError("sequential append must not be used")

        def append_batch_atomic(self, evidence):
            assert len(evidence) == 2
            raise RuntimeError("synthetic transaction failure")

    store = FailingBatchStore()
    prior = next(FakeAdapter("tenant-a").discover()).evidence
    InMemoryEvidenceStore.append(store, prior)
    kernel = OrionKernel(evidence_store=store)
    with pytest.raises(RuntimeError, match="synthetic transaction failure"):
        kernel.discover(FakeAdapter("tenant-a", "tenant-a"), tenant_id="tenant-a")
    assert store.query() == (prior,)


def test_store_without_atomic_commit_is_rejected_before_discovery() -> None:
    class SequentialOnlyStore:
        def append(self, evidence):
            raise AssertionError("must never be called")

        def query(self, *, tenant_id=None):
            return ()

    with pytest.raises(TypeError, match="atomic batch append"):
        OrionKernel(evidence_store=SequentialOnlyStore())


def test_in_memory_atomic_batch_retains_complete_existing_and_new_evidence() -> None:
    store = InMemoryEvidenceStore()
    kernel = OrionKernel(evidence_store=store)
    assert kernel.discover(FakeAdapter("tenant-a"), tenant_id="tenant-a") == 1
    assert kernel.discover(FakeAdapter("tenant-a", "tenant-a"), tenant_id="tenant-a") == 2
    assert len(store.query(tenant_id="tenant-a")) == 3


def test_connector_and_reader_cannot_mutate_stored_nested_evidence() -> None:
    payload = {"record": {"status": "original"}}

    class MutableAdapter:
        def discover(self):
            yield Observation(evidence=Evidence(
                kind=EvidenceKind.METADATA, source="synthetic", payload=payload,
                tenant_id="tenant-a",
            ))

    store = InMemoryEvidenceStore()
    OrionKernel(evidence_store=store).discover(MutableAdapter(), tenant_id="tenant-a")
    payload["record"]["status"] = "connector-changed"
    assert store.query(tenant_id="tenant-a")[0].payload["record"]["status"] == "original"

    visible = store.query(tenant_id="tenant-a")[0]
    visible.payload["record"]["status"] = "reader-changed"
    assert store.query(tenant_id="tenant-a")[0].payload["record"]["status"] == "original"


def test_uncopyable_second_record_rejects_whole_batch_and_preserves_prior_evidence() -> None:
    class Uncopyable:
        def __deepcopy__(self, memo):
            raise RuntimeError("private payload error")

    store = InMemoryEvidenceStore()
    prior = next(FakeAdapter("tenant-a").discover()).evidence
    store.append(prior)
    ordinary = next(FakeAdapter("tenant-a").discover()).evidence
    bad = Evidence(
        kind=EvidenceKind.METADATA, source="synthetic",
        payload={"record": Uncopyable()}, tenant_id="tenant-a",
    )
    with pytest.raises(ValueError, match="evidence snapshot unavailable"):
        store.append_batch_atomic((ordinary, bad))
    assert store.query(tenant_id="tenant-a") == (prior,)


def test_immutable_mapping_payload_is_preserved_without_aliasing() -> None:
    record = {"value": "original"}
    evidence = Evidence(
        kind=EvidenceKind.METADATA, source="synthetic",
        payload=MappingProxyType({"record": MappingProxyType(record)}),
        tenant_id="tenant-a",
    )
    store = InMemoryEvidenceStore()
    store.append(evidence)
    record["value"] = "changed"
    assert store.query(tenant_id="tenant-a")[0].payload["record"]["value"] == "original"


def test_cyclic_payload_is_rejected_without_publishing_evidence() -> None:
    cyclic = {}
    cyclic["self"] = cyclic
    evidence = Evidence(
        kind=EvidenceKind.METADATA, source="synthetic", payload=cyclic,
        tenant_id="tenant-a",
    )
    store = InMemoryEvidenceStore()
    with pytest.raises(ValueError, match="evidence snapshot unavailable"):
        store.append_batch_atomic((evidence,))
    assert store.query() == ()
