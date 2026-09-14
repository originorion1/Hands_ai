"""Offline supervised broker. Fixed subprocess, local sources, no network egress.

Run under a trusted external owner, separately from the ORION application. The
stdin protocol cannot choose credentials, paths, callables or worker commands.
Same-UID filesystem/process attacks are NOT contained by this module.
"""
import argparse
import hmac
import json
import os
import secrets
import subprocess
import sys
import time
from dataclasses import asdict, replace
from datetime import datetime
from pathlib import Path

from ..contracts import utc_now
from ..discovery.erpnext_adapter import _normalize_base_url
from ..discovery.erpnext_live_session import CredentialEnvironmentReferences
from ..discovery.pilot_metadata import launch_pilot_metadata
from ..discovery.pilot_read import _text, launch_pilot_read
from ..history.evidence import _observation_to_data
from ..understanding.role_checkpoint import _json
from .broker_contract import (
    MAX_FRAME,
    VERSION,
    authenticate,
    decode,
    digest,
    exact,
    grant_from,
    metadata_grant_from,
    metadata_request_from,
    observations_from,
    private_bytes,
    request_from,
    validate_field_classifications,
)
from .broker_metadata import proposal_from
from .journal import AttemptJournal, JournalDenied, TransportLimits


class Broker:
    """Trusted supervisor component; never instantiate inside an untrusted agent."""

    def __init__(self, config, directory, *, expected_head=None):
        exact(config, ('version', 'mode', 'caller', 'grant', 'limits', 'protocol',
            'secret_reference', 'auth_reference', 'source_path', 'source_digest',
            'field_classifications', 'operation'))
        if config['version'] != VERSION or config['mode'] != 'synthetic_read_only':
            raise ValueError('offline broker configuration required')
        _text(config['caller'])
        self.operation = config['operation']
        if self.operation == 'read':
            self.grant = grant_from(config['grant'])
            self.source_id = self.grant.source_id
            self.expires_at = self.grant.window.expires_at
            if (self.grant.max_records > 25 or len(self.grant.window.fields) > 64
                    or (self.grant.window.end - self.grant.window.start).days > 31):
                raise ValueError('bounded synthetic records required')
            protocols = ('local_rows_v1', 'local_columns_v1')
            validate_field_classifications(config['field_classifications'], self.grant.window.fields)
        elif self.operation == 'metadata':
            self.grant = metadata_grant_from(config['grant'])
            self.source_id = self.grant.request.source_id
            self.expires_at = self.grant.expires_at
            if self.grant.max_catalog_entries > 10 or self.grant.max_schemas > 2:
                raise ValueError('bounded synthetic discovery required')
            exact(config['field_classifications'], ())
            protocols = ('local_schema_v1',)
        else:
            raise ValueError('explicit read operation required')
        if (not _normalize_base_url(self.source_id).endswith('.test')
                or self.source_id != _normalize_base_url(self.source_id)):
            raise ValueError('bounded synthetic source required')
        if (config['protocol'] not in protocols or type(config['source_path']) is not str
                or not Path(config['source_path']).is_absolute()
                or type(config['source_digest']) is not str or len(config['source_digest']) != 64):
            raise ValueError('pinned local source required')
        limits = dict(exact(config['limits'], TransportLimits.__dataclass_fields__))
        limits['expires_at'] = datetime.fromisoformat(limits['expires_at'])
        self.limits = TransportLimits(**limits)
        if self.limits.response_bytes > MAX_FRAME or self.limits.request_bytes > MAX_FRAME:
            raise ValueError('bounded broker frames required')
        refs = CredentialEnvironmentReferences(config['auth_reference'], config['secret_reference'])
        resolved = refs.resolve(os.environ)
        # Referenced values stay in the broker process; only the source credential
        # is passed to the fixed source worker via its anonymous stdin pipe.
        self.key = os.environ[config['auth_reference']].encode()
        self.secret = os.environ[config['secret_reference']]
        del resolved
        if len(self.key) < 32 or not 16 <= len(self.secret) <= 256:
            raise ValueError('bounded broker credentials unavailable')
        self.config = config
        self.binding = digest(config)
        self.journal = AttemptJournal(Path(directory) / 'broker.db', key=self.key,
            binding=self.binding, limits=self.limits, expected_head=expected_head)
        self.armed = False
        self.phase = 'startup'
        self.nonce = secrets.token_hex(32)
        if self.journal.inspect()['pending']:
            raise JournalDenied('interrupted attempt requires external recovery')
        self._event('broker_start')

    def _event(self, event, request=None, observations=None, reason=None):
        references = {'caller': digest(self.config['caller']), 'scope': self.binding,
                      'version': digest(VERSION)}
        if request is not None:
            references['request'] = request
        if observations is not None:
            references['observations'] = observations
        if reason is not None:
            references['reason'] = digest(reason)
        self.journal.lifecycle(event, at=utc_now(), references=references)

    def status(self, state):
        return {'status': state, 'version': VERSION, 'nonce': self.nonce,
            'head': self.journal.head, 'budget': self.journal.inspect(),
            'execution_allowed': False, 'allow_live_customer_access': False}

    def stopped(self):
        return self.journal.inspect()['stopped'] or any(e['event'] in
            ('broker_stop', 'broker_revoke') for e in self.journal.lifecycle_records())

    def control(self, value):
        self.phase = 'control_authentication'
        exact(value, ('control', 'nonce', 'head', 'mac'))
        payload = {k: value[k] for k in ('control', 'nonce', 'head')}
        if (value['control'] not in ('arm', 'stop', 'revoke') or value['nonce'] != self.nonce
                or value['head'] != self.journal.head or type(value['mac']) is not str
                or not hmac.compare_digest(value['mac'], authenticate(self.key, 'control', payload))):
            raise ValueError('control authentication denied')
        if value['control'] == 'arm':
            if self.stopped() or utc_now() >= min(self.limits.expires_at, self.expires_at):
                raise ValueError('stopped or expired')
            self._event('broker_arm')
            self.armed = True
        else:
            self._event('broker_' + value['control'])
            self.journal.stop()
            self.armed = False
        self.nonce = secrets.token_hex(32)
        return self.status(value['control'])

    def read(self, value):
        self.phase = 'request_validation'
        exact(value, ('operation', 'grant_token', 'request_id', 'request'))
        if not self.armed or self.stopped():
            raise ValueError('broker not armed')
        self.phase = 'grant_authentication'
        if (value['operation'] != self.operation or type(value['grant_token']) is not str
                or not hmac.compare_digest(value['grant_token'],
                    authenticate(self.key, 'read_grant', self.binding))):
            raise ValueError('grant authentication denied')
        _text(value['request_id'])
        identity = digest((self.config['caller'], value['request_id']))
        if any(e.get('references', {}).get('request') == identity
               for e in self.journal.lifecycle_records()):
            raise ValueError('duplicate attempt denied')
        self.phase = 'scope_authorization'
        if digest(self.config) != self.binding:
            raise ValueError('broker configuration changed')
        if self.operation == 'metadata':
            return self._metadata(value, identity)
        request = request_from(value['request'])
        validate_field_classifications(self.config['field_classifications'], self.grant.window.fields)
        supervisor = self

        class ProcessReader:
            source_id = supervisor.grant.source_id

            def read(self, permit):
                permit.claim_io(self.source_id)
                supervisor.phase = 'resource_reservation'
                # Reservation is durable BEFORE worker creation or source file I/O.
                supervisor._event('broker_request', request=identity)
                supervisor.journal.begin(permit.current_time(), len(_json(value).encode()))
                received = 0
                success = False
                try:
                    bootstrap = {k: supervisor.config[k] for k in
                                 ('grant', 'protocol', 'source_digest', 'field_classifications')}
                    bootstrap.update(request=asdict(request), path=supervisor.config['source_path'],
                                     secret=supervisor.secret)
                    result = supervisor._worker(bootstrap)
                    received = len(result.stdout)
                    supervisor._validate_worker(result)
                    admitted = observations_from(decode(result.stdout))
                    permit.check(self.source_id)
                    supervisor.journal.check_active(permit.current_time())
                    # Reuse canonical admission in the supervisor as well. Source
                    # observations remain observations, never transport authority.
                    raw = tuple(replace(o, evidence=replace(o.evidence, payload={
                        'resource': o.evidence.payload['resource'],
                        'record': o.evidence.payload['record']})) for o in admitted)
                    success = True
                    supervisor.phase = 'admission'
                    return raw
                finally:
                    supervisor.journal.finish(success=success, received_bytes=received)

        observations = launch_pilot_read(request, authorization_id=self.grant.authorization_id,
            lookup=lambda _: None if self.stopped() else self.grant, adapter=ProcessReader())
        self._event('broker_admitted', request=identity,
                    observations=digest([str(o.observation_id) for o in observations]))
        response = self.status('admitted')
        response['observations'] = [_observation_to_data(o) for o in observations]
        if len(_json(response).encode()) > MAX_FRAME:
            raise ValueError('application reply oversized')
        return response

    def _worker(self, bootstrap, *, timeout=5):
        source = str(Path(__file__).resolve().parents[2])
        command = 'import sys;sys.path.insert(0,' + repr(source) + ');' + (
            'from orion.pilot.broker_worker import main;raise SystemExit(main())')
        seal = secrets.token_bytes(32)
        envelope = {'bootstrap': bootstrap,
                    'mac': authenticate(seal, 'worker_bootstrap', bootstrap)}
        sealed_input = _json(envelope).encode()
        if len(sealed_input) > MAX_FRAME:
            raise ValueError('worker bootstrap oversized')
        self.phase = 'worker_acquisition'
        read_fd, write_fd = os.pipe()
        try:
            os.write(write_fd, seal)
        finally:
            os.close(write_fd)
        try:
            result = subprocess.run([sys.executable, '-I', '-c', command,
                '--seal-fd', str(read_fd)], input=sealed_input, capture_output=True,
                timeout=timeout, check=False, env={'PATH': os.defpath, 'LANG': 'C.UTF-8'},
                close_fds=True, pass_fds=(read_fd,))
        finally:
            os.close(read_fd)
        return result

    def _validate_worker(self, result):
        if (result.returncode != 0 or result.stderr
                or len(result.stdout) > min(MAX_FRAME, self.limits.response_bytes)
                or any(_json(secret)[1:-1].encode() in result.stdout
                       for secret in (self.secret, self.key.decode()))):
            raise ValueError('local worker failed')

    def _metadata(self, value, identity):
        request = metadata_request_from(value['request'])
        supervisor = self
        deadline = time.monotonic() + 5
        requested = False

        class ProcessMetadata:
            source_id = supervisor.source_id

            def acquire(self, permit, target):
                nonlocal requested
                permit.check(self.source_id)
                if not requested:
                    supervisor._event('broker_request', request=identity)
                    requested = True
                # Each catalog/schema acquisition consumes its own durable attempt.
                attempts = [e for e in supervisor.journal.lifecycle_records() if e['event'] == 'attempt']
                if attempts:
                    wait = attempts[-1]['at'] + supervisor.limits.minimum_interval_seconds - utc_now().timestamp()
                    if wait > 0:
                        if wait >= deadline - time.monotonic():
                            raise ValueError('metadata scheduling deadline')
                        time.sleep(wait)
                if time.monotonic() >= deadline:
                    raise ValueError('metadata deadline')
                permit.claim_io(self.source_id)
                supervisor.phase = 'resource_reservation'
                supervisor.journal.begin(permit.current_time(), len(_json(value).encode()))
                received, success = 0, False
                try:
                    bootstrap = {k: supervisor.config[k] for k in
                        ('operation', 'grant', 'protocol', 'source_digest', 'field_classifications')}
                    bootstrap.update(request=asdict(request), path=supervisor.config['source_path'],
                                     secret=supervisor.secret, target=target)
                    result = supervisor._worker(bootstrap, timeout=max(0.001, deadline - time.monotonic()))
                    received = len(result.stdout)
                    supervisor._validate_worker(result)
                    response = decode(result.stdout)
                    permit.check(self.source_id)
                    supervisor.journal.check_active(permit.current_time())
                    if time.monotonic() >= deadline:
                        raise ValueError('metadata response deadline')
                    if target is None:
                        exact(response, ('catalog', 'complete'))
                        if type(response['catalog']) is not list:
                            raise ValueError('catalog list required')
                        output = tuple(response['catalog']), response['complete']
                    else:
                        exact(response, ('resource', 'declarations'))
                        if response['resource'] != target:
                            raise ValueError('schema scope mismatch')
                        output = proposal_from(target, response['declarations'])
                    success = True
                    return output
                finally:
                    supervisor.journal.finish(success=success, received_bytes=received)

            def catalog(self, permit, limit):
                return self.acquire(permit, None)

            def schema(self, permit, resource):
                return self.acquire(permit, resource)

        def lookup(_authorization_id):
            if digest(self.config) != self.binding or not self.armed or self.stopped():
                return None
            return self.grant

        discovery = launch_pilot_metadata(request, authorization_id=self.grant.authorization_id,
            lookup=lookup, adapter=ProcessMetadata())
        observations = discovery.observations
        # History's wire format uses JSON lists; canonical admission remains frozen.
        values = [_observation_to_data(replace(o, evidence=replace(o.evidence,
                  payload=json.loads(_json(o.evidence.payload))))) for o in observations]
        response = self.status('admitted')
        response['observations'] = values
        if len(_json(response).encode()) > MAX_FRAME:
            raise ValueError('application reply oversized')
        self._event('broker_admitted', request=identity,
                    observations=digest([str(o.observation_id) for o in observations]))
        response.update(self.status('admitted'))
        return response

    def handle(self, value):
        self.phase = 'request_validation'
        try:
            if type(value) is not dict:
                raise ValueError('object required')
            return self.control(value) if 'control' in value else self.read(value)
        except Exception:  # noqa: BLE001 - fixed denial, no secret/record/traceback leakage
            self._event('broker_denied', reason=self.phase)
            return self.status('denied')


def main(argv=None):
    parser = argparse.ArgumentParser(description='Local-only supervised broker; never activate a pilot.')
    parser.add_argument('--config', required=True)
    parser.add_argument('--state', required=True)
    parser.add_argument('--expected-head')
    args = parser.parse_args(argv)
    try:
        broker = Broker(decode(private_bytes(args.config)), args.state, expected_head=args.expected_head)
        print(_json(broker.status('unarmed')), flush=True)
        for _ in range(100):
            raw = sys.stdin.buffer.readline(MAX_FRAME + 1)
            if not raw:
                broker._event('broker_shutdown')
                print(_json(broker.status('shutdown')), flush=True)
                return 0
            if len(raw) > MAX_FRAME or not raw.endswith(b'\n'):
                raise ValueError('framing denied')
            try:
                message = decode(raw)
            except Exception:  # noqa: BLE001 - malformed frames have no trusted fields
                message = None
            print(_json(broker.handle(message)), flush=True)
        raise ValueError('session exhausted')
    except Exception:  # noqa: BLE001 - fail closed without configuration or exception contents
        print('{"status":"broker_blocked","execution_allowed":false}', flush=True)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
