import inspect
import json
import re
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import pytest

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.discovery import erpnext_historical_expansion
from orion.discovery.erpnext_historical_expansion import (
    EXPANSION_MAX_NEW_OBSERVATIONS,
    EXPANSION_WINDOW_SIZE,
    ERPNextHistoricalExpansionConfig,
    expand_erpnext_historical_sample,
)
from orion.history.evidence import HistoricalEvidenceBatch, HistoricalEvidenceError
from orion.learning import project_customer_patterns
from orion.stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore

TENANT = "synthetic-expansion-tenant"
RESOURCE = "Purchase Invoice"
COMPANY = "Synthetic Company"


class FakeResponse:
    def __init__(self, payload, url):
        self._body = json.dumps(payload).encode()
        self._url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self._body if size < 0 else self._body[:size]

    def geturl(self):
        return self._url


def config():
    return ERPNextHistoricalExpansionConfig(
        base_url="https://example.test",
        tenant_id=TENANT,
        company=COMPANY,
        api_key="synthetic-key",
        api_secret="synthetic-secret",
    )


def record(name, index):
    return {
        "name": name,
        "company": COMPANY,
        "supplier": f"Synthetic Supplier {index % 3}",
        "posting_date": f"2026-09-{(index % 20) + 1:02d}",
        "currency": "USD",
        "grand_total": index * 10,
        "due_date": f"2026-10-{(index % 20) + 1:02d}",
        "docstatus": 1,
    }


def remote_rows(names):
    return [record(name, index) for index, name in enumerate(names, start=1)]


def opener_factory(calls, names):
    def opener(request, timeout):
        calls.append(request)
        return FakeResponse({"data": remote_rows(names)}, request.full_url)

    return opener


def initial_batch():
    observations = tuple(
        Observation(
            evidence=Evidence(
                kind=EvidenceKind.API,
                source="synthetic-expansion-fixture",
                tenant_id=TENANT,
                observed_at=datetime(2026, 9, 1, tzinfo=UTC),
                payload={"resource": RESOURCE, "record": record(f"SYN-OLD-{index}", index)},
            )
        )
        for index in range(1, 6)
    )
    return HistoricalEvidenceBatch(
        tenant_id=TENANT,
        resource=RESOURCE,
        sequence=1,
        created_at=datetime(2026, 9, 1, tzinfo=UTC),
        observations=observations,
    )


def seed(path):
    store = SQLiteHistoricalEvidenceStore(path)
    store.append(initial_batch())
    return store


def expand(path, calls, names, **kwargs):
    return expand_erpnext_historical_sample(
        config(),
        database_path=path,
        opener=opener_factory(calls, names),
        clock=lambda: datetime(2026, 9, 2, tzinfo=UTC),
        **kwargs,
    )


def test_overlap_window_persists_twenty_new_and_reopens_with_pattern_projection(tmp_path):
    path = tmp_path / "expansion.sqlite3"
    seed(path)
    calls = []
    names = [f"SYN-OLD-{index}" for index in range(1, 6)] + [f"SYN-NEW-{index}" for index in range(1, 21)]

    summary = expand(path, calls, names)

    assert summary.remote_window_count == 25
    assert summary.skipped_known_count == 5
    assert summary.new_observation_count == 20
    assert summary.new_batch_sequence == 2
    assert summary.durable_batch_count_after == 2
    assert summary.durable_observation_count_after == 25
    assert len(calls) == 1
    reopened = SQLiteHistoricalEvidenceStore(path).load_all(tenant_id=TENANT, resource=RESOURCE)
    assert sum(len(batch.observations) for batch in reopened) == 25
    snapshot = project_customer_patterns(reopened, tenant_id=TENANT, resource=RESOURCE)
    assert snapshot.observation_count == 25
    assert len(snapshot.observation_ids) == 25
    assert len(snapshot.evidence_ids) == 25


def test_all_new_window_is_deterministically_capped_at_twenty(tmp_path):
    path = tmp_path / "all-new.sqlite3"
    seed(path)
    calls = []
    names = [f"SYN-OTHER-{index}" for index in range(1, 26)]

    summary = expand(path, calls, names)

    assert EXPANSION_WINDOW_SIZE == 25
    assert EXPANSION_MAX_NEW_OBSERVATIONS == 20
    assert summary.skipped_known_count == 0
    assert summary.new_observation_count == 20
    assert summary.durable_observation_count_after == 25
    persisted = SQLiteHistoricalEvidenceStore(path).load_all(tenant_id=TENANT, resource=RESOURCE)
    names_after = [item.evidence.payload["record"]["name"] for item in persisted[1].observations]
    assert names_after == names[:20]


def test_partial_overlap_persists_only_unseen_observations(tmp_path):
    path = tmp_path / "partial.sqlite3"
    seed(path)
    calls = []
    names = ["SYN-OLD-1", "SYN-OLD-2", "SYN-PARTIAL-1", "SYN-PARTIAL-2", "SYN-PARTIAL-3"]

    summary = expand(path, calls, names)

    assert summary.skipped_known_count == 2
    assert summary.new_observation_count == 3
    assert summary.durable_observation_count_after == 8


def test_zero_unseen_fails_closed_after_one_http_without_append(tmp_path):
    path = tmp_path / "zero-new.sqlite3"
    seed(path)
    calls = []

    with pytest.raises(HistoricalEvidenceError, match="observations"):
        expand(path, calls, [f"SYN-OLD-{index}" for index in range(1, 6)])

    assert len(calls) == 1
    history = SQLiteHistoricalEvidenceStore(path).load_all(tenant_id=TENANT, resource=RESOURCE)
    assert len(history) == 1


def test_first_expansion_preconditions_fail_before_http(tmp_path):
    empty_path = tmp_path / "empty.sqlite3"
    calls = []
    with pytest.raises(HistoricalEvidenceError, match="five-observation"):
        expand(empty_path, calls, [])
    assert calls == []

    wrong_path = tmp_path / "wrong.sqlite3"
    store = SQLiteHistoricalEvidenceStore(wrong_path)
    batch = initial_batch()
    store.append(HistoricalEvidenceBatch(
        tenant_id=TENANT,
        resource=RESOURCE,
        sequence=1,
        created_at=batch.created_at,
        observations=batch.observations[:4],
    ))
    with pytest.raises(HistoricalEvidenceError, match="five-observation"):
        expand(wrong_path, calls, [])
    assert calls == []


def test_second_first_expansion_invocation_fails_before_http(tmp_path):
    path = tmp_path / "second.sqlite3"
    seed(path)
    calls = []
    expand(path, calls, [f"SYN-NEW-{index}" for index in range(1, 21)])
    calls.clear()

    with pytest.raises(HistoricalEvidenceError, match="five-observation"):
        expand(path, calls, [])
    assert calls == []


def test_duplicate_existing_history_fails_before_http(tmp_path):
    path = tmp_path / "duplicate-existing.sqlite3"
    store = SQLiteHistoricalEvidenceStore(path)
    batch = initial_batch()
    store.append(batch)
    # A malformed second batch is rejected before any remote request.
    duplicate = HistoricalEvidenceBatch(
        tenant_id=TENANT,
        resource=RESOURCE,
        sequence=2,
        created_at=batch.created_at,
        observations=batch.observations,
    )
    store.append(duplicate)
    calls = []
    with pytest.raises(HistoricalEvidenceError, match="duplicate document identity"):
        expand(path, calls, [])
    assert calls == []


def test_duplicate_remote_identity_is_rejected_by_sampler(tmp_path):
    path = tmp_path / "duplicate-window.sqlite3"
    seed(path)
    calls = []

    with pytest.raises(RuntimeError, match="duplicate document identity"):
        expand(path, calls, ["SYN-DUPLICATE"] * 2)
    assert len(calls) == 1
    assert len(SQLiteHistoricalEvidenceStore(path).load_all(tenant_id=TENANT, resource=RESOURCE)) == 1


def test_query_profile_and_exactly_one_get(tmp_path):
    path = tmp_path / "query.sqlite3"
    seed(path)
    calls = []
    expand(path, calls, [f"SYN-NEW-{index}" for index in range(1, 4)])
    request = calls[0]
    query = parse_qs(urlsplit(request.full_url).query)

    assert request.method == "GET"
    assert query["limit_page_length"] == ["25"]
    assert query["order_by"] == ["posting_date desc, name desc"]
    assert json.loads(query["filters"][0]) == [["company", "=", COMPANY], ["docstatus", "=", 1]]
    assert json.loads(query["fields"][0]) == [
        "name", "company", "supplier", "posting_date", "currency", "grand_total", "due_date", "docstatus"
    ]
    assert len(calls) == 1


def test_safe_summary_excludes_synthetic_raw_values_and_uuids(tmp_path):
    path = tmp_path / "safe.sqlite3"
    seed(path)
    calls = []
    summary = expand(path, calls, ["SYN-NEW-1"])
    rendered = repr(summary)

    assert "Synthetic Company" not in rendered
    assert "Synthetic Supplier" not in rendered
    assert "SYN-NEW" not in rendered
    assert "synthetic-key" not in rendered
    assert "synthetic-secret" not in rendered
    assert "execution_allowed=False" in rendered
    assert summary.recommendation_allowed is False
    assert summary.promotion_allowed is False


def test_in_repository_database_path_rejects_before_http_and_creation():
    path = Path.cwd() / "expansion-should-not-exist.sqlite3"
    calls = []
    with pytest.raises(ValueError, match="outside the Git repository"):
        expand(path, calls, [])
    assert calls == []
    assert not path.exists()


def test_persistence_failure_propagates_without_retry(monkeypatch, tmp_path):
    def fail(source, *args, **kwargs):
        source.discover()
        raise RuntimeError("synthetic append failure")

    monkeypatch.setattr(erpnext_historical_expansion, "persist_historical_sample", fail)
    path = tmp_path / "append-failure.sqlite3"
    seed(path)
    calls = []
    with pytest.raises(RuntimeError, match="append failure"):
        expand(path, calls, ["SYN-NEW-1"])
    assert len(calls) == 1


def test_expansion_module_has_no_forbidden_capabilities():
    source = inspect.getsource(erpnext_historical_expansion).lower()
    for forbidden in ("post", "put", "patch", "delete", "checkpoint", "promotion", "recommendation"):
        assert re.search(rf"\\b{forbidden}\\b", source) is None
    assert "execution_allowed=true" not in source
