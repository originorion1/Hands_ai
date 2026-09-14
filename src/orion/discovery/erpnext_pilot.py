"""Permit-gated wrapper of the existing bounded historical reader."""
import json
from urllib.parse import parse_qs, quote, urlsplit

from ..contracts import EvidenceKind
from .erpnext_adapter import _default_opener, _normalize_base_url
from .erpnext_historical_sample import ERPNextHistoricalSampleAdapter
from .pilot_read import PilotReadPermit


class ERPNextPilotReader:
    def __init__(self, *, source_id, api_key, api_secret, opener=None):
        self._source_id = _normalize_base_url(source_id)
        self._api_key = api_key
        self._api_secret = api_secret
        self._opener = opener or _default_opener

    @property
    def source_id(self):
        return self._source_id

    def read(self, permit):
        if not isinstance(permit, PilotReadPermit):
            raise TypeError('launcher-issued read permit required')
        request, grant = permit.check(self._source_id)
        if (grant.identity_field != 'name' or grant.company_field != 'company'
                or grant.provenance_source != 'erpnext-historical-sample-read-only'
                or grant.evidence_kind is not EvidenceKind.API):
            raise ValueError('unsupported ERP provenance field mapping')

        def guarded_transport(http_request, timeout):
            permit.check(self._source_id)
            target = urlsplit(http_request.full_url)
            if (http_request.method != 'GET' or target.fragment
                    or f'{target.scheme}://{target.netloc}' != self._source_id
                    or target.path != '/api/resource/' + quote(request.resource, safe='')):
                raise ValueError('transport exceeded read-only source boundary')
            query = parse_qs(target.query, strict_parsing=True)
            expected = {
                'fields': [json.dumps(list(request.fields), separators=(',', ':'))],
                'filters': [json.dumps([['company', '=', request.company],
                                       ['docstatus', '=', 1]] + grant.window.filters(),
                                      separators=(',', ':'))],
                'order_by': [f'{request.date_field} desc, name desc'],
                'limit_start': ['0'], 'limit_page_length': [str(request.max_records)],
            }
            if query != expected:
                raise ValueError('encoded request differs from authorized read')
            permit.claim_io(self._source_id)
            return self._opener(http_request, timeout=timeout)

        adapter = ERPNextHistoricalSampleAdapter(
            base_url=self._source_id, tenant_id=request.tenant_id,
            api_key=self._api_key, api_secret=self._api_secret,
            resource=request.resource, company=request.company, fields=request.fields,
            sample_size=request.max_records,
            order_by=f'{request.date_field} desc, name desc',
            opener=guarded_transport, read_window=grant.window, clock=permit.current_time,
        )
        try:
            return adapter.discover()
        except Exception:  # noqa: BLE001 - withhold transport and private row details
            raise ValueError('pilot adapter read rejected') from None
