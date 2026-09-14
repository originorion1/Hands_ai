import json
from datetime import UTC, date, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from orion.discovery.erpnext_historical_sample import ERPNextHistoricalSampleAdapter
from orion.discovery.read_window import ReviewedReadWindow

NOW = datetime(2026, 1, 1, tzinfo=UTC)
FIELDS = ('name', 'company', 'docstatus', 'posting_date')


def window():
    return ReviewedReadWindow('tenant', 'Example', 'Entry', FIELDS, 'posting_date',
                              date(2024, 1, 1), date(2024, 12, 31), NOW + timedelta(hours=1))


def adapter(opener, clock=lambda: NOW, **changes):
    args = {'base_url': 'https://example.test', 'tenant_id': 'tenant', 'company': 'Example',
                'resource': 'Entry', 'fields': FIELDS, 'api_key': 'fixture', 'api_secret': 'fixture',
                'read_window': window(), 'clock': clock, 'opener': opener}
    args.update(changes)
    return ERPNextHistoricalSampleAdapter(**args)


class Response:
    def __init__(self, value):
        self.value = value
    def __enter__(self):
        return self
    def __exit__(self, *args):
        return False
    def read(self, bound):
        return json.dumps({'data': [{'name': 'row', 'company': 'Example', 'docstatus': 1,
                                       'posting_date': self.value}]}).encode()


@pytest.mark.parametrize('value', ['2024-01-01', '2024-12-31'])
def test_dates_filtered_on_server_and_validated_locally(value):
    def opener(request, timeout):
        filters = json.loads(parse_qs(urlsplit(request.full_url).query)['filters'][0])
        assert ['posting_date', '>=', '2024-01-01'] in filters
        assert ['posting_date', '<=', '2024-12-31'] in filters
        assert request.method == 'GET'
        return Response(value)
    assert len(adapter(opener).discover()) == 1


@pytest.mark.parametrize('value', ['2023-12-31', '2025-01-01', None, '2024-01-01T00:00:00', 'bad'])
def test_ignored_server_filters_never_create_observations(value):
    with pytest.raises((ValueError, TypeError)):
        adapter(lambda *args, **kwargs: Response(value)).discover()


@pytest.mark.parametrize('change', [{'company': 'Other'}, {'resource': 'Payroll'},
                                   {'tenant_id': 'other'}, {'fields': FIELDS + ('salary',)}])
def test_scope_mismatch_fails_before_request(change):
    def forbidden(*args, **kwargs):
        pytest.fail('unauthorized request')
    with pytest.raises((ValueError, TypeError)):
        adapter(forbidden, **change).discover()


def test_expiry_checked_before_and_after_request():
    def forbidden(*args, **kwargs):
        pytest.fail('expired request')
    with pytest.raises((ValueError, TypeError)):
        adapter(forbidden, clock=lambda: NOW + timedelta(hours=1)).discover()
    times = iter([NOW, NOW + timedelta(hours=1)])
    with pytest.raises((ValueError, TypeError)):
        adapter(lambda *args, **kwargs: Response('2024-01-01'), clock=lambda: next(times)).discover()
