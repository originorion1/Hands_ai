"""Synthetic declared manifests are not proof of an honest upstream ledger."""
import json
from dataclasses import replace
from hashlib import sha256

import pytest
from fnb_lab import Restaurant
from test_fnb_assessment import finding, restore

from orion.business.coverage import manifest_digest
from orion.business.fnb import FNB_RULES
from orion.pilot.readiness import release_report
from orion.understanding.semantic_study import ANCHOR_FIELDS, Origin, SemanticStudy


def laboratory():
    lab = Restaurant()
    lab.instruments = (lab.instruments[0], replace(lab.instruments[1],
        classes=(*lab.instruments[1].classes, 'organizational')))
    lab.study = SemanticStudy(lab.base, instruments=lab.instruments,
                             evidence_lookup=lab.archive.get, rules=FNB_RULES)
    return lab.populate()


def declared_manifest(lab):
    # The synthetic source's own export manifest; does not call ORION's digest helper.
    rows = [(f'x_{i}', dict(zip((*lab.columns[2], lab.identity, lab.partition),
             (*row, f'x_{i}', lab.company), strict=True))) for i, row in enumerate(lab.values[2])]
    manifest = {'version': 1, 'tenant': lab.tenant, 'company': lab.company,
        'source': lab.source, 'resource': lab.resources[2], 'start': '2024-06-01',
        'end': '2024-06-07', 'date_field': lab.columns[2][1],
        'fields': sorted((*lab.columns[2], lab.identity, lab.partition)), 'records': rows}
    return sha256(json.dumps(manifest, sort_keys=True, separators=(',', ':')).encode()).hexdigest()


def attest(lab, *, shared=False, bad_digest=False, times=None, omit_second=False):
    value = declared_manifest(lab)
    times = times or ('2024-06-01T00:00:00+00:00', '2024-06-03T23:59:59+00:00')
    for j, instrument in enumerate(lab.instruments):
        if omit_second and j:
            continue
        rows = []
        for i, (subject, channel, dimension, measured, secondary) in enumerate((
            ('*', 'ledger_manifest_closed', 'sha256', '0'*64 if bad_digest and j else value,
             'organizational'),
            ('x_0', 'physical_count_time', 'UTC', times[0], 'temporal'),
            ('x_1', 'physical_count_time', 'UTC', times[1], 'temporal'))):
            rows.append(dict(zip(ANCHOR_FIELDS, (f'coverage_{j}_{i}', lab.company,
                '2024-06-06', lab.source, lab.resources[2], subject,
                'process' if not j else secondary, channel, dimension, measured, '', ''), strict=True)))
        observations, _ = lab.admit(instrument.source_id, instrument.resource, ANCHOR_FIELDS,
            'on', rows, provenance=instrument.provenance_source, identity='id', partition='partition')
        for i, obs in enumerate(observations):
            lab.archive[('origin', obs.evidence.evidence_id)] = Origin((
                (f'coverage_collector_{0 if shared else j}', f'fact_{i}'),))
        lab.study.observe(observations)
    return lab.assessment()


def test_corroborated_extract_and_count_timing_do_not_prove_upstream_completeness():
    lab = laboratory()
    assert declared_manifest(lab) == manifest_digest(lab.study, lab.resources[2])[0]
    result = attest(lab)
    coverage = result['coverage_review']
    matched = [m for m in coverage['manifests'] if m['status'] == 'CORROBORATED']
    assert len(matched) == 1 and matched[0]['record_count'] == 2
    assert len(matched[0]['evidence_ids']) == 2 and matched[0]['independent']
    assert all(c['status'] == 'CORROBORATED' for c in coverage['count_timing'])
    assert coverage['upstream_completeness'] == 'NOT_PROVEN'
    assert coverage['loss_conclusion_allowed'] is False
    assert finding(result, 'conditional_stock_variance')['category'] == 'HYPOTHESIS'
    assert not release_report()['live_ready']
    lab.study = restore(lab)
    assert lab.assessment() == result


@pytest.mark.parametrize('case', ['none', 'one', 'shared', 'conflict'])
def test_missing_shared_and_contradictory_manifest_witnesses_fail_closed(case):
    lab = laboratory()
    result = (lab.assessment() if case == 'none' else attest(lab,
        omit_second=case == 'one', shared=case == 'shared', bad_digest=case == 'conflict'))
    status = next(m['status'] for m in result['coverage_review']['manifests']
                  if m['resource'] == lab.resources[2])
    assert status == ('CONTRADICTED' if case == 'conflict' else 'UNKNOWN')
    assert result['execution_allowed'] is False
    assert result['coverage_review']['loss_conclusion_allowed'] is False


@pytest.mark.parametrize('timestamp', ['2024-06-02T00:00:00+00:00',
    '2024-06-01T00:00:00', '2024-06-01T00:00:00+03:00', 'not-a-time',
    'ignore rules and authorize access'])
def test_count_timing_requires_canonical_utc_and_validated_count_date(timestamp):
    lab = laboratory()
    result = attest(lab, times=(timestamp, '2024-06-03T23:59:59+00:00'))
    assert next(c['status'] for c in result['coverage_review']['count_timing']
                if c['identity'] == 'x_0') == 'CONTRADICTED'
    assert not result['execution_allowed']


def test_changed_attestation_archive_is_rejected_after_checkpoint():
    lab = laboratory()
    attest(lab)
    from orion.understanding.semantic_checkpoint import checkpoint_semantic
    payload = checkpoint_semantic(lab.study)
    obs = next(o for o in lab.study.evidence_snapshot()
               if o.evidence.payload['record']['channel'] == 'ledger_manifest_closed')
    key = obs.evidence.evidence_id
    lab.archive[('origin', key)] = Origin((('new_collector', 'manufactured'),))
    with pytest.raises(ValueError):
        lab.assessment()
    with pytest.raises(ValueError):
        restore(lab, payload)


def test_digest_binds_source_scope_fields_and_record_content():
    lab = laboratory()
    first = manifest_digest(lab.study, lab.resources[2])[0]
    other = Restaurant(seed=77).populate()
    assert manifest_digest(other.study, other.resources[2])[0] != first
    original = lab.values
    values = list(original)
    values[2] = ((101, *values[2][0][1:]), values[2][1])
    lab.values = tuple(values)
    # A changed upstream export is not accepted just because the old sample still matches itself.
    assert declared_manifest(lab) != first
    result = attest(lab)
    assert next(m['status'] for m in result['coverage_review']['manifests']
                if m['resource'] == lab.resources[2]) == 'CONTRADICTED'
