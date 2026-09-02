import json
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlsplit

import pytest

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.discovery.erpnext_historical_capture import FIRST_CAPTURE_FIELDS
from orion.discovery.erpnext_historical_corpus_expansion import (
    CORPUS_MAX_NEW_OBSERVATIONS,
    CORPUS_WINDOW_SIZE,
    expand_erpnext_historical_corpus,
)
from orion.discovery.erpnext_historical_expansion import ERPNextHistoricalExpansionConfig
from orion.discovery.erpnext_historical_sample import ERPNextHistoricalSampleAdapter
from orion.history.evidence import HistoricalEvidenceBatch, HistoricalEvidenceError
from orion.learning import project_customer_patterns
from orion.learning.shadow_backtest import run_shadow_backtest
from orion.stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore

TENANT = "synthetic-corpus-tenant"
COMPANY = "Synthetic Corpus Company"
RESOURCE = "Purchase Invoice"


class Response:
    def __init__(self, payload, url):
        self.body = json.dumps(payload).encode()
        self.url = url

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=-1):
        return self.body if size < 0 else self.body[:size]

    def geturl(self):
        return self.url


def config():
    return ERPNextHistoricalExpansionConfig("https://example.test", TENANT, COMPANY, "synthetic-key", "synthetic-secret")


def row(index, name=None):
    return {"name": name or f"CORPUS-{index:03d}", "company": COMPANY, "supplier": f"Supplier {index % 5}", "posting_date": f"2026-01-{index % 28 + 1:02d}", "currency": "USD", "grand_total": index, "due_date": f"2026-02-{index % 28 + 1:02d}", "docstatus": 1}


def observation(index, name=None):
    return Observation(Evidence(EvidenceKind.API, "synthetic-corpus", {"resource": RESOURCE, "record": row(index, name)}, datetime(2026, 1, 1, tzinfo=UTC), tenant_id=TENANT))


def seed(path):
    store = SQLiteHistoricalEvidenceStore(path)
    store.append(HistoricalEvidenceBatch(TENANT, RESOURCE, 1, datetime(2026, 1, 1, tzinfo=UTC), tuple(observation(i) for i in range(1, 13))))
    store.append(HistoricalEvidenceBatch(TENANT, RESOURCE, 2, datetime(2026, 1, 2, tzinfo=UTC), tuple(observation(i) for i in range(13, 26))))


def run(path, names, calls):
    def opener(request, timeout):
        calls.append(request)
        return Response({"data": [row(i, name) for i, name in enumerate(names, 1)]}, request.full_url)

    return expand_erpnext_historical_corpus(config(), database_path=path, opener=opener, clock=lambda: datetime(2026, 1, 3, tzinfo=UTC))


def test_overlap_persists_75_and_reopens_100_with_projections(tmp_path):
    path = tmp_path / "corpus.sqlite3"
    seed(path)
    calls = []
    names = [f"CORPUS-{i:03d}" for i in range(1, 26)] + [f"NEW-{i:03d}" for i in range(1, 76)]
    summary = run(path, names, calls)
    assert summary.new_observation_count == 75 and summary.remote_window_count == 100 and len(calls) == 1
    reopened = SQLiteHistoricalEvidenceStore(path).load_all(tenant_id=TENANT, resource=RESOURCE)
    assert len(reopened) == 3 and sum(len(batch.observations) for batch in reopened) == 100
    assert project_customer_patterns(reopened, tenant_id=TENANT, resource=RESOURCE).observation_count == 100
    assert run_shadow_backtest(reopened, tenant_id=TENANT, resource=RESOURCE).observation_count == 100


def test_all_new_is_capped_at_75_in_remote_order(tmp_path):
    path = tmp_path / "all-new.sqlite3"
    seed(path)
    names = [f"ALL-{i:03d}" for i in range(100)]
    run(path, names, [])
    batch = SQLiteHistoricalEvidenceStore(path).load_all(tenant_id=TENANT, resource=RESOURCE)[2]
    assert len(batch.observations) == CORPUS_MAX_NEW_OBSERVATIONS
    assert batch.observations[0].evidence.payload["record"]["name"] == names[0]


def test_partial_overlap(tmp_path):
    path = tmp_path / "partial.sqlite3"
    seed(path)
    calls = []
    summary = run(path, ["CORPUS-001", "CORPUS-002", "NEW-1"], calls)
    assert summary.new_observation_count == 1 and summary.skipped_known_count == 2
    assert len(calls) == 1


def test_zero_overlap_from_exact_start_fails_after_one_get_without_append(tmp_path):
    path = tmp_path / "zero.sqlite3"
    seed(path)
    calls = []
    with pytest.raises(HistoricalEvidenceError):
        run(path, [f"CORPUS-{i:03d}" for i in range(1, 26)], calls)
    assert len(calls) == 1
    reopened = SQLiteHistoricalEvidenceStore(path).load_all(tenant_id=TENANT, resource=RESOURCE)
    assert len(reopened) == 2 and sum(len(batch.observations) for batch in reopened) == 25


def test_preconditions_and_second_invocation_fail_before_http(tmp_path):
    path = tmp_path / "bad.sqlite3"
    seed(path)
    calls = []
    run(path, [f"NEW-{i}" for i in range(100)], calls)
    with pytest.raises(HistoricalEvidenceError):
        run(path, [], calls)
    assert len(calls) == 1


def test_query_profile_and_fixed_adapter_limit(tmp_path):
    path = tmp_path / "query.sqlite3"
    seed(path)
    calls = []
    run(path, [f"NEW-{i}" for i in range(100)], calls)
    request = calls[0]
    parts = urlsplit(request.full_url)
    query = parse_qs(parts.query)
    assert request.method == "GET"
    assert unquote(parts.path.rsplit("/", 1)[-1]) == RESOURCE
    assert json.loads(query["fields"][0]) == list(FIRST_CAPTURE_FIELDS)
    assert json.loads(query["filters"][0]) == [["company", "=", COMPANY], ["docstatus", "=", 1]]
    assert query["limit_start"] == ["0"]
    assert query["limit_page_length"] == ["100"]
    assert query["order_by"] == ["posting_date desc, name desc"]
    assert CORPUS_WINDOW_SIZE == 100 and CORPUS_MAX_NEW_OBSERVATIONS == 75
    with pytest.raises(ValueError):
        ERPNextHistoricalSampleAdapter(base_url="https://x", tenant_id=TENANT, api_key="k", api_secret="s", resource=RESOURCE, company=COMPANY, fields=("name", "company", "docstatus"), sample_size=26)
    import inspect

    from orion.discovery import erpnext_historical_corpus_expansion as module
    assert "resource" not in inspect.signature(module._ERPNextPurchaseInvoiceCorpusAdapter).parameters
    assert "fields" not in inspect.signature(module._ERPNextPurchaseInvoiceCorpusAdapter).parameters
    assert "order_by" not in inspect.signature(module._ERPNextPurchaseInvoiceCorpusAdapter).parameters
    assert "sample_size" not in inspect.signature(module._ERPNextPurchaseInvoiceCorpusAdapter).parameters


def test_wrong_state_duplicate_and_repo_path_fail_before_http(tmp_path):
    path = tmp_path / "wrong.sqlite3"
    store = SQLiteHistoricalEvidenceStore(path)
    store.append(HistoricalEvidenceBatch(TENANT, RESOURCE, 1, datetime(2026, 1, 1, tzinfo=UTC), tuple(observation(i) for i in range(1, 6))))
    calls = []
    with pytest.raises(HistoricalEvidenceError):
        run(path, [], calls)
    assert not calls
    with pytest.raises(ValueError):
        run(Path.cwd() / "no-corpus.sqlite3", [], calls)


def test_persistence_failure_no_ack_or_retry(monkeypatch, tmp_path):
    path = tmp_path / "failure.sqlite3"
    seed(path)
    import orion.discovery.erpnext_historical_corpus_expansion as module

    def fail(source, *args, **kwargs):
        source.discover()
        raise RuntimeError("append failure")

    monkeypatch.setattr(module, "persist_historical_sample", fail)
    calls = []
    with pytest.raises(RuntimeError):
        run(path, [f"NEW-{i}" for i in range(100)], calls)
    assert len(calls) == 1


def test_safe_summary_and_capability_surface(tmp_path):
    path = tmp_path / "safe.sqlite3"
    seed(path)
    summary = run(path, [f"NEW-{i}" for i in range(100)], [])
    rendered = repr(summary)
    assert COMPANY not in rendered and "synthetic-key" not in rendered and "NEW-" not in rendered
    source = Path("src/orion/discovery/erpnext_historical_corpus_expansion.py").read_text().lower()
    assert "post(" not in source and "put(" not in source and "delete(" not in source and "checkpoint" not in source
