"""Integrity-bound process-role restart; reconstruct claims from original evidence."""
import hmac
import json
from dataclasses import asdict
from uuid import UUID

from ..discovery.json_boundary import unique_json_object
from .process_roles import ProcessRoleStudy, TraceProtocol
from .role_checkpoint import (
    _json,
    _observation_digest,
    _scope_digest,
    checkpoint_sha256,
    checkpoint_study,
    restore_study,
)


def checkpoint_process(study):
    if type(study) is not ProcessRoleStudy:
        raise TypeError('process study required')
    base,protocol,traces=study.evidence_snapshot()
    payload=_json({'version':1,'base':checkpoint_study(base),'protocol':asdict(protocol),
        'traces':[{'id':str(obs.evidence.evidence_id),'sha256':_observation_digest(obs),
                   'scope_sha256':_scope_digest(req)} for obs,req in traces]})
    checkpoint_sha256(payload)
    return payload


def restore_process(payload,*,expected_sha256,tenant_id,company,source_id,protocol,evidence_lookup):
    if (type(expected_sha256) is not str
            or not hmac.compare_digest(checkpoint_sha256(payload),expected_sha256)):
        raise ValueError('process checkpoint integrity mismatch')
    data=json.loads(payload,object_pairs_hook=unique_json_object)
    if (type(data) is not dict or set(data)!={'version','base','protocol','traces'}
            or type(data['version']) is not int or data['version']!=1
            or type(protocol) is not TraceProtocol or data['protocol']!=asdict(protocol)
            or type(data['traces']) is not list or len(data['traces'])>100):
        raise ValueError('process checkpoint contract mismatch')
    base=restore_study(data['base'],expected_sha256=checkpoint_sha256(data['base']),
        tenant_id=tenant_id,company=company,source_id=source_id,evidence_lookup=evidence_lookup)
    study=ProcessRoleStudy(base,protocol=protocol,evidence_lookup=evidence_lookup)
    seen=set()
    for ref in data['traces']:
        if type(ref) is not dict or set(ref)!={'id','sha256','scope_sha256'}:
            raise ValueError('trace reference malformed')
        identity=UUID(ref['id'])
        if str(identity)!=ref['id'] or identity in seen:
            raise ValueError('duplicate or noncanonical trace reference')
        seen.add(identity)
        obs=evidence_lookup(identity)
        req=evidence_lookup(('scope',identity))
        if (_observation_digest(obs)!=ref['sha256'] or _scope_digest(req)!=ref['scope_sha256']
                or obs.evidence.evidence_id!=identity):
            raise ValueError('trace evidence changed')
        study.observe_traces((obs,),request=req)
    study.evidence_snapshot()
    return study
