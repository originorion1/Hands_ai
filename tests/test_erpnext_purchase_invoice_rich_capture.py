import json
from datetime import UTC, datetime

import pytest

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.discovery.erpnext_historical_capture import FIRST_CAPTURE_FIELDS
from orion.discovery.erpnext_purchase_invoice_rich_capture import (
    ITEM_FIELDS,
    PARENT_FIELDS,
    PROFILE_ID,
    RichCaptureConfig,
    capture_purchase_invoice_rich_v1,
)
from orion.history.evidence import HistoricalEvidenceBatch, HistoricalEvidenceError
from orion.history.profiled_evidence import (
    ProfiledEvidenceBatch,
    profiled_evidence_checksum,
    profiled_evidence_from_json,
    profiled_evidence_to_json,
)
from orion.stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore
from orion.stores.sqlite_profiled_evidence import SQLiteProfiledEvidenceStore

TENANT = "synthetic-rich-tenant"
COMPANY = "Synthetic Rich Company"


class Response:
    def __init__(self, payload, url):
        self.body = json.dumps(payload).encode()
        self.url = url

    def __enter__(self): return self
    def __exit__(self, *args): return False
    def read(self, size=-1): return self.body if size < 0 else self.body[:size]
    def geturl(self): return self.url


def config():
    return RichCaptureConfig("https://example.test", TENANT, COMPANY, "synthetic-key", "synthetic-secret")


def header_obs(i):
    record = {"name": f"RICH-{i:03d}", "company": COMPANY, "supplier": "Synthetic Supplier", "posting_date": f"2026-01-{i % 28 + 1:02d}", "currency": "USD", "grand_total": i, "due_date": f"2026-02-{i % 28 + 1:02d}", "docstatus": 1}
    return Observation(Evidence(EvidenceKind.API, "synthetic", {"resource": "Purchase Invoice", "record": record}, datetime(2026, 1, 1, tzinfo=UTC), tenant_id=TENANT))


def seed(path):
    store = SQLiteHistoricalEvidenceStore(path)
    n = 1
    for seq, count in ((1, 34), (2, 33), (3, 33)):
        rows = tuple(header_obs(i) for i in range(n, n + count))
        store.append(HistoricalEvidenceBatch(TENANT, "Purchase Invoice", seq, datetime(2026, 1, seq, tzinfo=UTC), rows))
        n += count


def document(name):
    parent = {field: (1 if field == "docstatus" else name if field == "name" else COMPANY if field == "company" else "value") for field in PARENT_FIELDS}
    parent.update({"posting_date": "2026-01-01", "due_date": "2026-01-31", "grand_total": 100.0})
    parent["items"] = [{field: (1 if field in {"qty", "rate", "amount"} else f"{field}-value") for field in ITEM_FIELDS}]
    parent["taxes"] = [{field: (1 if field in {"rate", "tax_amount"} else False if field == "included_in_print_rate" else f"{field}-value") for field in ("account_head", "add_deduct_tax", "charge_type", "cost_center", "rate", "tax_amount", "included_in_print_rate")}]
    parent["extra_secret"] = "discard"
    return parent


def test_rich_capture_selects_ten_gets_and_persists_projection(tmp_path):
    header = tmp_path / "header.sqlite3"
    profile = tmp_path / "profile.sqlite3"
    seed(header)
    calls = []
    def opener(request, timeout):
        calls.append(request)
        return Response({"data": document(request.full_url.rsplit("/", 1)[-1])}, request.full_url)
    summary = capture_purchase_invoice_rich_v1(config(), header_database_path=header, profile_database_path=profile, opener=opener, clock=lambda: datetime(2026, 1, 4, tzinfo=UTC))
    assert summary.selected_invoice_count == summary.persisted_invoice_count == 10 and len(calls) == 10
    stored = SQLiteProfiledEvidenceStore(profile).load_all(tenant_id=TENANT, resource="Purchase Invoice", profile_id=PROFILE_ID)
    assert len(stored) == 1 and len(stored[0].observations) == 10
    record = stored[0].observations[0].evidence.payload["record"]
    assert "extra_secret" not in record and set(record) == set(PARENT_FIELDS) | {"items", "taxes"}


def test_second_invocation_and_failures_do_not_append(tmp_path):
    header, profile = tmp_path / "h.sqlite3", tmp_path / "p.sqlite3"
    seed(header)
    calls = []
    def opener(request, timeout):
        calls.append(request); return Response({"data": document(request.full_url.rsplit("/", 1)[-1])}, request.full_url)
    capture_purchase_invoice_rich_v1(config(), header_database_path=header, profile_database_path=profile, opener=opener)
    with pytest.raises(HistoricalEvidenceError):
        capture_purchase_invoice_rich_v1(config(), header_database_path=header, profile_database_path=profile, opener=opener)
    assert len(calls) == 10


def test_wrong_header_state_fails_before_http(tmp_path):
    path = tmp_path / "wrong.sqlite3"
    store = SQLiteHistoricalEvidenceStore(path)
    store.append(HistoricalEvidenceBatch(TENANT, "Purchase Invoice", 1, datetime(2026, 1, 1, tzinfo=UTC), tuple(header_obs(i) for i in range(1, 6))))
    with pytest.raises(HistoricalEvidenceError):
        capture_purchase_invoice_rich_v1(config(), header_database_path=path, profile_database_path=tmp_path / "p.sqlite3", opener=lambda *_: pytest.fail("HTTP"))


def test_projection_constants_and_exact_parent_allowlist():
    assert FIRST_CAPTURE_FIELDS == PARENT_FIELDS[:8]
    assert PROFILE_ID == "purchase-invoice-accounting-v1"


def test_profile_round_trip_checksum_and_strict_decode(tmp_path):
    path = tmp_path / "profile.sqlite3"
    seed(path)
    obs = Observation(Evidence(EvidenceKind.API, "synthetic", {"resource": "Purchase Invoice", "profile_id": PROFILE_ID, "record": {"name": "R", "items": [], "taxes": []}}, datetime(2026, 1, 1, tzinfo=UTC), tenant_id=TENANT))
    batch = ProfiledEvidenceBatch(TENANT, "Purchase Invoice", PROFILE_ID, 1, datetime(2026, 1, 1, tzinfo=UTC), (obs,))
    encoded = profiled_evidence_to_json(batch)
    assert profiled_evidence_checksum(encoded) == profiled_evidence_checksum(encoded)
    assert profiled_evidence_from_json(encoded) == batch
    with pytest.raises(HistoricalEvidenceError):
        profiled_evidence_from_json(encoded.replace('"format_version":1', '"format_version":1,"extra":2'))


def test_config_requires_https_and_child_shapes_fail_closed(tmp_path):
    with pytest.raises(ValueError):
        RichCaptureConfig("http://example.test", TENANT, COMPANY, "k", "s")
    header, profile = tmp_path / "h.sqlite3", tmp_path / "p.sqlite3"
    seed(header)
    def opener(request, timeout):
        payload = document(request.full_url.rsplit("/", 1)[-1])
        payload["items"][0]["qty"] = float("inf")
        return Response({"data": payload}, request.full_url)
    with pytest.raises(HistoricalEvidenceError):
        capture_purchase_invoice_rich_v1(config(), header_database_path=header, profile_database_path=profile, opener=opener)
    assert not profile.exists() or not SQLiteProfiledEvidenceStore(profile).load_all(tenant_id=TENANT, resource="Purchase Invoice", profile_id=PROFILE_ID)


def test_empty_taxes_allowed_but_empty_items_rejected(tmp_path):
    header, profile = tmp_path / "h.sqlite3", tmp_path / "p.sqlite3"
    seed(header)
    def opener(request, timeout):
        payload = document(request.full_url.rsplit("/", 1)[-1]); payload["taxes"] = []
        return Response({"data": payload}, request.full_url)
    capture_purchase_invoice_rich_v1(config(), header_database_path=header, profile_database_path=profile, opener=opener)
    header2, profile2 = tmp_path / "h2.sqlite3", tmp_path / "p2.sqlite3"
    seed(header2)
    def empty(request, timeout):
        payload = document(request.full_url.rsplit("/", 1)[-1]); payload["items"] = []
        return Response({"data": payload}, request.full_url)
    with pytest.raises(HistoricalEvidenceError):
        capture_purchase_invoice_rich_v1(config(), header_database_path=header2, profile_database_path=profile2, opener=empty)
