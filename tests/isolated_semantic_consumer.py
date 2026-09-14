"""Fixed local consumer of ORION contracts. No issuer credentials or live adapter."""
import json
import os
import sys
from pathlib import Path

# Only the repository's read-only mounted source, or its test checkout, is used.
source = Path('/app/src') if Path(__file__).resolve() == Path('/work/probe.py') else (
    Path(__file__).resolve().parents[1] / 'src')
sys.path.insert(0, str(source))

from orion.pilot.broker_contract import decode, observations_from, request_from
from orion.shadow.semantic_review import review_semantic_study
from orion.understanding.role_checkpoint import _json, checkpoint_sha256
from orion.understanding.role_study import RoleStudy
from orion.understanding.semantic_checkpoint import (
    checkpoint_semantic,
    restore_semantic,
)
from orion.understanding.semantic_rules import RULES
from orion.understanding.semantic_study import Instrument, SemanticStudy


def semantic_state(scope, observations):
    schema = observations_from(scope['schema'])[0]
    request = request_from(scope['message']['request'])
    archive = {o.evidence.evidence_id: o for o in (schema, *observations)}
    archive.update({('scope', o.evidence.evidence_id): request for o in observations})
    instruments = tuple(Instrument(i['source_id'], i['resource'], i['provenance_source'],
                                   tuple(i['classes'])) for i in scope['instruments'])
    kwargs = {'tenant_id': request.tenant_id, 'company': request.company, 'source_id': request.source_id}
    if scope.get('checkpoint') is not None:
        study = restore_semantic(scope['checkpoint'], expected_sha256=scope['checkpoint_sha256'],
                                 **kwargs, instruments=instruments, rules=RULES, evidence_lookup=archive.get)
    else:
        base = RoleStudy(schema, evidence_lookup=archive.get)
        base.observe(observations, request=request)
        study = SemanticStudy(base, instruments=instruments, evidence_lookup=archive.get)
    review = review_semantic_study(study, **kwargs)
    checkpoint = checkpoint_semantic(study)
    return {'audit_id': review.audit_id, 'checkpoint': checkpoint,
            'checkpoint_sha256': checkpoint_sha256(checkpoint),
            'unknown_count': sum(c.hypothesis.status == 'unknown' for c in study.claims()),
            'validated_count': sum(c.hypothesis.status == 'validated' for c in study.claims()),
            'evidence_ids': [str(key) for key in review.evidence_ids],
            'execution_allowed': review.execution_allowed}


def custody_checks(scope):
    from isolation_lab import inspect_child, readable
    try:
        checks = inspect_child(scope['isolation'])
    except OSError:
        checks = {'host_probe_available': False}
    checks.update({label: not readable(scope[label]) for label in
                   ('source_denied', 'configuration_denied', 'metadata_source_denied', 'metadata_configuration_denied')})
    for label in ('journal_write_denied', 'metadata_journal_write_denied'):
        try:
            descriptor = os.open(scope[label], os.O_WRONLY)
            os.close(descriptor)
            checks[label] = False
        except OSError:
            checks[label] = True
    # Reproduce the legacy injected-reader route using only a synthetic file.
    from orion.contracts import utc_now
    from orion.discovery.http_adapter import ReadOnlyHttpDiscoveryAdapter
    def injected(_url):
        return Path(scope['source_denied']).read_bytes()
    adapter = ReadOnlyHttpDiscoveryAdapter(base_url='https://opaque.test', paths=('/r',), fetcher=injected)
    try:
        adapter.discover(tenant_id='t_01', observed_at=utc_now())
        checks['direct_adapter_source_denied'] = False
    except OSError:
        checks['direct_adapter_source_denied'] = True
    except Exception:  # noqa: BLE001 - reading followed by any error is not denied access
        # A parser error after reading bytes is not proof of denied file access.
        checks['direct_adapter_source_denied'] = False
    return checks


def consume(scope, exchange):
    isolation = custody_checks(scope)
    checks = {}
    message = scope['message']
    if scope.get('checkpoint') is None:
        if 'schema' in scope:
            raise ValueError('initial schema must come through broker')
        response = exchange(scope['metadata_message'])
        checks['brokered_discovery'] = response['status'] == 'admitted' and response['budget']['attempts'] == 3
        if not checks['brokered_discovery']:
            raise ValueError('metadata discovery failed')
        scope['schema'] = response['observations']
        metadata = observations_from(scope['schema'])[0].evidence.payload
        checks['metadata_not_record_authority'] = metadata['record_reads_allowed'] is False
        denied = exchange(dict(message, grant_token=scope['metadata_message']['grant_token']))
        checks['metadata_token_read_denied'] = denied['status'] == 'denied' and denied['budget']['attempts'] == 0
        wrong = json.loads(_json(message))
        wrong['request']['tenant_id'] = 'unapproved'
        denied = exchange(wrong)
        checks['scope_denied_before_attempt'] = denied['status'] == 'denied' and denied['budget']['attempts'] == 0
        wrong = dict(message, operation='write')
        denied = exchange(wrong)
        checks['write_denied_before_attempt'] = denied['status'] == 'denied' and denied['budget']['attempts'] == 0
        denied = exchange({'control': 'arm', 'nonce': 'unapproved', 'head': 'unapproved', 'mac': '0' * 64})
        checks['control_escalation_denied'] = denied['status'] == 'denied' and denied['budget']['attempts'] == 0
        response = exchange(message)
        checks['authorized_read'] = response['status'] == 'admitted' and response['budget']['attempts'] == 1
        if not checks['authorized_read']:
            raise ValueError('authorized local read failed')
        observations = observations_from(response['observations'])
        denied = exchange(message)
        checks['replay_denied'] = denied['status'] == 'denied' and denied['budget']['attempts'] == 1
    else:
        denied = exchange(dict(scope['metadata_message'], request_id='metadata_after_restart'))
        checks['metadata_revoked_restart_denied'] = denied['status'] == 'denied' and denied['budget']['attempts'] == 3
        observations = observations_from(scope['observations'])
        denied = exchange(dict(message, request_id='after_restart'))
        checks['revoked_restart_denied'] = denied['status'] == 'denied' and denied['budget']['attempts'] == 1
    state = semantic_state(scope, observations)
    checks['honest_unknown'] = state['unknown_count'] > 0 and state['validated_count'] == 0
    checks['no_execution'] = state['execution_allowed'] is False
    return {'pipeline_checks': checks, 'isolation_checks': isolation, 'state': state}


def main():
    # The owner pipe is bounded and is the only source of admitted observations.
    scope = decode(sys.stdin.buffer.readline(65537).rstrip(b'\n'))
    def exchange(message):
        print(_json(message), flush=True)
        return decode(sys.stdin.buffer.readline(65537).rstrip(b'\n'))
    print(_json({'consumer_result': consume(scope, exchange)}), flush=True)


if __name__ == '__main__':
    # A fixed sibling test helper; not an operator-selectable import path.
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    main()
