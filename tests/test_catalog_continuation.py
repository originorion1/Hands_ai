import json
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from test_erpnext_metadata_preflight import NOW, SECRET_ENVIRONMENT, FakeResponse, config

from orion.discovery import erpnext_metadata_preflight as p
from orion.discovery.catalog_continuation import continue_catalog


def prepared(tmp_path):
    cfg = config(tmp_path)
    ledger = p._RunLedger.create(p._ledger_path(cfg))
    with ledger._connect() as c:
        c.execute("UPDATE preflight_run SET status='failed', attempted_gets=4, "
                  "company_count=1, company_catalog_complete=1 WHERE singleton=1")
    return cfg, ledger


def run(cfg, opener, **kwargs):
    return continue_catalog(cfg, environment=SECRET_ENVIRONMENT,
                            expires_at=NOW + timedelta(days=1), clock=lambda: NOW,
                            opener=opener, **kwargs)


def test_pages_resume_without_manual_export_and_retain_names_privately(tmp_path):
    cfg, ledger = prepared(tmp_path)
    def first(req, timeout):
        assert req.method == 'GET'
        return FakeResponse(req, {'data': [{'name': f'Entity{i:03}'} for i in range(99)]})
    assert run(cfg, first, max_pages=1)['status'] == 'bounded_partial'
    def second(req, timeout):
        query = parse_qs(urlsplit(req.full_url).query)
        assert json.loads(query['filters'][0]) == [['name', '>', 'Entity098']]
        return FakeResponse(req, {'data': [{'name': 'Entity099'}]})
    report = run(cfg, second)
    assert report['catalog_name_count'] == 100
    assert report['catalog_complete'] is True
    assert report['attempted_gets'] == 6
    assert 'Entity' not in json.dumps(report)
    def forbidden(*args, **kwargs):
        pytest.fail('completed catalog repeated request')
    assert run(cfg, forbidden)['pages_read'] == 0
    assert p._ledger_path(cfg).stat().st_mode & 0o777 == 0o600
    with ledger._connect() as c:
        assert len(json.loads(c.execute('SELECT names FROM automatic_catalog').fetchone()[0])) == 100


def test_transport_failure_consumes_attempt_and_blocks_ambiguous_replay(tmp_path):
    cfg, ledger = prepared(tmp_path)
    def failed(*args, **kwargs):
        raise RuntimeError('fixture')
    with pytest.raises(RuntimeError):
        run(cfg, failed)
    assert ledger.snapshot()['attempted_gets'] == 5
    with pytest.raises(ValueError, match='in-flight'):
        run(cfg, failed)
    assert ledger.snapshot()['attempted_gets'] == 5


def test_expiry_prevents_transport_and_new_state(tmp_path):
    cfg, ledger = prepared(tmp_path)
    with pytest.raises(ValueError, match='expired'):
        continue_catalog(cfg, environment=SECRET_ENVIRONMENT, expires_at=NOW,
                         clock=lambda: NOW)
    assert ledger.snapshot()['attempted_gets'] == 4


def test_budget_is_shared_and_never_reset(tmp_path):
    cfg, ledger = prepared(tmp_path)
    def full(req, timeout):
        return FakeResponse(req, {'data': [{'name': f'E{i:03}'} for i in range(99)]})
    run(cfg, full, max_pages=1)
    with ledger._connect() as c:
        c.execute('UPDATE preflight_run SET attempted_gets=100')
    assert run(cfg, full)['attempted_gets'] == 100


def test_scope_binding_change_rejected(tmp_path):
    cfg, ledger = prepared(tmp_path)
    def full(req, timeout):
        return FakeResponse(req, {'data': [{'name': f'E{i:03}'} for i in range(99)]})
    run(cfg, full, max_pages=1)
    # Alter retained binding to simulate wrong configuration without changing path.
    with ledger._connect() as c:
        c.execute("UPDATE automatic_catalog SET binding='different'")
    with pytest.raises(ValueError, match='binding'):
        run(cfg, full)


def test_ignored_cursor_fails_without_accepting_duplicate_page(tmp_path):
    cfg, _ledger = prepared(tmp_path)
    def full(req, timeout):
        return FakeResponse(req, {'data': [{'name': f'E{i:03}'} for i in range(99)]})
    run(cfg, full, max_pages=1)
    with pytest.raises(p.MetadataPreflightError):
        run(cfg, full)
