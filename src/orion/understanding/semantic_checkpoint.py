"""Reference-only semantic recovery; policy and original archive remain external."""
import hmac
import json
from dataclasses import asdict
from uuid import UUID

from ..discovery.json_boundary import unique_json_object
from .role_checkpoint import (
    _json,
    checkpoint_sha256,
    checkpoint_study,
    restore_study,
)
from .semantic_study import SemanticStudy


def checkpoint_semantic(study):
    study.evidence_snapshot()
    payload = _json({'version': 1, 'base': checkpoint_study(study.base),
        'policy': {'rules': [asdict(r) for r in study.rules],
                   'instruments': [asdict(i) for i in study.instruments]},
        'batches': [[{'id': str(key), 'fingerprint': study._fingerprints[key]} for key in batch]
                    for batch in study._batches],
        'revisions': [r.revision_id for r in study.history]})
    checkpoint_sha256(payload)
    return payload


def restore_semantic(payload, *, expected_sha256, tenant_id, company, source_id,
                     instruments, rules, evidence_lookup):
    if (type(expected_sha256) is not str
            or not hmac.compare_digest(checkpoint_sha256(payload), expected_sha256)):
        raise ValueError('semantic checkpoint integrity mismatch')
    data = json.loads(payload, object_pairs_hook=unique_json_object)
    policy = json.loads(_json({'rules': [asdict(r) for r in rules],
                               'instruments': [asdict(i) for i in instruments]}))
    if (type(data) is not dict or set(data) != {'version', 'base', 'policy', 'batches', 'revisions'}
            or type(data['version']) is not int or data['version'] != 1
            or data['policy'] != policy or type(data['batches']) is not list
            or len(data['batches']) > 100 or type(data['revisions']) is not list):
        raise ValueError('semantic checkpoint contract mismatch')
    base = restore_study(data['base'], expected_sha256=checkpoint_sha256(data['base']),
        tenant_id=tenant_id, company=company, source_id=source_id, evidence_lookup=evidence_lookup)
    study = SemanticStudy(base, instruments=instruments, rules=rules, evidence_lookup=evidence_lookup)
    seen = set()
    for batch in data['batches']:
        if type(batch) is not list or not 1 <= len(batch) <= 25:
            raise ValueError('malformed semantic revision batch')
        observations = []
        for ref in batch:
            if type(ref) is not dict or set(ref) != {'id', 'fingerprint'}:
                raise ValueError('malformed semantic evidence reference')
            key = UUID(ref['id'])
            if str(key) != ref['id'] or key in seen:
                raise ValueError('duplicate semantic checkpoint evidence')
            seen.add(key)
            obs = evidence_lookup(key)
            if json.loads(_json(study._fingerprint(obs))) != ref['fingerprint']:
                raise ValueError('semantic evidence changed')
            observations.append(obs)
        study.observe(tuple(observations))
    if [r.revision_id for r in study.history] != data['revisions']:
        raise ValueError('semantic revision history changed')
    study.evidence_snapshot()
    return study
