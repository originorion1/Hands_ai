"""Strict local broker wire formats. No authority issuer or execution interface."""
import hashlib
import hmac
import json
import os
import stat
from dataclasses import replace
from datetime import date, datetime
from pathlib import Path
from types import MappingProxyType

from ..contracts import EvidenceKind
from ..discovery.json_boundary import unique_json_object
from ..discovery.pilot_metadata import MetadataAuthorization, MetadataRequest
from ..discovery.pilot_read import PilotAuthorization, PilotRequest, _text
from ..discovery.read_window import ReviewedReadWindow
from ..history.evidence import _observation_from_data
from ..understanding.role_checkpoint import _json

VERSION = 'local-broker-v3'
ERPNEXT_VERSION = 'erpnext-candidate-broker-v1'
GRANT_TRANSITION_VERSION = 'erpnext-record-grant-transition-v1'
MAX_FRAME = 65536
INSTRUMENT_OPERATIONS = tuple('instrument_' + str(n) for n in range(8))


def is_record_operation(operation):
    """Fixed governed record routes; never a caller-selected transport or URL."""
    return operation == 'read' or operation in INSTRUMENT_OPERATIONS


def is_erpnext_candidate(config):
    """Explicit version dispatch; legacy synthetic configs remain unchanged."""
    return type(config) is dict and config.get('version') == ERPNEXT_VERSION


def transition_policy_from(value):
    """Validate the enrolled envelope without inventing a future record scope."""
    value = exact(value, (
        'version', 'tenant_id', 'company', 'source_id', 'caller',
        'secret_reference', 'auth_reference', 'limits', 'max_fields',
        'max_records', 'max_window_days', 'provenance_source',
    ))
    if value['version'] != GRANT_TRANSITION_VERSION:
        raise ValueError('explicit grant transition version required')
    for name in (
        'tenant_id', 'company', 'source_id', 'caller', 'secret_reference',
        'auth_reference', 'provenance_source',
    ):
        _text(value[name])
    if not value['source_id'].startswith('https://') or not value['source_id'].endswith('.test'):
        raise ValueError('bounded synthetic transition source required')
    from .journal import TransportLimits

    limits = dict(exact(value['limits'], TransportLimits.__dataclass_fields__))
    limits['expires_at'] = datetime.fromisoformat(limits['expires_at'])
    TransportLimits(**limits)
    for name, high in (('max_fields', 64), ('max_records', 25), ('max_window_days', 31)):
        if type(value[name]) is not int or not 1 <= value[name] <= high:
            raise ValueError('bounded grant transition envelope required')
    return value


def transition_binding(policy):
    return digest({'version': GRANT_TRANSITION_VERSION,
                   'policy': transition_policy_from(policy)})


def transition_initial_head(policy):
    return digest({'version': GRANT_TRANSITION_VERSION,
                   'scope_binding': transition_binding(policy),
                   'state': 'unprovisioned'})


def transition_record_config(value, policy):
    """Return one exact canonical record grant inside the enrolled envelope."""
    policy = transition_policy_from(policy)
    exact(value, (
        'version', 'mode', 'caller', 'grant', 'limits', 'protocol',
        'secret_reference', 'auth_reference', 'field_classifications', 'operation',
    ))
    if (
        value['version'] != ERPNEXT_VERSION
        or value['mode'] != 'candidate_erpnext_read_only'
        or value['operation'] != 'read'
        or value['protocol'] != 'erpnext_records_v1'
        or any(value[name] != policy[name] for name in (
            'caller', 'secret_reference', 'auth_reference', 'limits'
        ))
    ):
        raise ValueError('exact transitioned record configuration required')
    grant = grant_from(value['grant'])
    window = grant.window
    if (
        (window.tenant_id, window.company, grant.source_id)
        != (policy['tenant_id'], policy['company'], policy['source_id'])
        or grant.evidence_kind is not EvidenceKind.API
        or grant.provenance_source != policy['provenance_source']
        or len(window.fields) > policy['max_fields']
        or grant.max_records > policy['max_records']
        or (window.end - window.start).days > policy['max_window_days']
        or window.expires_at > datetime.fromisoformat(policy['limits']['expires_at'])
        or not {grant.identity_field, grant.company_field, window.date_field}
        <= set(window.fields)
    ):
        raise ValueError('record grant exceeds transition envelope')
    validate_field_classifications(value['field_classifications'], window.fields)
    return grant


def transition_request_from(value, policy, deployment_identity):
    """Validate one signed generation-zero-to-one transition request."""
    exact(value, (
        'version', 'deployment_identity', 'generation', 'predecessor_generation',
        'config', 'metadata', 'expected_witness_sha256', 'mac',
    ))
    if (
        value['version'] != GRANT_TRANSITION_VERSION
        or value['deployment_identity'] != deployment_identity
        or value['generation'] != 1
        or value['predecessor_generation'] != 0
    ):
        raise ValueError('grant transition predecessor denied')
    exact(value['metadata'], (
        'binding', 'checkpoint', 'evidence_head', 'payload_sha256',
        'journal_head', 'request_reference', 'observation_id', 'evidence_id',
    ))
    for name in ('deployment_identity', 'expected_witness_sha256', 'mac'):
        if type(value[name]) is not str or len(value[name]) != 64:
            raise ValueError('grant transition digest required')
    for name in ('binding', 'evidence_head', 'payload_sha256', 'journal_head',
                 'request_reference'):
        reference = value['metadata'][name]
        if type(reference) is not str or len(reference) != 64:
            raise ValueError('metadata transition reference required')
    if type(value['metadata']['checkpoint']) is not int or value['metadata']['checkpoint'] < 2:
        raise ValueError('metadata transition checkpoint required')
    for name in ('observation_id', 'evidence_id'):
        identity = value['metadata'][name]
        if type(identity) is not str or not identity or len(identity) > 64:
            raise ValueError('metadata transition identity required')
    transition_record_config(value['config'], policy)
    return value


def digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def authenticate(key, purpose, value):
    """Trusted supervisor/issuer utility, not exposed by the application wire API."""
    if type(key) is not bytes or len(key) < 32:
        raise ValueError('invalid authentication key')
    return hmac.new(key, _json((VERSION, purpose, value)).encode(), hashlib.sha256).hexdigest()


def exact(value, keys):
    if type(value) is not dict or set(value) != set(keys):
        raise ValueError('wire shape mismatch')
    return value


def decode(raw):
    if type(raw) is not bytes or not 1 <= len(raw) <= MAX_FRAME:
        raise ValueError('bounded wire frame required')
    return json.loads(raw, object_pairs_hook=unique_json_object,
                      parse_constant=lambda _: (_ for _ in ()).throw(ValueError('nonfinite')))


def request_from(value):
    value = dict(exact(value, PilotRequest.__dataclass_fields__))
    if type(value['fields']) is not list:
        raise ValueError('field list required')
    value['fields'] = tuple(value['fields'])
    for name in ('start', 'end'):
        value[name] = date.fromisoformat(value[name])
    return PilotRequest(**value)


def grant_from(value):
    value = dict(exact(value, PilotAuthorization.__dataclass_fields__))
    window = dict(exact(value['window'], ReviewedReadWindow.__dataclass_fields__))
    if type(window['fields']) is not list:
        raise ValueError('field list required')
    window['fields'] = tuple(window['fields'])
    for name in ('start', 'end'):
        window[name] = date.fromisoformat(window[name])
    window['expires_at'] = datetime.fromisoformat(window['expires_at'])
    value['window'] = ReviewedReadWindow(**window)
    value['evidence_kind'] = EvidenceKind(value['evidence_kind'])
    return PilotAuthorization(**value)


def private_bytes(path, limit=MAX_FRAME):
    """Owner-only regular files. Not a defense against hostile same-UID processes."""
    fd = os.open(Path(path), os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
    with os.fdopen(fd, 'rb') as stream:
        info = os.fstat(stream.fileno())
        if (not stat.S_ISREG(info.st_mode) or info.st_uid != os.getuid()
                or stat.S_IMODE(info.st_mode) & 0o077 or info.st_nlink != 1):
            raise ValueError('private regular input required')
        result = stream.read(limit + 1)
    if len(result) > limit:
        raise ValueError('input oversized')
    return result


def observations_from(values):
    """Restore canonical immutable values from the trusted broker pipe, not grants."""
    if type(values) is not list or len(values) > 25:
        raise ValueError('bounded observation reply required')

    def freeze(v):
        if type(v) is dict:
            return MappingProxyType({k: freeze(x) for k, x in v.items()})
        if type(v) is list:
            return tuple(freeze(x) for x in v)
        return v
    result = []
    for value in values:
        observation = _observation_from_data(value)
        result.append(replace(observation, evidence=replace(observation.evidence,
                      payload=freeze(observation.evidence.payload))))
    return tuple(result)


def validate_field_classifications(value, fields):
    """Trusted control-plane labels, bound by the existing configuration MAC.

    Only public fields are supported. This checks policy, not the truth of a
    collector's labels; it is not content inspection or an authority issuer.
    """
    exact(value, fields)
    if any(type(label) is not str or label != 'public' for label in value.values()):
        raise ValueError('field classification denied')


def metadata_request_from(value):
    return MetadataRequest(**exact(value, MetadataRequest.__dataclass_fields__))


def metadata_grant_from(value):
    value = dict(exact(value, MetadataAuthorization.__dataclass_fields__))
    value['request'] = metadata_request_from(value['request'])
    value['expires_at'] = datetime.fromisoformat(value['expires_at'])
    if type(value['excluded_resources']) is not list:
        raise ValueError('explicit exclusion list required')
    value['excluded_resources'] = tuple(value['excluded_resources'])
    return MetadataAuthorization(**value)
