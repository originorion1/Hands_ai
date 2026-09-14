"""Pilot-gated reuse of the existing ERP metadata and catalog readers."""
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit
from urllib.request import Request

from ..understanding.metadata import build_metadata_understanding
from .erpnext_adapter import _normalize_base_url
from .erpnext_live_session import derive_metadata_scope_candidate, is_sensitive_metadata_name
from .erpnext_metadata_adapter import ERPNextMetadataAdapter
from .erpnext_metadata_preflight import _contains_sensitive_embedded_value, _read_name_catalog
from .pilot_metadata import MetadataAuthorization, MetadataTarget, ScopeProposal
from .pilot_read import PilotReadPermit
from .pilot_transport import open_pilot_read


@dataclass(frozen=True)
class _CatalogLocation:
    base_url: str


class ERPNextPilotMetadataReader:
    def __init__(self, *, source_id, api_key, api_secret, opener=None):
        self._source_id = _normalize_base_url(source_id)
        self._api_key = api_key
        self._api_secret = api_secret
        self._opener = opener

    @property
    def source_id(self):
        return self._source_id

    def _scope(self, permit):
        if type(permit) is not PilotReadPermit:
            raise TypeError('metadata launcher permit required')
        request, grant = permit.check(self.source_id)
        if type(grant) is not MetadataAuthorization or type(request) is not MetadataTarget:
            raise ValueError('record grants cannot authorize metadata')
        return request, grant

    def _transport(self, permit, path, query):
        self._scope(permit)
        def dispatch(req, timeout):
            self._scope(permit)
            target = urlsplit(req.full_url)
            if (req.get_method() != 'GET' or req.data is not None or target.fragment
                    or f'{target.scheme}://{target.netloc}' != self.source_id
                    or target.path != path
                    or parse_qs(target.query, strict_parsing=True, keep_blank_values=True) != query):
                raise ValueError('metadata wire scope mismatch')
            authenticated = Request(req.full_url, method='GET', headers={
                'Accept': 'application/json',
                'Authorization': f'token {self._api_key}:{self._api_secret}'})
            permit.bind_wire(authenticated)
            return open_pilot_read(authenticated, permit=permit, timeout=timeout, opener=self._opener)
        return dispatch

    def catalog(self, permit, requested):
        target, grant = self._scope(permit)
        if target.resource is not None:
            raise ValueError('schema permit cannot enumerate catalog')
        if requested != grant.max_catalog_entries + 1:
            raise ValueError('catalog bound mismatch')
        transport = self._transport(permit, '/api/resource/DocType', {
            'fields': ['["name"]'], 'limit_start': ['0'],
            'limit_page_length': [str(requested)], 'order_by': ['name asc']})
        try:
            return _read_name_catalog(_CatalogLocation(self.source_id), resource='DocType',
                                      requested=requested, opener=transport)
        except Exception:  # noqa: BLE001 - no private transport detail
            raise ValueError('pilot metadata catalog rejected') from None

    def schema(self, permit, resource):
        request, grant = self._scope(permit)
        if request.resource != resource or resource in grant.excluded_resources:
            raise ValueError('schema target exceeds issued metadata permit')
        if is_sensitive_metadata_name(resource):
            return None
        transport = self._transport(permit, '/api/method/frappe.desk.form.load.getdoctype',
                                    {'doctype': [resource]})
        try:
            observations = ERPNextMetadataAdapter(base_url=self.source_id,
                tenant_id=request.tenant_id, api_key=self._api_key, api_secret=self._api_secret,
                doctypes=(resource,), opener=transport).discover()
            raw = observations[0].evidence.payload['metadata']
            if _contains_sensitive_embedded_value(raw):
                return None
            candidate = derive_metadata_scope_candidate(request.tenant_id, resource, observations)
            if candidate is None:
                return None
            entity = build_metadata_understanding(observations, tenant_id=request.tenant_id,
                allowed_doctypes=frozenset({resource})).entities[0]
            if not entity.is_submittable:
                return None
            dates = tuple(sorted(f.fieldname for f in entity.fields
                if f.fieldtype == 'Date' and f.fieldname in candidate.fields))
            fields = tuple(sorted(set(candidate.fields) | {'name', 'company', 'docstatus'}))
            return ScopeProposal(resource, fields, dates)
        except Exception:  # noqa: BLE001 - no raw schema payload in diagnostics
            raise ValueError('pilot metadata schema rejected') from None
