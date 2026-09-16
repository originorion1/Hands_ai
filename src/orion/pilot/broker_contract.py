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
from ..discovery.pilot_read import PilotAuthorization, PilotRequest
from ..discovery.read_window import ReviewedReadWindow
from ..history.evidence import _observation_from_data
from ..understanding.role_checkpoint import _json

VERSION = 'local-broker-v3'
MAX_FRAME = 65536
INSTRUMENT_OPERATIONS = tuple('instrument_' + str(n) for n in range(8))


def is_record_operation(operation):
    """Fixed governed record routes; never a caller-selected transport or URL."""
    return operation == 'read' or operation in INSTRUMENT_OPERATIONS


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
