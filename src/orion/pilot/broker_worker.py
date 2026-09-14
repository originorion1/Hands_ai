"""Fixed one-shot local source worker. No networking or dynamically loaded adapter.

Bootstrap arrives only through a supervisor-owned pipe. Process ownership and
OS restrictions are external requirements, not established by this Python file.
"""
import argparse
import hashlib
import hmac
import os
import signal
import sys

from ..contracts import Evidence, Observation, utc_now
from ..discovery.pilot_read import launch_pilot_read
from ..history.evidence import _observation_to_data
from ..understanding.role_checkpoint import _json
from .broker_contract import (
    MAX_FRAME,
    authenticate,
    decode,
    exact,
    grant_from,
    private_bytes,
    request_from,
)


def acquire(bootstrap):
    exact(bootstrap, ('grant', 'request', 'path', 'source_digest', 'protocol', 'secret'))
    grant, request = grant_from(bootstrap['grant']), request_from(bootstrap['request'])
    if bootstrap['protocol'] not in ('local_rows_v1', 'local_columns_v1'):
        raise ValueError('unsupported local protocol')

    class LocalReader:
        source_id = grant.source_id

        def read(self, permit):
            permit.claim_io(self.source_id)
            raw = private_bytes(bootstrap['path'])
            if hashlib.sha256(raw).hexdigest() != bootstrap['source_digest']:
                raise ValueError('source changed')
            envelope = decode(raw)
            fields = ('resource', 'credential_digest', 'rows') if bootstrap['protocol'] == 'local_rows_v1' else (
                'resource', 'credential_digest', 'columns', 'values')
            exact(envelope, fields)
            secret = bootstrap['secret']
            if (type(secret) is not str or not secret or type(envelope['credential_digest']) is not str
                    or not hmac.compare_digest(envelope['credential_digest'],
                                               hashlib.sha256(secret.encode()).hexdigest())):
                raise ValueError('source authentication failed')
            if envelope['resource'] != request.resource:
                raise ValueError('source resource mismatch')
            if bootstrap['protocol'] == 'local_rows_v1':
                rows = envelope['rows']
            else:
                columns = envelope['columns']
                if (type(columns) is not list or any(type(x) is not str for x in columns)
                        or len(columns) != len(set(columns)) or type(envelope['values']) is not list):
                    raise ValueError('malformed columns')
                rows = [dict(zip(columns, row, strict=True)) for row in envelope['values']
                        if type(row) is list]
                if len(rows) != len(envelope['values']):
                    raise ValueError('malformed row')
            if type(rows) is not list or not 0 <= len(rows) <= min(25, request.max_records):
                raise ValueError('bounded source records required')
            now = utc_now()
            return tuple(Observation(Evidence(grant.evidence_kind, grant.provenance_source,
                {'resource': request.resource, 'record': row}, tenant_id=request.tenant_id,
                observed_at=now)) for row in rows)

    return launch_pilot_read(request, authorization_id=grant.authorization_id,
                            lookup=lambda _: grant, adapter=LocalReader())


def authenticated_acquire(envelope, key):
    exact(envelope, ('bootstrap', 'mac'))
    if (type(envelope['mac']) is not str or not hmac.compare_digest(envelope['mac'],
            authenticate(key, 'worker_bootstrap', envelope['bootstrap']))):
        raise ValueError('worker bootstrap authentication denied')
    return acquire(envelope['bootstrap'])


def main():
    try:
        # A worker also has its own deadline if its supervisor is terminated.
        signal.alarm(5)
        parser = argparse.ArgumentParser()
        parser.add_argument('--seal-fd', required=True, type=int)
        args = parser.parse_args()
        if args.seal_fd < 3:
            raise ValueError('dedicated supervisor descriptor required')
        with os.fdopen(args.seal_fd, 'rb') as stream:
            key = stream.read(65)
        if len(key) != 32:
            raise ValueError('invalid supervisor seal')
        observations = authenticated_acquire(decode(sys.stdin.buffer.read(MAX_FRAME + 1)), key)
        output = _json([_observation_to_data(o) for o in observations]).encode()
        if len(output) > MAX_FRAME:
            raise ValueError('reply oversized')
        sys.stdout.buffer.write(output)
        return 0
    except Exception:  # noqa: BLE001 - no source/secret/exception text crosses the pipe
        sys.stdout.write('{"status":"worker_denied"}')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
