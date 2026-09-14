from dataclasses import replace
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from test_erpnext_metadata_adapter import FakeResponse
from test_pilot_read import NOW
from test_pilot_read import grant as record_grant

from orion.discovery.erpnext_pilot_metadata import ERPNextPilotMetadataReader
from orion.discovery.pilot_metadata import (
    MetadataAuthorization,
    MetadataRequest,
    launch_pilot_metadata,
)


def request():
    return MetadataRequest('tenant-a', 'Example', 'https://example.test')


def grant():
    return MetadataAuthorization('metadata-v1', request(), NOW + timedelta(hours=1), True, 5, 2, ())


def schema(name='Entry'):
    return {'message': {'docs': [{'name': name, 'is_submittable': 1, 'fields': [
        {'fieldname': 'company', 'fieldtype': 'Link', 'options': 'Company'},
        {'fieldname': 'posting_date', 'fieldtype': 'Date'},
        {'fieldname': 'amount', 'fieldtype': 'Currency'},
    ]}]}}


def reader(calls, transform=lambda payload: payload):
    def transport(req, timeout):
        calls.append(req)
        path = urlsplit(req.full_url).path
        if path == '/api/resource/DocType':
            payload = {'data': [{'name': 'Entry'}]}
        else:
            assert path == '/api/method/frappe.desk.form.load.getdoctype'
            assert parse_qs(urlsplit(req.full_url).query) == {'doctype': ['Entry']}
            payload = schema()
        return FakeResponse(transform(payload), url=req.full_url)
    return ERPNextPilotMetadataReader(source_id=request().source_id,
        api_key='fixture', api_secret='fixture', opener=transport)


def launch(adapter, req=None, lookup=lambda key: grant(), clock=lambda: NOW):
    return launch_pilot_metadata(req or request(), authorization_id='metadata-v1',
                                  lookup=lookup, adapter=adapter, clock=clock)


def test_discovers_names_and_fields_without_business_record_read():
    calls = []
    result = launch(reader(calls))
    assert len(calls) == 2
    assert result.catalog == ('Entry',) and result.catalog_complete
    assert result.schema_targets == ('Entry',)
    assert result.proposals[0].resource == 'Entry'
    assert 'amount' in result.proposals[0].fields
    assert result.proposals[0].date_fields == ('posting_date',)
    assert result.review_required and not result.record_reads_allowed
    evidence = result.observations[0].evidence
    assert evidence.tenant_id == 'tenant-a' and evidence.observed_at == NOW
    assert evidence.payload['company_context'] == 'Example'
    assert evidence.payload['scope_sha256']
    with pytest.raises(TypeError):
        evidence.payload['review_required'] = False


@pytest.mark.parametrize('change', [{'tenant_id':'other'}, {'company':'other'},
                                   {'source_id':'https://other.test'}])
def test_wrong_scope_denied_before_transport(change):
    calls = []
    with pytest.raises(ValueError):
        launch(reader(calls), req=replace(request(), **change))
    assert not calls


@pytest.mark.parametrize('value', [None, {}, [], record_grant()])
def test_missing_ambiguous_or_record_grant_cannot_discover_metadata(value):
    calls = []
    with pytest.raises(ValueError):
        launch(reader(calls), lookup=lambda key: value)
    assert not calls


@pytest.mark.parametrize('delta', [3600, 3601])
def test_expired_metadata_grant_including_equality_never_dispatches(delta):
    calls = []
    with pytest.raises(ValueError):
        launch(reader(calls), clock=lambda: NOW + timedelta(seconds=delta))
    assert not calls


@pytest.mark.parametrize('revoke', [True, False])
def test_expiry_or_revocation_after_catalog_stops_schema_and_admission(revoke):
    calls = []
    state = {'grant': grant(), 'now': NOW}
    def expire(payload):
        if revoke:
            state['grant'] = None
        else:
            state['now'] = grant().expires_at
        return payload
    with pytest.raises(ValueError):
        launch(reader(calls, expire), lookup=lambda key: state['grant'], clock=lambda: state['now'])
    assert len(calls) == 1


def test_excluded_resource_is_not_read_and_partial_catalog_is_explicit():
    calls = []
    result = launch(reader(calls), lookup=lambda key: replace(grant(), excluded_resources=('Entry',)))
    assert len(calls) == 1 and not result.schema_targets and not result.proposals


def test_replay_is_deterministic_and_cannot_be_used_as_a_record_grant():
    first, second = launch(reader([])), launch(reader([]))
    assert first == second
    from test_pilot_read import launch as record_launch
    from test_pilot_read import reader as record_reader
    calls = []
    with pytest.raises(ValueError):
        record_launch(record_reader(calls), grants=lambda key: grant())
    assert not calls


@pytest.mark.parametrize('change', [{'site_schema_read':False}, {'max_schemas':0},
    {'max_catalog_entries':101}, {'excluded_resources':[]}, {'expires_at':NOW.replace(tzinfo=None)}])
def test_invalid_metadata_authorizations_fail_closed(change):
    with pytest.raises((ValueError, TypeError)):
        replace(grant(), **change)


def test_direct_reader_without_permit_denied():
    calls = []
    with pytest.raises(TypeError):
        reader(calls).catalog(None, 6)
    assert not calls


@pytest.mark.parametrize('payload', [{'data':[{'name':'Entry','record':'secret'}]},
    {'data':[{'name':'Entry'},{'name':'Entry'}]}, {'data':'bad'}])
def test_malformed_catalog_never_becomes_evidence(payload):
    calls = []
    with pytest.raises(ValueError):
        launch(reader(calls, lambda old: payload))
    assert len(calls) == 1


def test_sensitive_embedded_schema_values_do_not_become_proposals():
    def sensitive(payload):
        if 'message' in payload:
            payload['message']['docs'][0]['fields'].append(
                {'fieldname':'password','fieldtype':'Password','default':'fixture-secret'})
        return payload
    result = launch(reader([], sensitive))
    assert result.proposals == ()
    assert 'fixture-secret' not in repr(result)


def test_catalog_sentinel_and_schema_budget_prevent_unbounded_discovery():
    calls = []
    def transport(req, timeout):
        calls.append(req)
        if urlsplit(req.full_url).path == '/api/resource/DocType':
            return FakeResponse({'data': [{'name': name} for name in ('Alpha','Beta','Gamma')]})
        name = parse_qs(urlsplit(req.full_url).query)['doctype'][0]
        return FakeResponse(schema(name))
    adapter = ERPNextPilotMetadataReader(source_id=request().source_id,api_key='fixture',
                                         api_secret='fixture',opener=transport)
    result = launch(adapter, lookup=lambda key: replace(grant(),max_catalog_entries=2,max_schemas=1))
    assert result.catalog == ('Alpha','Beta')
    assert not result.catalog_complete
    assert result.schema_targets == ('Alpha',)
    assert len(calls) == 2


@pytest.mark.parametrize('mutation', ['resource','method','blank_query','repeat','schema_target'])
def test_metadata_wire_cannot_widen_or_repeat(monkeypatch, mutation):
    from urllib.request import Request

    import orion.discovery.erpnext_pilot_metadata as bridge
    calls = []
    if mutation == 'schema_target':
        def altered(self):
            self._opener(Request(request().source_id +
                '/api/method/frappe.desk.form.load.getdoctype?doctype=Other',method='GET'),timeout=20)
            return ()
        monkeypatch.setattr(bridge.ERPNextMetadataAdapter,'discover',altered)
    else:
        def altered(config, resource, requested, opener):
            from urllib.parse import urlencode
            query = urlencode({'fields':'["name"]','limit_start':0,
                               'limit_page_length':requested,'order_by':'name asc'})
            if mutation == 'blank_query':
                query += '&fields='
            path = 'Entry' if mutation == 'resource' else 'DocType'
            req = Request(request().source_id + '/api/resource/' + path + '?' + query,
                          method='POST' if mutation == 'method' else 'GET')
            opener(req,timeout=20)
            if mutation == 'repeat':
                opener(req,timeout=20)
            return (),True
        monkeypatch.setattr(bridge,'_read_name_catalog',altered)
    with pytest.raises(ValueError):
        launch(reader(calls))
    assert len(calls) == (1 if mutation in ('repeat','schema_target') else 0)


def test_metadata_response_redirect_is_rejected():
    calls=[]
    def redirect(req,timeout):
        calls.append(req)
        return FakeResponse({'data':[]},url='https://other.test/')
    adapter=ERPNextPilotMetadataReader(source_id=request().source_id,api_key='fixture',
                                       api_secret='fixture',opener=redirect)
    with pytest.raises(ValueError):
        launch(adapter)
    assert len(calls)==1


def test_no_default_transport_and_wrong_adapter_source():
    adapter=ERPNextPilotMetadataReader(source_id=request().source_id,api_key='fixture',
                                       api_secret='fixture')
    with pytest.raises(ValueError):
        launch(adapter)
    adapter._source_id='https://other.test'
    with pytest.raises(ValueError):
        launch(adapter)
