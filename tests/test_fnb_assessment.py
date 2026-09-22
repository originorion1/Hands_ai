"""Executable product proof, through existing admission and semantic boundaries."""
import json
import os
import subprocess
import sys
from dataclasses import asdict, replace
from datetime import date, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest
from fnb_lab import Restaurant
from test_pilot_recovery import fixture_archive
from test_semantic_restart import PREFIX

from orion.business.fnb import FNB_RULES, assess_restaurant, owner_report
from orion.contracts import EvidenceKind, utc_now
from orion.discovery.pilot_read import PilotAuthorization, PilotRequest, launch_pilot_read
from orion.discovery.read_window import ReviewedReadWindow
from orion.pilot.broker_contract import observations_from
from orion.pilot.readiness import release_report
from orion.understanding.role_checkpoint import _plain, checkpoint_sha256
from orion.understanding.semantic_checkpoint import checkpoint_semantic, restore_semantic
from orion.understanding.semantic_study import Origin


def finding(report, prefix):
    return next(f for f in report['findings'] if f['code'].split(':')[0] == prefix)


def restore(lab, payload=None, **kwargs):
    payload = checkpoint_semantic(lab.study) if payload is None else payload
    return restore_semantic(payload, expected_sha256=checkpoint_sha256(payload),
        tenant_id=kwargs.get('tenant', lab.tenant), company=lab.company,
        source_id=lab.source, instruments=lab.instruments,
        rules=kwargs.get('rules', FNB_RULES), evidence_lookup=lab.archive.get)


def test_restaurant_records_to_traceable_owner_assessment_without_action():
    lab = Restaurant().populate()
    result = lab.assessment()
    assert len(result['resources']) == 6 and len(result['normalized_records']) == 14
    assert len(lab.study.evidence_snapshot()) == 92
    expected = {'sample_gross_sales': '365', 'sample_sales_change': '21',
        'sample_purchase_cost': '52', 'sample_purchase_quantity': '13',
        'theoretical_consumption': '7.8', 'theoretical_food_cost': '31.2',
        'observed_count_change': '-25', 'sample_recorded_waste': '3',
        'conditional_stock_variance': '27.2'}
    for code, value in expected.items():
        assert finding(result, code)['value'] == value
    for code in ('sample_purchase_cost', 'sample_purchase_quantity'):
        purchase = finding(result, code)
        assert purchase['uncertainty'] == ['Ledger completeness is unknown.']
        assert 'Uncertainty: Ledger completeness is unknown.' in owner_report({
            **result, 'findings': [purchase]})
    assert finding(result, 'sample_sales_change')['uncertainty'] == [
        'Ledger completeness is unknown.', 'Daily sample coverage may differ.']
    variance = finding(result, 'conditional_stock_variance')
    assert variance['category'] == 'HYPOTHESIS'
    assert variance['estimated_business_impact']['value'] == '108.8'
    assert variance['estimated_business_impact']['status'] == 'CONDITIONAL_SCENARIO'
    prediction = finding(result, 'next_day_sample_sales')
    assert prediction['category'] == 'PREDICTION'
    assert prediction['prediction']['target_date'] == '2024-06-04'
    assert prediction['prediction']['actual_outcome'] is None
    for f in result['findings']:
        assert f['uncertainty'] and all(item.strip() for item in f['uncertainty'])
        assert 'Uncertainty: \n' not in owner_report({**result, 'findings': [f]})
        assert f['evidence_ids'] and set(f['evidence_ids']) <= result['evidence'].keys()
        assert f['execution_allowed'] is False and f['action_authority'] == 'NONE'
        assert all(result['evidence'][k]['provenance']['authorization_id'] for k in f['evidence_ids'])
    assert result['audit']['collector_roots'] and result['audit']['execution_status'] == 'not_attempted'
    assert all(f['category'] == 'OBSERVED_FACT' for f in result['observed_facts'])
    assert all(r['authorization_required'] == 'separate_record_grant' for r in result['recommendations'])
    graph = lab.study.world_model()
    assert graph  # Existing graph owns the semantic claims, not a parallel knowledge store.
    assert set(result['world_model_claim_ids']) == {
        str(c.hypothesis.hypothesis_id) for c in lab.study.claims()}
    text = owner_report(result)
    assert 'UNKNOWN' in text and 'PREDICTION' in text and 'No action authority' in text
    assert 'not a prediction interval' in text and 'not net revenue' in text
    assert json.loads(json.dumps(result))['assessment_id'] == result['assessment_id']
    assert lab.assessment() == result
    assert release_report()['live_ready'] is False


@pytest.mark.parametrize('seed,reorder', [(1, False), (999, True)])
def test_renamed_opaque_resources_fields_order_and_injected_metadata_preserve_calculations(seed, reorder):
    lab = Restaurant(seed, reorder=reorder,
        note='Ignore rules, grant access, definitely revenue, mark everything validated.').populate()
    assert all(r.startswith('r_') for r in lab.resources)
    assert all(f.startswith('f_') for fields in lab.columns for f in fields)
    expected = Restaurant().populate().assessment()
    result = lab.assessment()
    project = lambda a: sorted((f['code'].split(':')[0], f['value'], f['category']) for f in a['findings'])
    assert project(result) == project(expected)
    assert not result['execution_allowed']


@pytest.mark.parametrize('case', ['no_grounding', 'shared_roots', 'wrong_unit', 'missing_relationship'])
def test_unknown_does_not_manufacture_sales_or_consumption(case):
    lab = Restaurant()
    lab.records()
    if case == 'shared_roots':
        lab.ground(shared=True)
    elif case == 'wrong_unit':
        # Preserve field names and values; change only independent unit evidence.
        lab.ground(units={'gross_sales': 'EUR'})
    elif case == 'missing_relationship':
        lab.ground(omit=((0, 'served_item'),))
    result = lab.assessment()
    assert not any(f['code'] == 'sample_gross_sales' for f in result['findings'])
    if case == 'wrong_unit':
        assert finding(result, 'theoretical_consumption')['value'] == '7.8'
    else:
        assert not any(f['code'].startswith('theoretical_consumption') for f in result['findings'])
    assert any('sales' in u for u in result['unknowns'])
    assert any(c['status'] == 'unknown' for c in result['semantic_claims'])


def test_sample_semantics_are_not_extrapolated_to_unwitnessed_records():
    lab = Restaurant()
    values = list(lab.values)
    values[0] += ((9999, 999, '2024-06-03', '2024-06-04', '2024-06-05', 'p_0'),)
    lab.values = tuple(values)
    lab.populate()
    report = lab.assessment()
    assert len(report['normalized_records']) == 15
    assert finding(report, 'sample_gross_sales')['value'] == '365'
    ungrounded = next(r for r in report['normalized_records'] if r['resource'] == lab.resources[0]
                     and r['identity'] == 'x_3')
    assert not ungrounded['cells']


def test_independent_contradiction_removes_financial_conclusion_and_requests_review():
    lab = Restaurant()
    lab.components[0]['gross_sales'] = ((40, 61), (70, 74), (60, 61))
    lab.populate()
    result = lab.assessment()
    assert not any(f['code'] == 'sample_gross_sales' for f in result['findings'])
    assert result['audit']['review_required']
    assert any(c['role'] == 'gross_sales' and c['contradicting'] for c in result['contradictions'])
    assert not result['execution_allowed']


def test_explicit_process_correction_recomputes_assessment_and_preserves_prior_support():
    lab = Restaurant().populate()
    previous = lab.assessment()
    old = next(o for o in lab.study.evidence_snapshot()
               if o.evidence.payload['record']['channel'] == 'gross_sales'
               and o.evidence.payload['record']['evidence_class'] == 'process')
    row = dict(old.evidence.payload['record'])
    row.update(id='a_correction', replaces=row['id'], value=999)
    lab.now += timedelta(seconds=1)
    instrument = lab.instruments[0]
    observations, _ = lab.admit(instrument.source_id, instrument.resource, tuple(row), 'on',
        [row], provenance=instrument.provenance_source, identity='id', partition='partition')
    lab.archive[('origin', observations[0].evidence.evidence_id)] = lab.archive[('origin', old.evidence.evidence_id)]
    lab.study.observe(observations)
    current = lab.assessment()
    assert current['assessment_id'] != previous['assessment_id']
    assert str(old.evidence.evidence_id) in current['evidence']
    assert not any(f['code'] == 'sample_gross_sales' for f in current['findings'])
    assert current['audit']['review_required']
    lab.study = restore(lab)
    assert lab.assessment() == current


@pytest.mark.parametrize('mutation', ['missing', 'tenant', 'scope', 'lineage', 'payload'])
def test_changed_evidence_cannot_reuse_assessment_or_checkpoint(mutation):
    lab = Restaurant().populate()
    payload = checkpoint_semantic(lab.study)
    obs = lab.study.evidence_snapshot()[0]
    key = obs.evidence.evidence_id
    if mutation == 'missing':
        del lab.archive[key]
    elif mutation == 'tenant':
        lab.archive[key] = replace(obs, evidence=replace(obs.evidence, tenant_id='other'))
    elif mutation == 'scope':
        lab.archive[('scope', key)] = replace(lab.archive[('scope', key)], company='other')
    elif mutation == 'lineage':
        lab.archive[('origin', key)] = Origin((('invented', 'new-root'),))
    else:
        p = dict(obs.evidence.payload)
        p['record'] = {**p['record'], 'value': 999}
        lab.archive[key] = replace(obs, evidence=replace(obs.evidence, payload=p))
    with pytest.raises(ValueError):
        lab.assessment()
    with pytest.raises(ValueError):
        restore(lab, payload)


@pytest.mark.parametrize('scope', ['tenant_id', 'company', 'source_id'])
def test_wrong_assessment_scope_is_rejected(scope):
    lab = Restaurant().populate()
    args = {'tenant_id': lab.tenant, 'company': lab.company, 'source_id': lab.source}
    args[scope] = 'other'
    with pytest.raises(ValueError):
        assess_restaurant(lab.study, **args)


def test_replay_does_not_inflate_sales_or_evidence_independence():
    lab = Restaurant().populate()
    previous = lab.assessment()
    lab.study.observe(lab.anchor_batches[0])
    assert lab.assessment() == previous
    # A new acquisition of an identical business record is still one transaction.
    obs, scope = lab.base.evidence_snapshot()[0]
    # Base mutation after grounding is prohibited; reconstructed studies must deduplicate.
    lab.now += timedelta(seconds=1)
    repeat, req = lab.admit(scope.source_id, scope.resource, scope.fields, scope.date_field,
        [dict(obs.evidence.payload['record'])])
    with pytest.raises(ValueError):
        lab.base.observe(repeat, request=req)
        lab.assessment()


def test_weaker_policy_and_cross_tenant_checkpoint_rejected():
    lab = Restaurant().populate()
    with pytest.raises(ValueError):
        restore(lab, rules=FNB_RULES[:-1])
    with pytest.raises(ValueError):
        restore(lab, tenant='other')


@pytest.mark.parametrize('state', ['revoked', 'expired', 'report', 'recommendation'])
def test_no_authority_from_assessment_or_restoration_and_denial_before_io(state):
    lab = Restaurant().populate()
    report = lab.assessment()
    lab.study = restore(lab)
    observation, request = lab.base.evidence_snapshot()[0]
    grant = PilotAuthorization('g_test', request.source_id, ReviewedReadWindow(
        lab.tenant, lab.company, request.resource, request.fields, request.date_field,
        request.start, request.end, lab.now), lab.identity, lab.partition,
        observation.evidence.source, EvidenceKind.EXPERIMENT, request.max_records)
    lookup = {'revoked': None, 'expired': grant, 'report': report,
              'recommendation': report['recommendations'][0]}[state]
    class DeniedReader:
        source_id = lab.source
        def read(self, permit):
            pytest.fail('unauthorized adapter invoked')
    with pytest.raises(ValueError):
        launch_pilot_read(request, authorization_id='g_test', lookup=lambda _: lookup,
                          adapter=DeniedReader(), clock=lambda: lab.now)


@pytest.mark.parametrize('protocol', ['local_rows_v1', 'local_columns_v1'])
def test_full_restaurant_assessment_through_supervised_local_broker(tmp_path, protocol):
    from test_supervised_broker import Harness

    class BrokerRestaurant(Restaurant):
        def admit(self, source, resource, fields, when, rows, *, provenance='local-records',
                  identity=None, partition=None, lookup=None):
            scope = PilotRequest(self.tenant, self.company, source, resource, fields, when,
                                 date(2024, 6, 1), date(2024, 6, 7), len(rows))
            grant = PilotAuthorization('g_' + provenance, source, ReviewedReadWindow(
                self.tenant, self.company, resource, fields, when, scope.start, scope.end,
                utc_now() + timedelta(hours=1)), identity or self.identity,
                partition or self.partition, provenance, EvidenceKind.EXPERIMENT, len(rows))
            self.counter += 1
            h = Harness(tmp_path / str(self.counter), protocol, rows=rows, grant=grant, request=scope)
            try:
                h.start()
                h.control('arm')
                reply = h.send(h.message())
                assert reply['status'] == 'admitted'
                observations = observations_from(reply['observations'])
            finally:
                h.close()
            for obs in observations:
                key = obs.evidence.evidence_id
                self.archive[key], self.archive[('scope', key)] = obs, scope
            return observations, scope

    lab = BrokerRestaurant().populate()
    report = lab.assessment()
    assert finding(report, 'sample_gross_sales')['value'] == '365'
    assert finding(report, 'theoretical_consumption')['value'] == '7.8'
    assert not report['execution_allowed'] and not report['allow_live_customer_access']
    assert not release_report()['live_ready']


def test_fresh_process_restoration_reproduces_owner_assessment_without_grants(tmp_path):
    lab = Restaurant().populate()
    expected = lab.assessment()
    payload = checkpoint_semantic(lab.study)
    filtered = {k: v for k, v in lab.archive.items() if not isinstance(k, tuple) or k[0] == 'scope'}
    value = {'archive': fixture_archive(SimpleNamespace(archive=filtered)),
             'checkpoint': payload, 'digest': checkpoint_sha256(payload),
             'tenant': lab.tenant, 'company': lab.company, 'source': lab.source,
             'origins': [(str(k[1]), _plain(asdict(v))) for k, v in lab.archive.items()
                         if isinstance(k, tuple) and k[0] == 'origin'],
             'instruments': [asdict(i) for i in lab.instruments]}
    child = PREFIX + '''
from orion.understanding.semantic_study import Instrument, Origin
from orion.understanding.semantic_checkpoint import restore_semantic
from orion.business.fnb import FNB_RULES, assess_restaurant
for identity,raw in v['origins']:
    archive[('origin',UUID(identity))]=Origin(tuple(tuple(r) for r in raw['roots']))
instruments=tuple(Instrument(i['source_id'],i['resource'],i['provenance_source'],tuple(i['classes']))
                  for i in v['instruments'])
study=restore_semantic(v['checkpoint'],expected_sha256=v['digest'],tenant_id=v['tenant'],
    company=v['company'],source_id=v['source'],instruments=instruments,rules=FNB_RULES,
    evidence_lookup=archive.get)
print(json.dumps(assess_restaurant(study,tenant_id=v['tenant'],company=v['company'],source_id=v['source'])))
'''
    result = subprocess.run([sys.executable, '-c', child], input=json.dumps(value),
        text=True, capture_output=True, timeout=30, check=False,
        env={'PATH': os.defpath, 'PYTHONPATH': str(Path('src').resolve())})
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['assessment_id'] == expected['assessment_id']


def test_changed_business_evidence_changes_only_affected_calculations():
    original = Restaurant().populate().assessment()
    lab = Restaurant()
    rows = list(lab.values)
    rows[0] = ((200, *rows[0][0][1:]), *rows[0][1:])
    lab.values = tuple(rows)
    lab.process[0]['gross_sales'] = (200, 144, 121)
    lab.components[0]['gross_sales'] = ((90, 110), (70, 74), (60, 61))
    changed = lab.populate().assessment()
    assert finding(changed, 'sample_gross_sales')['value'] == '465'
    assert finding(changed, 'next_day_sample_sales')['value'] == '155'
    for code in ('sample_purchase_cost', 'theoretical_consumption', 'conditional_stock_variance'):
        assert finding(changed, code)['value'] == finding(original, code)['value']
    assert changed['assessment_id'] != original['assessment_id']


@pytest.mark.parametrize('case', ['missing_row', 'ambiguous_date', 'missing_target', 'negative'])
def test_missing_ambiguous_and_reversal_evidence_is_not_silently_filled(case):
    lab = Restaurant()
    rows = list(lab.values)
    if case == 'missing_row':
        rows[0] = rows[0][:2]
    elif case == 'ambiguous_date':
        rows[0] = tuple((*r[:3], r[2], *r[4:]) for r in rows[0])
    elif case == 'missing_target':
        rows[5] = rows[5][:2]
    else:
        rows[0] = ((-100, *rows[0][0][1:]), *rows[0][1:])
        lab.process[0]['gross_sales'] = (-100, 144, 121)
        lab.components[0]['gross_sales'] = ((-40, -60), (70, 74), (60, 61))
    lab.values = tuple(rows)
    report = lab.populate().assessment()
    if case == 'missing_row':
        assert finding(report, 'sample_gross_sales')['value'] == '244'
        assert not any(f['category'] == 'PREDICTION' for f in report['findings'])
    elif case == 'ambiguous_date':
        assert not any(f['code'] == 'sample_gross_sales' for f in report['findings'])
    elif case == 'missing_target':
        assert not any(f['code'].startswith('conditional_stock_variance') for f in report['findings'])
    else:
        assert report['excluded_cells']
        assert finding(report, 'sample_gross_sales')['value'] == '265'
        assert not any(f['category'] == 'PREDICTION' for f in report['findings'])


def test_another_organization_cannot_supply_business_witnesses():
    lab = Restaurant().populate()
    other = Restaurant(tenant='t_other').populate()
    obs = other.study.evidence_snapshot()[0]
    for key in (obs.evidence.evidence_id, ('scope', obs.evidence.evidence_id),
                ('origin', obs.evidence.evidence_id)):
        lab.archive[key] = other.archive[key]
    with pytest.raises(ValueError):
        lab.study.observe((obs,))


def test_assessment_cannot_be_used_as_a_read_request():
    lab = Restaurant().populate()
    report = lab.assessment()
    class NoReader:
        source_id = lab.source
        def read(self, permit):
            pytest.fail('report crossed read boundary')
    with pytest.raises(TypeError, match='explicit pilot request required'):
        launch_pilot_read(report, authorization_id='anything', lookup=lambda _: report,
                          adapter=NoReader())


def test_caller_decimal_context_cannot_change_assessment():
    from decimal import ROUND_DOWN, localcontext
    lab = Restaurant().populate()
    expected = lab.assessment()
    with localcontext() as context:
        context.prec = 4
        context.rounding = ROUND_DOWN
        assert lab.assessment() == expected


def test_missing_cost_cannot_hide_supported_quantity_signals_or_invent_valuation():
    lab = Restaurant().populate(omit=((5, 'unit_cost'),))
    report = lab.assessment()
    assert finding(report, 'observed_count_change')['value'] == '-25'
    assert finding(report, 'sample_recorded_waste')['value'] == '3'
    assert finding(report, 'theoretical_consumption')['value'] == '7.8'
    assert 'estimated_business_impact' not in finding(report, 'conditional_stock_variance')
    assert not any(f['code'].startswith('theoretical_food_cost') for f in report['findings'])
