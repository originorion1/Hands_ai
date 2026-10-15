"""Offline egress inventory and bypass regressions; never use real sockets."""
import ast
import importlib
from pathlib import Path
from urllib.request import Request

import pytest
from test_pilot_read import FIELDS, NOW, grant, launch, reader

from orion.discovery.erpnext_adapter import ERPNextDiscoveryAdapter
from orion.discovery.erpnext_historical_sample import ERPNextHistoricalSampleAdapter
from orion.discovery.http_adapter import ReadOnlyHttpDiscoveryAdapter
from orion.discovery.pilot_transport import open_pilot_read

LEGACY_MODULES = (
    'erpnext_adapter', 'erpnext_historical_sample', 'erpnext_metadata_adapter',
    'erpnext_identity_record_sample', 'erpnext_company_record_sample',
    'erpnext_metadata_preflight', 'erpnext_metadata_refresh', 'erpnext_live_session',
    'erpnext_bounded_trial', 'erpnext_six_hour_continuation', 'erpnext_learning_comparison',
)


@pytest.mark.parametrize('module', LEGACY_MODULES)
def test_all_legacy_launcher_default_transports_are_denied(module, monkeypatch):
    monkeypatch.setattr('urllib.request.OpenerDirector.open',
                        lambda *a, **k: pytest.fail('legacy network contacted'))
    opener = importlib.import_module('orion.discovery.' + module)._default_opener
    with pytest.raises(RuntimeError, match='legacy network read disabled'):
        opener(Request('https://example.test/api/resource/Entry'), timeout=20)


@pytest.mark.parametrize('kind', ['historical', 'discovery', 'http'])
def test_direct_legacy_adapters_cannot_use_builtin_network(kind, monkeypatch):
    monkeypatch.setattr('urllib.request.OpenerDirector.open',
                        lambda *a, **k: pytest.fail('legacy network contacted'))
    if kind == 'http':
        adapter = ReadOnlyHttpDiscoveryAdapter(base_url='https://example.test', paths=('/Entry',))
        with pytest.raises(RuntimeError, match='legacy network read disabled'):
            adapter.discover(tenant_id='tenant-a', observed_at=NOW)
        return
    kwargs = {'base_url': 'https://example.test', 'tenant_id': 'tenant-a',
              'api_key': 'fixture', 'api_secret': 'fixture'}
    if kind == 'historical':
        adapter = ERPNextHistoricalSampleAdapter(**kwargs, resource='Entry', company='Example', fields=FIELDS)
    else:
        adapter = ERPNextDiscoveryAdapter(**kwargs)
    with pytest.raises(RuntimeError, match='legacy network read disabled'):
        adapter.discover()


def test_transport_requires_real_permit_before_network(monkeypatch):
    with pytest.raises(TypeError):
        open_pilot_read(Request('https://example.test'), permit=None, timeout=20,
                        opener=lambda *a, **k: pytest.fail('transport opened without permit'))


@pytest.mark.parametrize('mutation', ['unbound', 'url', 'method', 'header', 'body', 'expiry'])
def test_unbound_or_mutated_wire_request_never_opens(mutation):
    calls = []
    now = [NOW]
    class Probe:
        source_id = 'https://example.test'
        def read(self, permit):
            req = Request('https://example.test/api/resource/Entry', method='GET')
            if mutation != 'unbound':
                permit.bind_wire(req)
            if mutation == 'url':
                req.full_url = 'https://example.test/api/resource/Other'
            if mutation == 'method':
                req.method = 'POST'
            if mutation == 'header':
                req.add_header('X-Changed', 'yes')
            if mutation == 'body':
                req.data = b'body'
            if mutation == 'expiry':
                now[0] = grant().window.expires_at
            open_pilot_read(req, permit=permit, timeout=20,
                            opener=lambda *a, **k: calls.append(a))
            return ()
    with pytest.raises(ValueError):
        launch(Probe(), clock=lambda: now[0])
    assert calls == []


def test_pilot_has_no_implicit_production_transport():
    adapter = reader([])
    adapter._opener = None
    with pytest.raises(ValueError):
        launch(adapter)


def test_no_unreviewed_builtin_raw_network_openers_remain():
    root = Path(__file__).resolve().parents[1] / 'src' / 'orion'
    for path in root.rglob('*.py'):
        relative = path.relative_to(root).as_posix()
        for node in ast.walk(ast.parse(path.read_text())):
            if isinstance(node, ast.Import):
                assert not any(alias.name.split('.')[0] in
                    {'urllib', 'http', 'socket', 'requests', 'httpx', 'aiohttp'}
                    for alias in node.names), path
            if isinstance(node, ast.ImportFrom):
                module = node.module or ''
                imported = {alias.name for alias in node.names}
                reviewed_loopback_server_import = (
                    relative == 'presentation/server.py'
                    and (
                        (module == 'http' and imported == {'HTTPStatus'})
                        or (
                            module == 'http.server'
                            and imported == {'BaseHTTPRequestHandler', 'HTTPServer'}
                        )
                    )
                )
                if reviewed_loopback_server_import:
                    continue
                if module == 'urllib.request':
                    assert imported <= {'Request'}, path
                else:
                    assert module.split('.')[0] not in {
                        'socket', 'requests', 'httpx', 'aiohttp', 'http'}, path


@pytest.mark.parametrize('error,category', [
    (ValueError, 'scope_or_response_invalid'),
    (RuntimeError, 'upstream_read_failed'),
    (AttributeError, 'unexpected_internal_failure'),
])
def test_safe_error_categories_withhold_private_details(monkeypatch, error, category):
    from orion.discovery.erpnext_pilot import PilotAdapterReadError
    def fail(self):
        raise error('private fixture content')
    monkeypatch.setattr(ERPNextHistoricalSampleAdapter, 'discover', fail)
    with pytest.raises(PilotAdapterReadError) as caught:
        launch(reader([]))
    assert caught.value.category == category
    assert 'private fixture content' not in str(caught.value)
    assert caught.value.__suppress_context__


@pytest.mark.parametrize('fail', [False, True])
def test_closed_permit_releases_bound_headers(fail):
    captured = []
    delegate = reader([])
    if fail:
        def reject(*args, **kwargs):
            raise RuntimeError('fixture failure')
        delegate._opener = reject
    class Capture:
        source_id = delegate.source_id
        def read(self, permit):
            captured.append(permit)
            return delegate.read(permit)
    if fail:
        with pytest.raises(ValueError):
            launch(Capture())
    else:
        launch(Capture())
    assert captured[0]._wire is None
    assert not captured[0]._active


@pytest.mark.parametrize('headers', [
    {'x-ReQuEsT-ID': 'fixture', 'aCcEpT': 'application/json'},
    {'AUTHORIZATION': 'fixture-only', 'x-custom-HEADER': 'value'},
])
def test_bound_request_header_casing_survives_copy(headers):
    calls = []
    class Probe:
        source_id = 'https://example.test'
        def read(self, permit):
            req = Request('https://example.test/api/resource/Entry', headers=headers, method='GET')
            permit.bind_wire(req)
            def capture(outbound, timeout):
                assert outbound is not req
                assert sorted(outbound.header_items()) == sorted(req.header_items())
                calls.append(outbound)
            open_pilot_read(req, permit=permit, timeout=20, opener=capture)
            return ()
    assert launch(Probe()) == ()
    assert len(calls) == 1
