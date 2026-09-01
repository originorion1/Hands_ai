from datetime import UTC, datetime

import pytest

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.history.evidence import HistoricalEvidenceBatch, HistoricalEvidenceError
from orion.history.sampling import (
    PersistedHistoricalSample,
    persist_historical_sample,
)

TENANT = "customer-a"
RESOURCE = "Purchase Invoice"


def make_observation(name="PINV-001"):
    return Observation(
        evidence=Evidence(
            kind=EvidenceKind.API,
            source="offline-test",
            tenant_id=TENANT,
            observed_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
            payload={"resource": RESOURCE, "record": {"name": name}},
        )
    )


def make_batch(sequence=1, name="PINV-001"):
    return HistoricalEvidenceBatch(
        tenant_id=TENANT,
        resource=RESOURCE,
        sequence=sequence,
        created_at=datetime(2026, 9, 2, 11, sequence, tzinfo=UTC),
        observations=(make_observation(name),),
    )


class Source:
    def __init__(self, result):
        self.result = result
        self.calls = 0

    def discover(self):
        self.calls += 1
        return self.result


class Store:
    def __init__(self, history=()):
        self.history = history
        self.loads = []
        self.appended = []

    def load_all(self, *, tenant_id, resource):
        self.loads.append((tenant_id, resource))
        return self.history

    def append(self, batch):
        self.appended.append(batch)
        self.history = self.history + (batch,)


class OrderedStore(Store):
    def __init__(self, source):
        super().__init__()
        self.source = source

    def load_all(self, *, tenant_id, resource):
        self.loads.append((tenant_id, resource))
        if len(self.loads) == 1:
            assert self.source.calls == 0
        return self.history


def persist(source, store):
    return persist_historical_sample(
        source,
        store,
        tenant_id=TENANT,
        resource=RESOURCE,
        clock=lambda: datetime(2026, 9, 2, 12, tzinfo=UTC),
    )


def test_preflights_history_before_single_discovery_and_persists_sequence_one():
    source = Source((make_observation(),))
    store = OrderedStore(source)

    acknowledgement = persist(source, store)

    assert source.calls == 1
    assert store.loads == [(TENANT, RESOURCE), (TENANT, RESOURCE)]
    assert store.appended[0].sequence == 1
    assert isinstance(acknowledgement, PersistedHistoricalSample)


def test_existing_verified_history_increments_sequence_and_preserves_ids():
    prior = make_batch()
    sample = make_observation("PINV-002")
    source = Source((sample,))
    store = Store((prior,))

    acknowledgement = persist(source, store)

    assert acknowledgement.sequence == 2
    assert store.appended[0].observations == (sample,)
    assert acknowledgement.observation_ids == (sample.observation_id,)
    assert acknowledgement.evidence_ids == (sample.evidence.evidence_id,)


def test_empty_sample_fails_closed_without_append():
    source = Source(())
    store = Store()
    with pytest.raises(HistoricalEvidenceError, match="observations"):
        persist(source, store)
    assert source.calls == 1
    assert store.appended == []


def test_non_tuple_result_fails_closed():
    source = Source([make_observation()])
    store = Store()
    with pytest.raises(HistoricalEvidenceError, match="tuple"):
        persist(source, store)
    assert store.appended == []


def test_cross_tenant_and_resource_mismatch_fail_via_batch():
    cross_tenant = Observation(
        evidence=Evidence(
            kind=EvidenceKind.API,
            source="offline-test",
            tenant_id="customer-b",
            observed_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
            payload={"resource": RESOURCE, "record": {"name": "PINV-001"}},
        )
    )

    source = Source((cross_tenant,))
    with pytest.raises(HistoricalEvidenceError, match="tenant"):
        persist(source, Store())

    mismatched = Observation(
        evidence=Evidence(
            kind=EvidenceKind.API,
            source="offline-test",
            tenant_id=TENANT,
            observed_at=datetime(2026, 9, 2, 10, tzinfo=UTC),
            payload={"resource": "Sales Invoice", "record": {"name": "SINV-001"}},
        )
    )
    with pytest.raises(HistoricalEvidenceError, match="resource"):
        persist(Source((mismatched,)), Store())


def test_append_failure_propagates_without_acknowledgement():
    class FailingStore(Store):
        def append(self, batch):
            raise RuntimeError("append failed")

    with pytest.raises(RuntimeError, match="append failed"):
        persist(Source((make_observation(),)), FailingStore())


def test_reload_failure_and_mismatch_fail_closed():
    class ReloadFailStore(Store):
        def load_all(self, *, tenant_id, resource):
            result = super().load_all(tenant_id=tenant_id, resource=resource)
            if len(self.loads) == 2:
                raise RuntimeError("reload failed")
            return result

    with pytest.raises(RuntimeError, match="reload failed"):
        persist(Source((make_observation(),)), ReloadFailStore())

    class MismatchStore(Store):
        def load_all(self, *, tenant_id, resource):
            self.loads.append((tenant_id, resource))
            return () if len(self.loads) == 2 else self.history

    with pytest.raises(HistoricalEvidenceError, match="reload"):
        persist(Source((make_observation(),)), MismatchStore())


def test_preflight_integrity_failure_prevents_discovery():
    source = Source((make_observation(),))

    class BrokenStore(Store):
        def load_all(self, **kwargs):
            raise RuntimeError("integrity failure")

    with pytest.raises(RuntimeError, match="integrity"):
        persist(source, BrokenStore())
    assert source.calls == 0


def test_sequence_conflict_propagates_without_retry_or_second_discovery():
    class ConflictStore(Store):
        def append(self, batch):
            raise RuntimeError("sequence conflict")

    source = Source((make_observation(),))
    with pytest.raises(RuntimeError, match="sequence conflict"):
        persist(source, ConflictStore())
    assert source.calls == 1


def test_acknowledgement_contains_only_persistence_facts():
    acknowledgement = persist(Source((make_observation(),)), Store())
    fields = set(acknowledgement.__dataclass_fields__)

    assert fields == {
        "tenant_id",
        "resource",
        "sequence",
        "observation_count",
        "observation_ids",
        "evidence_ids",
    }


def test_neutral_module_has_no_erpnext_dependency():
    import inspect

    from orion.history import sampling

    assert "erpnext" not in inspect.getsource(sampling).lower()
