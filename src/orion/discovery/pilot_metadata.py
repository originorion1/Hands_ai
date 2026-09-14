"""Separate site-schema discovery authorization; never a business read grant."""
import hashlib
import json
from dataclasses import asdict, dataclass
from datetime import datetime
from types import MappingProxyType
from uuid import NAMESPACE_URL, uuid5

from ..contracts import Evidence, EvidenceKind, Observation, utc_now
from .pilot_read import _run_permitted_read, _text
from .read_window import ReviewedReadWindow


@dataclass(frozen=True, slots=True)
class MetadataRequest:
    tenant_id: str
    company: str
    source_id: str

    def __post_init__(self):
        for value in (self.tenant_id, self.company, self.source_id):
            _text(value)


@dataclass(frozen=True, slots=True)
class MetadataTarget:
    tenant_id: str
    company: str
    source_id: str
    resource: str | None


@dataclass(frozen=True, slots=True)
class MetadataAuthorization:
    authorization_id: str
    request: MetadataRequest
    expires_at: datetime
    site_schema_read: bool
    max_catalog_entries: int
    max_schemas: int
    excluded_resources: tuple[str, ...]

    def __post_init__(self):
        _text(self.authorization_id)
        if type(self.request) is not MetadataRequest:
            raise TypeError('explicit metadata request scope required')
        self.request.__post_init__()
        ReviewedReadWindow.check_time_type(self.expires_at)
        if self.site_schema_read is not True:
            raise ValueError('site-wide schema permission must be explicit')
        for value, maximum in ((self.max_catalog_entries, 100), (self.max_schemas, 10)):
            if type(value) is not int or not 1 <= value <= maximum:
                raise ValueError('bounded metadata allowance required')
        if self.max_schemas > self.max_catalog_entries:
            raise ValueError('schema budget exceeds catalog budget')
        if type(self.excluded_resources) is not tuple:
            raise TypeError('explicit immutable exclusions required')
        if len(set(self.excluded_resources)) != len(self.excluded_resources):
            raise ValueError('duplicate exclusions')
        for name in self.excluded_resources:
            _text(name)


@dataclass(frozen=True, slots=True)
class ScopeProposal:
    resource: str
    fields: tuple[str, ...]
    date_fields: tuple[str, ...]

    def __post_init__(self):
        _text(self.resource)
        for values in (self.fields, self.date_fields):
            if type(values) is not tuple or len(set(values)) != len(values):
                raise ValueError('unique immutable proposal fields required')
            for value in values:
                _text(value)
        if not set(self.date_fields) <= set(self.fields):
            raise ValueError('date candidates must be proposed fields')


@dataclass(frozen=True, slots=True)
class MetadataDiscovery:
    catalog: tuple[str, ...]
    catalog_complete: bool
    schema_targets: tuple[str, ...]
    proposals: tuple[ScopeProposal, ...]
    observations: tuple[Observation, ...]
    review_required: bool = True
    record_reads_allowed: bool = False


def launch_pilot_metadata(request, *, authorization_id, lookup, adapter, clock=utc_now):
    """One bounded catalog read plus bounded schema reads; no persistence or retry."""
    if type(request) is not MetadataRequest:
        raise TypeError('explicit metadata request required')
    request.__post_init__()
    _text(authorization_id)
    if not callable(lookup) or not callable(clock):
        raise TypeError('trusted metadata lookup and clock required')

    def authorize():
        value = lookup(authorization_id)
        if type(value) is not MetadataAuthorization:
            raise ValueError('metadata authorization missing or ambiguous')
        value.__post_init__()
        if value.authorization_id != authorization_id or value.request != request:
            raise ValueError('metadata grant scope mismatch')
        now = clock()
        ReviewedReadWindow.check_time_type(now)
        if now >= value.expires_at:
            raise ValueError('metadata grant expired')
        return value

    initial = authorize()
    if adapter.source_id != request.source_id:
        raise ValueError('metadata adapter source mismatch')

    def guard(resource=None):
        if authorize() != initial:
            raise ValueError('metadata grant changed')
        return MetadataTarget(request.tenant_id, request.company, request.source_id, resource), initial

    catalog, complete = _run_permitted_read(guard, clock,
        lambda permit: adapter.catalog(permit, initial.max_catalog_entries + 1))
    if (type(catalog) is not tuple or type(complete) is not bool
            or len(catalog) > initial.max_catalog_entries + 1
            or catalog != tuple(sorted(set(catalog)))
            or complete != (len(catalog) < initial.max_catalog_entries + 1)):
        raise ValueError('malformed metadata catalog')
    for name in catalog:
        _text(name)
    visible = catalog[:initial.max_catalog_entries]
    selected = tuple(n for n in visible
                     if n not in initial.excluded_resources)
    proposals = []
    examined = []
    for name in selected[:initial.max_schemas]:
        proposal = _run_permitted_read(lambda name=name: guard(name), clock,
            lambda permit, name=name: adapter.schema(permit, name))
        examined.append(name)
        if proposal is not None:
            if type(proposal) is not ScopeProposal or proposal.resource != name:
                raise ValueError('metadata proposal source mismatch')
            proposal.__post_init__()
            proposals.append(proposal)
    guard()
    now = clock()
    ReviewedReadWindow.check_time_type(now)
    digest = hashlib.sha256(json.dumps(asdict(initial), sort_keys=True,
        default=lambda value: value.isoformat()).encode()).hexdigest()
    payload = MappingProxyType({'catalog': visible, 'catalog_complete': complete,
        'schema_targets': tuple(examined),
        'proposals': tuple(MappingProxyType(asdict(p)) for p in proposals),
        'company_context': request.company, 'site_schema_scope': True,
        'authorization_id': authorization_id, 'scope_sha256': digest,
        'source_id': request.source_id, 'review_required': True,
        'record_reads_allowed': False})
    identity = json.dumps({'scope': digest, 'catalog': visible, 'complete': complete,
        'examined': examined, 'proposals': [asdict(p) for p in proposals],
        'observed_at': now.isoformat()}, sort_keys=True)
    evidence = Evidence(EvidenceKind.METADATA, 'pilot-metadata-discovery', payload,
        observed_at=now, tenant_id=request.tenant_id,
        evidence_id=uuid5(NAMESPACE_URL, 'orion:metadata:evidence:' + identity))
    observation = Observation(evidence,
        observation_id=uuid5(NAMESPACE_URL, 'orion:metadata:observation:' + identity))
    guard()
    return MetadataDiscovery(visible, complete, tuple(examined), tuple(proposals), (observation,))
