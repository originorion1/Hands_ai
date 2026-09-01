import inspect
import json
import re
from datetime import UTC, datetime
from urllib.parse import parse_qs, urlsplit

import pytest

from orion.discovery import erpnext_historical_capture
from orion.discovery.erpnext_historical_capture import (
    FIRST_CAPTURE_FIELDS,
    ERPNextHistoricalCaptureConfig,
    capture_erpnext_historical_sample,
    default_historical_evidence_path,
)
from orion.history.evidence import HistoricalEvidenceError
from orion.stores.sqlite_historical_evidence import SQLiteHistoricalEvidenceStore

TENANT = "tenant-offline"
COMPANY = "Example Company"


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


def config(**overrides):
    values = {
        "base_url": "https://example.test",
        "tenant_id": TENANT,
        "company": COMPANY,
        "api_key": "offline-key",
        "api_secret": "offline-secret",
    }
    values.update(overrides)
    return ERPNextHistoricalCaptureConfig(**values)


def rows():
    return [{
        "name": "PINV-OFFLINE",
        "company": COMPANY,
        "supplier": "Supplier Offline",
        "posting_date": "2026-09-02",
        "currency": "USD",
        "grand_total": 10,
        "due_date": "2026-09-30",
        "docstatus": 1,
    }]


def opener_factory(calls, data=None):
    def opener(request, timeout):
        calls.append(request)
        return FakeResponse({"data": rows() if data is None else data}, request.full_url)

    return opener


def capture(config_value, path, calls, data=None, **kwargs):
    return capture_erpnext_historical_sample(
        config_value,
        database_path=path,
        opener=opener_factory(calls, data),
        clock=lambda: datetime(2026, 9, 2, 12, tzinfo=UTC),
        **kwargs,
    )


def test_full_offline_composition_and_sqlite_reopen(tmp_path):
    path = tmp_path / "capture.sqlite3"
    calls = []
    summary = capture(config(), path, calls)

    assert summary.batch_sequence == 1
    assert summary.observation_count == 1
    assert summary.persisted is True
    assert summary.execution_allowed is False
    assert len(calls) == 1

    first = SQLiteHistoricalEvidenceStore(path).load_all(tenant_id=TENANT, resource="Purchase Invoice")
    reopened = SQLiteHistoricalEvidenceStore(path).load_all(tenant_id=TENANT, resource="Purchase Invoice")
    assert len(first) == len(reopened) == 1
    assert first[0] == reopened[0]
    assert first[0].observations[0].observation_id == reopened[0].observations[0].observation_id
    assert first[0].observations[0].evidence.evidence_id == reopened[0].observations[0].evidence.evidence_id


def test_exact_profile_and_one_get(tmp_path):
    calls = []
    capture(config(), tmp_path / "capture.sqlite3", calls)
    request = calls[0]
    query = parse_qs(urlsplit(request.full_url).query)

    assert request.method == "GET"
    assert json.loads(query["fields"][0]) == list(FIRST_CAPTURE_FIELDS)
    assert json.loads(query["filters"][0]) == [["company", "=", COMPANY], ["docstatus", "=", 1]]
    assert query["limit_page_length"] == ["5"]
    assert query["order_by"] == ["posting_date desc, name desc"]
    assert len(calls) == 1


def test_safe_summary_has_no_raw_values_or_credentials(tmp_path):
    calls = []
    summary = capture(config(), tmp_path / "capture.sqlite3", calls)
    rendered = repr(summary)

    assert "PINV-OFFLINE" not in rendered
    assert COMPANY not in rendered
    assert "Supplier Offline" not in rendered
    assert "offline-key" not in rendered
    assert "offline-secret" not in rendered
    assert "execution_allowed=False" in rendered
    assert summary.erp_writes == 0


def test_first_capture_guard_prevents_http_when_history_exists(tmp_path):
    path = tmp_path / "capture.sqlite3"
    first_calls = []
    capture(config(), path, first_calls)
    second_calls = []

    with pytest.raises(HistoricalEvidenceError, match="empty durable"):
        capture(config(), path, second_calls)
    assert second_calls == []


def test_invalid_runtime_configuration_fails_before_http(tmp_path):
    with pytest.raises(ValueError, match="api_key"):
        config(api_key="")
    assert not (tmp_path / "capture.sqlite3").exists()


def test_append_reload_failure_returns_no_summary(monkeypatch, tmp_path):
    def fail(*args, **kwargs):
        raise RuntimeError("offline append failure")

    monkeypatch.setattr(erpnext_historical_capture, "persist_historical_sample", fail)
    with pytest.raises(RuntimeError, match="append failure"):
        capture(config(), tmp_path / "capture.sqlite3", [])


def test_default_path_is_hashed_and_outside_repo():
    path = default_historical_evidence_path("tenant-sensitive-name", state_root="C:/orion-state")
    assert path.parent == __import__("pathlib").Path("C:/orion-state")
    assert "tenant-sensitive-name" not in str(path)
    assert "Purchase Invoice" not in str(path)


def test_capture_module_has_no_write_or_authority_capability():
    source = inspect.getsource(erpnext_historical_capture).lower()
    for forbidden in ("post", "put", "patch", "delete", "checkpoint", "promotion"):
        assert re.search(rf"\b{forbidden}\b", source) is None


def test_first_profile_config_rejects_non_five_sample_size():
    with pytest.raises(ValueError, match="exactly 5"):
        config(sample_size=4)
