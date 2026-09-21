"""Fixed synthetic schema protocol. No record reader, URLs or semantic authority."""
import hashlib
import hmac

from ..contracts import utc_now
from ..discovery.pilot_metadata import ScopeProposal, _metadata_guard
from ..discovery.pilot_read import _run_permitted_read, _text
from ..understanding.schema_evidence import FieldDeclaration, interpret_schema
from .broker_contract import (
    decode,
    exact,
    metadata_grant_from,
    metadata_request_from,
    private_bytes,
)


def proposal_from(resource, declarations):
    """Recompute structural candidates; labels never establish business meaning."""
    if type(declarations) is not list or len(declarations) > 64:
        raise ValueError('bounded declarations required')
    fields = tuple(FieldDeclaration(**exact(d, FieldDeclaration.__dataclass_fields__))
                   for d in declarations)
    interpretation = interpret_schema(resource, fields)
    return ScopeProposal(resource, tuple(d.name for d in fields),
                         tuple(d.name for d in fields if d.kind == 'date'), interpretation)


def erpnext_proposal_from(resource, fields, date_fields, declarations):
    """Validate the native structural proposal without granting business meaning."""
    structural = proposal_from(resource, declarations)
    if (
        type(fields) is not list
        or type(date_fields) is not list
        or len(fields) > 64
        or fields != sorted(set(fields))
        or date_fields != sorted(set(date_fields))
        or not set(date_fields).issubset(fields)
        or (fields and not set(structural.fields).issubset(fields))
    ):
        raise ValueError('bounded ERPNext structural proposal required')
    for field in fields:
        _text(field)
    return ScopeProposal(
        resource, tuple(fields), tuple(date_fields), structural.interpretation
    )


def acquire_metadata(bootstrap):
    exact(bootstrap, ('operation', 'grant', 'request', 'path', 'source_digest', 'protocol',
                      'secret', 'field_classifications', 'target'))
    grant = metadata_grant_from(bootstrap['grant'])
    request = metadata_request_from(bootstrap['request'])
    if (bootstrap['operation'] != 'metadata'
            or bootstrap['protocol'] not in ('local_schema_v1', 'erpnext_metadata_v1')
            or bootstrap['field_classifications'] != {}):
        raise ValueError('explicit schema-only operation required')
    target = bootstrap['target']
    if target is not None:
        _text(target)
        if target in grant.excluded_resources:
            raise ValueError('excluded schema')
    _, guard = _metadata_guard(request, grant.authorization_id, lambda _: grant, utc_now)

    def read(permit):
        if bootstrap['protocol'] == 'erpnext_metadata_v1':
            from .erpnext_candidate import acquire_metadata as acquire_erpnext_metadata

            return acquire_erpnext_metadata(bootstrap, grant, request, permit, target)
        permit.claim_io(request.source_id)
        raw = private_bytes(bootstrap['path'])
        if hashlib.sha256(raw).hexdigest() != bootstrap['source_digest']:
            raise ValueError('schema source changed')
        data = exact(decode(raw), ('credential_digest', 'schemas'))
        secret = bootstrap['secret']
        if (type(secret) is not str or not secret or type(data['credential_digest']) is not str
                or not hmac.compare_digest(data['credential_digest'],
                                           hashlib.sha256(secret.encode()).hexdigest())):
            raise ValueError('schema source authentication failed')
        schemas = data['schemas']
        if type(schemas) is not dict or len(schemas) > 100:
            raise ValueError('bounded schema catalog required')
        for resource in schemas:
            _text(resource)
        if target is None:
            catalog = sorted(schemas)[:grant.max_catalog_entries + 1]
            return {'catalog': catalog, 'complete': len(catalog) < grant.max_catalog_entries + 1}
        if target not in schemas:
            raise ValueError('schema unavailable')
        rows = schemas[target]
        if type(rows) is not list or len(rows) > 64:
            raise ValueError('bounded schema required')
        declarations, seen = [], set()
        for row in rows:
            exact(row, ('name', 'kind', 'classification'))
            _text(row['name'])
            if row['name'] in seen:
                raise ValueError('duplicate declaration')
            seen.add(row['name'])
            if row['classification'] != 'public':
                continue  # Hidden/sensitive/unclassified fields confer no knowledge or permission.
            declarations.append({'resource': target, 'name': row['name'], 'kind': row['kind'],
                                 'source_type': 'local_schema_v1'})
        proposal_from(target, declarations)
        return {'resource': target, 'declarations': declarations}

    return _run_permitted_read(lambda: guard(target), utc_now, read)
