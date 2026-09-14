from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from types import MappingProxyType
from uuid import UUID

import pytest
from test_erpnext_historical_sample import FakeResponse

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.discovery.erpnext_pilot import ERPNextPilotReader
from orion.discovery.pilot_read import PilotAuthorization, PilotRequest, launch_pilot_read
from orion.discovery.read_window import ReviewedReadWindow

NOW = datetime(2030, 1, 1, tzinfo=UTC)
FIELDS = ('name', 'company', 'docstatus', 'posting_date')


def grant():
    return PilotAuthorization('grant-v1', 'https://example.test',
        ReviewedReadWindow('tenant-a', 'Example', 'Entry', FIELDS, 'posting_date',
                           date(2024, 1, 1), date(2024, 12, 31), NOW + timedelta(hours=1)),
        'name', 'company', 'erpnext-historical-sample-read-only', EvidenceKind.API, 5)


def request():
    return PilotRequest('tenant-a', 'Example', 'https://example.test', 'Entry', FIELDS,
                        'posting_date', date(2024, 1, 1), date(2024, 12, 31), 5)


def row():
    return {'name': 'r1', 'company': 'Example', 'docstatus': 1, 'posting_date': '2024-06-01'}


def reader(calls, rows=None):
    def transport(req, timeout):
        calls.append(req)
        return FakeResponse({'data': [row()] if rows is None else rows})
    return ERPNextPilotReader(source_id='https://example.test', api_key='fixture',
                             api_secret='fixture', opener=transport)


def launch(adapter, req=None, grants=None, clock=lambda: NOW):
    return launch_pilot_read(request() if req is None else req, authorization_id='grant-v1',
                            lookup=(lambda key: grant()) if grants is None else grants,
                            adapter=adapter, clock=clock)


def test_authorized_read_returns_deeply_immutable_evidence():
    calls = []
    observations = launch(reader(calls))
    assert len(calls) == len(observations) == 1
    assert calls[0].method == 'GET'
    ev = observations[0].evidence
    assert ev.tenant_id == 'tenant-a'
    assert isinstance(ev.payload, MappingProxyType)
    assert ev.payload['provenance']['authorization_id'] == 'grant-v1'
    assert ev.payload['provenance']['source_id'] == 'https://example.test'
    assert ev.payload['provenance']['scope_sha256']
    assert isinstance(ev.evidence_id, UUID)
    with pytest.raises(TypeError):
        ev.payload['record']['company'] = 'other'


@pytest.mark.parametrize('changes', [
    {'tenant_id': 'other'}, {'company': 'other'}, {'source_id': 'https://other.test'},
    {'resource': 'Other'}, {'fields': FIELDS + ('secret_field',)},
    {'start': date(2023, 1, 1)}, {'date_field': 'creation'}, {'max_records': 6},
])
def test_mismatched_scope_never_contacts_transport(changes):
    calls = []
    with pytest.raises((ValueError, TypeError)):
        launch(reader(calls), replace(request(), **changes))
    assert calls == []


@pytest.mark.parametrize('value', [None, {}, 'grant-v1', [grant()]])
def test_missing_malformed_or_ambiguous_authorization_fails_before_io(value):
    calls = []
    with pytest.raises((ValueError, TypeError)):
        launch(reader(calls), grants=lambda key: value)
    assert calls == []


@pytest.mark.parametrize('delta', [timedelta(hours=1), timedelta(hours=2)])
def test_expiry_including_equality_prevents_io(delta):
    calls = []
    with pytest.raises(ValueError):
        launch(reader(calls), clock=lambda: NOW + delta)
    assert calls == []


@pytest.mark.parametrize('changes', [
    {'company': 'other'}, {'posting_date': '2025-01-01'},
    {'posting_date': None}, {'unexpected': 'payload'}, {'docstatus': 0},
])
def test_out_of_scope_rows_never_admitted(changes):
    calls = []
    with pytest.raises((ValueError, TypeError)):
        launch(reader(calls, [{**row(), **changes}]))
    assert len(calls) == 1


def test_post_response_expiry_and_revocation_fail_closed():
    for revoke in (False, True):
        state = {'now': NOW, 'grant': grant()}
        def transport(req, timeout, state=state, revoke=revoke):
            if revoke:
                state['grant'] = None
            else:
                state['now'] = NOW + timedelta(hours=1)
            return FakeResponse({'data': [row()]})
        adapter = ERPNextPilotReader(source_id='https://example.test', api_key='fixture',
                                     api_secret='fixture', opener=transport)
        with pytest.raises(ValueError):
            launch(adapter, grants=lambda key, state=state: state['grant'], clock=lambda state=state: state['now'])


def test_adapter_direct_call_without_launcher_permit_never_contacts_io():
    calls = []
    adapter = reader(calls)
    with pytest.raises((ValueError, TypeError)):
        adapter.read(request())
    assert calls == []
    assert not any(hasattr(adapter, name) for name in ('write', 'execute', 'post', 'put', 'delete'))


def test_replay_preserves_original_identity_and_provenance_without_aliasing():
    mutable = row()
    original = Observation(Evidence(EvidenceKind.API, grant().provenance_source,
                                   {'resource': 'Entry', 'record': mutable},
                                   observed_at=NOW, tenant_id='tenant-a'))
    class LocalReader:
        source_id = 'https://example.test'
        def read(self, permit):
            permit.check(self.source_id)
            return (original,)
    first = launch(LocalReader())
    second = launch(LocalReader())
    assert first == second
    assert first[0].evidence.payload['provenance']['upstream_evidence_id'] == str(original.evidence.evidence_id)
    assert first[0].evidence.payload['provenance']['upstream_observation_id'] == str(original.observation_id)
    mutable['name'] = 'changed-after-admission'
    assert first[0].evidence.payload['record']['name'] == 'r1'


@pytest.mark.parametrize('changes', [{'tenant_id':'other'}, {'source':'missing'},
                                     {'observed_at':NOW.replace(tzinfo=None)},
                                     {'evidence_id':None}])
def test_adapter_cannot_forge_cross_tenant_or_missing_provenance(changes):
    evidence = Evidence(EvidenceKind.API, grant().provenance_source,
                        {'resource': 'Entry', 'record': row()}, observed_at=NOW, tenant_id='tenant-a')
    class InvalidReader:
        source_id = 'https://example.test'
        def read(self, permit):
            return (Observation(replace(evidence, **changes)),)
    with pytest.raises((ValueError, TypeError)):
        launch(InvalidReader())


@pytest.mark.parametrize('mutation', ['method', 'source', 'fields', 'resource', 'repeat'])
def test_wire_level_bypass_cannot_widen_read(monkeypatch, mutation):
    import json
    from urllib.parse import quote, urlencode
    from urllib.request import Request

    from orion.discovery.erpnext_historical_sample import ERPNextHistoricalSampleAdapter

    calls = []
    def altered(self):
        query = {'fields': json.dumps(list(FIELDS), separators=(',', ':')),
                 'filters': json.dumps([['company', '=', 'Example'], ['docstatus', '=', 1]]
                                       + grant().window.filters(), separators=(',', ':')),
                 'order_by': 'posting_date desc, name desc', 'limit_start': '0',
                 'limit_page_length': '5'}
        origin = 'https://wrong.test' if mutation == 'source' else 'https://example.test'
        resource = 'Other' if mutation == 'resource' else 'Entry'
        if mutation == 'fields':
            query['fields'] = '["*"]'
        req = Request(origin + '/api/resource/' + quote(resource) + '?' + urlencode(query),
                      method='POST' if mutation == 'method' else 'GET')
        self._opener(req, timeout=1)
        if mutation == 'repeat':
            self._opener(req, timeout=1)
        return []
    monkeypatch.setattr(ERPNextHistoricalSampleAdapter, '_fetch_sample', altered)
    with pytest.raises(ValueError):
        launch(reader(calls))
    assert len(calls) == (1 if mutation == 'repeat' else 0)


def test_permit_is_not_constructible_by_request_and_closes_after_launch():
    from orion.discovery.pilot_read import PilotReadPermit
    with pytest.raises(ValueError):
        PilotReadPermit(object(), lambda: None, lambda: NOW)
    captured = []
    class LocalReader:
        source_id = 'https://example.test'
        def read(self, permit):
            captured.append(permit)
            return ()
    assert launch(LocalReader()) == ()
    with pytest.raises(ValueError, match='closed'):
        captured[0].check('https://example.test')


@pytest.mark.parametrize('changes', [
    {'authorization_id': ''}, {'provenance_source': ''}, {'max_records': True},
    {'window': None}, {'identity_field': 'missing'}, {'evidence_kind': 'API'},
])
def test_malformed_grant_construction_is_rejected(changes):
    with pytest.raises((TypeError, ValueError)):
        replace(grant(), **changes)


def test_wrong_adapter_binding_and_missing_grant_reference_do_not_dispatch():
    class WrongReader:
        source_id = 'https://other.test'
        def read(self, permit):
            pytest.fail('wrong adapter dispatched')
    with pytest.raises(ValueError):
        launch(WrongReader())
    calls = []
    with pytest.raises(ValueError):
        launch_pilot_read(request(), authorization_id='', lookup=lambda key: grant(),
                          adapter=reader(calls), clock=lambda: NOW)
    assert not calls


@pytest.mark.parametrize('changes', [
    {'provenance_source': 'different'}, {'evidence_kind': EvidenceKind.EVENT},
])
def test_unsupported_provenance_never_contacts_transport(changes):
    calls = []
    with pytest.raises(ValueError):
        launch(reader(calls), grants=lambda key: replace(grant(), **changes))
    assert calls == []


def test_fresh_acquisition_identity_ignores_random_upstream_ids():
    class FreshReader:
        source_id = 'https://example.test'
        def read(self, permit):
            return (Observation(Evidence(EvidenceKind.API, grant().provenance_source,
                {'resource': 'Entry', 'record': row()}, observed_at=NOW, tenant_id='tenant-a')),)
    first, second = launch(FreshReader())[0], launch(FreshReader())[0]
    assert first.evidence.evidence_id == second.evidence.evidence_id
    assert first.observation_id == second.observation_id
    assert first.evidence.payload['provenance']['upstream_evidence_id'] != (
        second.evidence.payload['provenance']['upstream_evidence_id'])
