from orion.contracts import Evidence, EvidenceKind, Observation
from orion.kernel import OrionKernel
from orion.stores.memory import InMemoryEvidenceStore


class FakeAdapter:
    def discover(self):
        yield Observation(
            evidence=Evidence(
                kind=EvidenceKind.METADATA,
                source="test",
                payload={"system": "example"},
                tenant_id="tenant-a",
            )
        )


def test_kernel_ingests_discovery_evidence():
    store = InMemoryEvidenceStore()
    kernel = OrionKernel(evidence_store=store)

    assert kernel.discover(FakeAdapter()) == 1
    assert len(store.query(tenant_id="tenant-a")) == 1
