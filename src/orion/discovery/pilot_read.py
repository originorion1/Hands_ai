"""Vendor-neutral admission for one externally authorized pilot read.

Grant lookup is a trusted control-plane dependency. A request cannot supply its
own grant. This boundary is not a sandbox for hostile Python code or plugins.
"""
import hashlib
import json
import math
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass, replace
from datetime import date
from types import MappingProxyType
from typing import Protocol
from uuid import NAMESPACE_URL, UUID, uuid5

from ..contracts import Evidence, EvidenceKind, Observation, ObservationMode, utc_now
from .read_window import ReviewedReadWindow


def _text(value):
    if (not isinstance(value, str) or not value or value != value.strip()
            or len(value) > 256 or '*' in value or not value.isprintable()):
        raise ValueError('exact bounded identity required')


@dataclass(frozen=True, slots=True)
class PilotAuthorization:
    authorization_id: str
    source_id: str
    window: ReviewedReadWindow
    identity_field: str
    company_field: str
    provenance_source: str
    evidence_kind: EvidenceKind
    max_records: int

    def __post_init__(self):
        for value in (self.authorization_id, self.source_id, self.identity_field,
                      self.company_field, self.provenance_source):
            _text(value)
        if not isinstance(self.window, ReviewedReadWindow):
            raise TypeError('reviewed window required')
        self.window.__post_init__()
        for value in (self.window.tenant_id, self.window.company, self.window.resource,
                      *self.window.fields):
            _text(value)
        if not {self.identity_field, self.company_field} <= set(self.window.fields):
            raise ValueError('identity and company provenance fields required')
        if len({self.identity_field, self.company_field, self.window.date_field}) != 3:
            raise ValueError('identity, company and date fields must be distinct')
        if not isinstance(self.evidence_kind, EvidenceKind):
            raise TypeError('explicit evidence kind required')
        if type(self.max_records) is not int or not 1 <= self.max_records <= 10000:
            raise ValueError('bounded record allowance required')


@dataclass(frozen=True, slots=True)
class PilotRequest:
    tenant_id: str
    company: str
    source_id: str
    resource: str
    fields: tuple[str, ...]
    date_field: str
    start: date
    end: date
    max_records: int

    def __post_init__(self):
        if type(self.fields) is not tuple or not self.fields or len(set(self.fields)) != len(self.fields):
            raise ValueError('unique immutable explicit fields required')
        for value in (self.tenant_id, self.company, self.source_id, self.resource,
                      self.date_field, *self.fields):
            _text(value)
        if self.date_field not in self.fields:
            raise ValueError('date field required in request')
        if type(self.start) is not date or type(self.end) is not date or self.start > self.end:
            raise ValueError('unambiguous date bounds required')
        if type(self.max_records) is not int or self.max_records < 1:
            raise ValueError('positive record bound required')


_SEAL = object()


class PilotReadPermit:
    """Short-lived launcher capability; not a serialized or user-created grant."""
    __slots__ = ('_active', '_clock', '_guard', '_used', '_wire')

    def __init__(self, seal, guard, clock):
        if seal is not _SEAL:
            raise ValueError('permit must be issued by launcher')
        self._clock = clock
        self._guard = guard
        self._active = True
        self._used = False
        self._wire = None

    def check(self, source_id):
        if not self._active:
            raise ValueError('pilot permit is closed')
        request, authorization = self._guard()
        if source_id != request.source_id:
            raise ValueError('adapter source mismatch')
        return request, authorization

    def current_time(self):
        if not self._active:
            raise ValueError('pilot permit is closed')
        return self._clock()

    def bind_wire(self, request):
        """Bind the trusted adapter's validated encoding once, before transport."""
        self.check(self._guard()[0].source_id)
        if self._wire is not None or self._used:
            raise ValueError('pilot wire binding already consumed')
        self._wire = (request.full_url, request.get_method(), request.data,
                      tuple(sorted(request.header_items())))

    def check_wire(self, request):
        current, _ = self._guard()
        self.check(current.source_id)
        wire = (request.full_url, request.get_method(), request.data,
                tuple(sorted(request.header_items())))
        if self._wire is None or wire != self._wire:
            raise ValueError('request differs from bound pilot wire request')
        return current.source_id

    def claim_io(self, source_id):
        result = self.check(source_id)
        if self._used:
            raise ValueError('pilot read permits one transport request')
        self._used = True
        return result


class PilotReader(Protocol):
    @property
    def source_id(self) -> str: ...

    def read(self, permit: PilotReadPermit) -> tuple[Observation, ...]: ...


def _authorize(request, authorization_id, lookup, clock):
    grant = lookup(authorization_id)
    if not isinstance(grant, PilotAuthorization):
        raise ValueError('authorization missing or ambiguous')  # noqa: TRY004 - deny all absent grants
    grant.__post_init__()
    if grant.authorization_id != authorization_id or grant.source_id != request.source_id:
        raise ValueError('authorization identity or source mismatch')
    grant.window.check(request.tenant_id, request.company, request.resource, request.fields, clock())
    if ((request.date_field, request.start, request.end)
            != (grant.window.date_field, grant.window.start, grant.window.end)
            or request.max_records > grant.max_records):
        raise ValueError('request exceeds authorized window or bound')
    return grant


def _admit(observations, request, grant, now):
    if type(observations) is not tuple or len(observations) > request.max_records:
        raise ValueError('reader must return a bounded immutable batch')
    digest = hashlib.sha256(json.dumps(asdict(grant), sort_keys=True,
                                      default=lambda x: x.isoformat()).encode()).hexdigest()
    admitted = []
    identities, evidence_ids, observation_ids = set(), set(), set()
    for observation in observations:
        if not isinstance(observation, Observation) or observation.mode is not ObservationMode.READ_ONLY:
            raise ValueError('read-only observation required')
        evidence = observation.evidence
        if not isinstance(evidence, Evidence):
            raise TypeError('canonical evidence required')
        confidence = evidence.confidence
        if confidence is not None and (type(confidence) not in (int, float)
                or not math.isfinite(confidence) or not 0 <= confidence <= 1):
            raise ValueError('invalid evidence confidence')
        if (evidence.tenant_id != request.tenant_id or evidence.kind is not grant.evidence_kind
                or evidence.source != grant.provenance_source):
            raise ValueError('evidence tenant/source provenance mismatch')
        if not isinstance(evidence.evidence_id, UUID) or not isinstance(observation.observation_id, UUID):
            raise TypeError('provenance identities required')
        ReviewedReadWindow.check_time_type(evidence.observed_at)
        if evidence.observed_at > now:
            raise ValueError('future evidence observation time')
        payload = evidence.payload
        if not isinstance(payload, Mapping) or set(payload) != {'resource', 'record'}:
            raise ValueError('explicit record provenance required')
        record = payload['record']
        if payload['resource'] != request.resource or not isinstance(record, Mapping):
            raise ValueError('record resource provenance mismatch')
        if set(record) != set(request.fields):
            raise ValueError('returned field scope mismatch')
        # Pilot v1 supports flat scalar records only; no hidden nested payloads.
        for value in record.values():
            if value is not None and type(value) not in (str, int, float, bool):
                raise ValueError('unsupported record value')
            if type(value) is float and not math.isfinite(value):
                raise ValueError('non-finite record value')
        identity = record[grant.identity_field]
        _text(identity)
        if record[grant.company_field] != request.company:
            raise ValueError('record company mismatch')
        grant.window.validate_row(record)
        if (identity in identities or evidence.evidence_id in evidence_ids
                or observation.observation_id in observation_ids):
            raise ValueError('duplicate record or provenance identity')
        identities.add(identity)
        evidence_ids.add(evidence.evidence_id)
        observation_ids.add(observation.observation_id)
        acquisition = json.dumps({
            'version': 1, 'scope_sha256': digest, 'tenant_id': request.tenant_id,
            'resource': request.resource, 'record': dict(record),
            'observed_at': evidence.observed_at.isoformat(),
            'kind': evidence.kind, 'source': evidence.source,
            'confidence': confidence, 'mode': observation.mode,
        }, sort_keys=True, separators=(',', ':'), allow_nan=False)
        evidence_id = uuid5(NAMESPACE_URL, 'orion:pilot:evidence:v1:' + acquisition)
        observation_id = uuid5(NAMESPACE_URL, 'orion:pilot:observation:v1:' + acquisition)
        provenance = MappingProxyType({'authorization_id': grant.authorization_id,
            'scope_sha256': digest, 'source_id': grant.source_id, 'source_record_id': identity,
            'upstream_evidence_id': str(evidence.evidence_id),
            'upstream_observation_id': str(observation.observation_id)})
        frozen = MappingProxyType({'resource': request.resource,
            'record': MappingProxyType(dict(record)), 'provenance': provenance})
        admitted.append(replace(observation, observation_id=observation_id,
                                evidence=replace(evidence, evidence_id=evidence_id, payload=frozen)))
    return tuple(admitted)


def launch_pilot_read(request: PilotRequest, *, authorization_id: str,
                      lookup: Callable[[str], PilotAuthorization | None],
                      adapter: PilotReader, clock=utc_now) -> tuple[Observation, ...]:
    """Authorize, dispatch once, reauthorize and atomically admit a read-only batch."""
    if not isinstance(request, PilotRequest):
        raise TypeError('explicit pilot request required')
    request.__post_init__()
    _text(authorization_id)
    if not callable(lookup) or not callable(clock):
        raise TypeError('trusted grant lookup and clock required')
    initial = _authorize(request, authorization_id, lookup, clock)
    if adapter.source_id != initial.source_id:
        raise ValueError('adapter source mismatch')

    def guard():
        current = _authorize(request, authorization_id, lookup, clock)
        if current != initial:
            raise ValueError('authorization changed during read')
        return request, current

    def operation(permit):
        observations = adapter.read(permit)
        guard()
        return _admit(observations, request, initial, clock())

    return _run_permitted_read(guard, clock, operation)


def _run_permitted_read(guard, clock, operation):
    """Shared permit lifecycle for separately authorized metadata operations."""
    guard()
    permit = PilotReadPermit(_SEAL, guard, clock)
    try:
        result = operation(permit)
        guard()
        return result
    finally:
        permit._active = False
        permit._wire = None
