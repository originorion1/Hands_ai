"""Reference-only role-study restart. Loading knowledge never restores authority.

The expected checkpoint digest must come from a separately trusted checkpoint
index. A digest stored alongside attacker-writable content is not authentication.
The archive and its acquisition-scope lookup remain trusted dependencies.
"""
import hashlib
import hmac
import json
import math
import re
from collections.abc import Mapping
from dataclasses import asdict
from datetime import date, datetime
from uuid import UUID

from ..contracts import Observation
from ..discovery.json_boundary import unique_json_object
from ..discovery.pilot_read import PilotRequest
from .role_study import RoleStudy

_VERSION = 1
_MAX_BYTES = 65536
_KEYS = {'version', 'tenant_id', 'company', 'source_id', 'schema', 'records'}


def _plain(value):
    if isinstance(value, Mapping):
        if any(type(key) is not str for key in value):
            raise ValueError('string payload keys required')
        return {key: _plain(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_plain(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, UUID):
        return str(value)
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if type(value) is float and math.isfinite(value):
        return value
    raise ValueError('unsupported checkpoint fingerprint value')


def _json(value):
    return json.dumps(_plain(value), sort_keys=True, separators=(',', ':'), allow_nan=False)


def _observation_digest(observation):
    if type(observation) is not Observation:
        raise ValueError('archived observation required')
    ev = observation.evidence
    return hashlib.sha256(_json({
        'observation_id': observation.observation_id, 'mode': observation.mode,
        'evidence_id': ev.evidence_id, 'kind': ev.kind, 'source': ev.source,
        'tenant_id': ev.tenant_id, 'observed_at': ev.observed_at,
        'confidence': ev.confidence, 'payload': ev.payload,
    }).encode()).hexdigest()


def _scope_digest(request):
    if type(request) is not PilotRequest:
        raise ValueError('trusted archived request required')
    request.__post_init__()
    return hashlib.sha256(_json(asdict(request)).encode()).hexdigest()


def checkpoint_sha256(payload: str) -> str:
    if type(payload) is not str or len(payload) > _MAX_BYTES:
        raise ValueError('bounded checkpoint text required')
    raw = payload.encode()
    if len(raw) > _MAX_BYTES:
        raise ValueError('checkpoint byte budget exceeded')
    return hashlib.sha256(raw).hexdigest()


def checkpoint_study(study: RoleStudy) -> str:
    """Produce canonical JSON containing references and hashes, no record values."""
    if type(study) is not RoleStudy:
        raise TypeError('role study required')
    rows = study.evidence_snapshot()
    payload = _json({
        'version': _VERSION, 'tenant_id': study.tenant,
        'company': study.company, 'source_id': study.source,
        'schema': {'id': str(study.schema.evidence.evidence_id),
                   'sha256': _observation_digest(study.schema)},
        'records': [{'id': str(obs.evidence.evidence_id),
                     'sha256': _observation_digest(obs), 'scope_sha256': _scope_digest(req)}
                    for obs, req in rows],
    })
    checkpoint_sha256(payload)
    return payload


def restore_study(payload: str, *, expected_sha256: str, tenant_id: str,
                  company: str, source_id: str, evidence_lookup) -> RoleStudy:
    """Reconstruct from trusted originals; persisted claims are never accepted."""
    if (type(expected_sha256) is not str
            or re.fullmatch('[0-9a-f]{64}', expected_sha256) is None
            or not hmac.compare_digest(checkpoint_sha256(payload), expected_sha256)):
        raise ValueError('checkpoint integrity mismatch')
    value = json.loads(payload, object_pairs_hook=unique_json_object)
    if (type(value) is not dict or set(value) != _KEYS
            or type(value['version']) is not int or value['version'] != _VERSION
            or (value['tenant_id'], value['company'], value['source_id']) != (
                tenant_id, company, source_id)):
        raise ValueError('checkpoint version or scope mismatch')
    records = value['records']
    if type(records) is not list or len(records) > 100:
        raise ValueError('checkpoint record budget exceeded')

    def resolve(reference, *, record=False):
        keys = {'id', 'sha256', 'scope_sha256'} if record else {'id', 'sha256'}
        if type(reference) is not dict or set(reference) != keys:
            raise ValueError('exact evidence reference required')
        try:
            identity = UUID(reference['id'])
        except (ValueError, TypeError, AttributeError) as exc:
            raise ValueError('invalid evidence identity') from exc
        if str(identity) != reference['id']:
            raise ValueError('canonical evidence identity required')
        observation = evidence_lookup(identity)
        if (type(observation) is not Observation or observation.evidence.evidence_id != identity
                or observation.evidence.tenant_id != tenant_id
                or _observation_digest(observation) != reference['sha256']):
            raise ValueError('missing or changed archived evidence')
        return observation

    schema = resolve(value['schema'])
    study = RoleStudy(schema, evidence_lookup=evidence_lookup)
    if (study.tenant, study.company, study.source) != (tenant_id, company, source_id):
        raise ValueError('schema binding mismatch')
    seen = set()
    for reference in records:
        observation = resolve(reference, record=True)
        identity = observation.evidence.evidence_id
        if identity in seen or identity == schema.evidence.evidence_id:
            raise ValueError('duplicate checkpoint evidence')
        seen.add(identity)
        request = evidence_lookup(('scope', identity))
        if _scope_digest(request) != reference['scope_sha256']:
            raise ValueError('archived acquisition scope changed')
        study.observe((observation,), request=request)
    # Recheck all originals, including ones read earlier in the reconstruction.
    study.evidence_snapshot()
    return study
