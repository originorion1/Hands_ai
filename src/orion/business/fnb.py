"""Bounded F&B assessment of independently grounded, identified samples.

The reviewed vocabulary is deliberately USD / kg / serving specific. Identifiers
are never mappings. Instruments must establish these units and process channels;
unmatched currencies, units, coverage, accounting treatment and labor stay unknown.
Calculations are sample inferences, never assertions of ledger completeness.
"""
import json
from dataclasses import asdict
from datetime import date, timedelta
from decimal import ROUND_HALF_EVEN, Context, Decimal, localcontext
from hashlib import sha256
from itertools import pairwise

from ..shadow.semantic_review import review_semantic_study
from ..understanding.semantic_rules import SemanticRule
from ..understanding.semantic_study import SemanticStudy
from .coverage import review_coverage


def _rule(role, kind, dimension, secondary):
    return SemanticRule(role, kind, (('process', role, dimension),
                                     (secondary, role, dimension)))


FNB_RULES = tuple(_rule(role, 'number', unit, 'aggregate') for role, unit in (
    ('gross_sales', 'USD'), ('served_units', 'serving'),
    ('received_quantity', 'kg'), ('receipt_cost', 'USD'),
    ('counted_stock', 'kg'), ('recipe_quantity', 'kg_per_serving'),
    ('recorded_waste', 'kg'), ('unit_cost', 'USD_per_kg'),
)) + (_rule('business_event_date', 'date', 'calendar', 'temporal'),) + tuple(
    _rule(role, 'reference', 'identity', 'relationship') for role in (
        'served_item', 'ingredient', 'prepared_item'))

VERSION = 'fnb-sample-assessment-v1'


def _json(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), allow_nan=False)


def assess_restaurant(study, *, tenant_id, company, source_id):
    """Use fixed arithmetic semantics, independent of caller decimal settings."""
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN)):
        return _assess_restaurant(study, tenant_id=tenant_id, company=company, source_id=source_id)


def _assess_restaurant(study, *, tenant_id, company, source_id):
    """Recompute from the canonical study and its external admission archive.

    Returns a JSON-compatible review artifact, not an admissible request/grant.
    Rule equality prevents substituting weaker semantic policy. Each normalized
    cell additionally needs independent witnesses for this particular record.
    """
    if (type(study) is not SemanticStudy or study.rules != FNB_RULES
            or (study.base.tenant, study.base.company, study.base.source)
            != (tenant_id, company, source_id)):
        raise ValueError('reviewed F&B study and exact scope required')
    review = review_semantic_study(study, tenant_id=tenant_id, company=company, source_id=source_id)
    origins = dict(review.evidence_origins)
    claims = review.evaluated_claims  # Revalidated archive, lineage and revisions.
    anchors = {o.evidence.evidence_id: o for o in study.evidence_snapshot()}
    records = study.base.evidence_snapshot()
    source = [study.base.schema, *(o for o, _ in records), *anchors.values()]
    evidence = {}
    for obs in source:
        ev = obs.evidence
        p = ev.payload
        evidence[str(ev.evidence_id)] = {
            'source': ev.source, 'kind': ev.kind.value, 'tenant': ev.tenant_id,
            'company': company, 'observed_at': ev.observed_at.isoformat(),
            'observation_id': str(obs.observation_id),
            'provenance': dict(p.get('provenance', {
                k: p.get(k) for k in ('authorization_id', 'scope_sha256', 'source_id')})),
        }
    unknowns = {('Complete ledger coverage, tax/refund treatment, count boundary timing, '
                'unobserved movements, recipe validity intervals and labor/attendance are not proven.')}
    normalized, rejected, seen = [], [], {}
    for obs, scope in records:
        ev, payload = obs.evidence, obs.evidence.payload
        row = payload['record']
        resource = payload['resource']
        identity = payload['provenance']['source_record_id']
        key = (resource, identity)
        serialized = _json(dict(row))
        if key in seen:
            if seen[key] != serialized:
                raise ValueError('conflicting business identity; reconstruct the study')
            continue  # A re-acquisition is not another business transaction.
        seen[key] = serialized
        cells = {}
        for claim in claims:
            if claim.resource != resource or claim.hypothesis.status != 'validated':
                continue
            witnesses = [anchors[k] for k in claim.independent if k in anchors
                and anchors[k].evidence.payload['record']['subject_id'] == identity
                and anchors[k].evidence.payload['record']['subject_resource'] == resource]
            requirements = {(a.evidence.payload['record']['evidence_class'],
                             a.evidence.payload['record']['channel'],
                             a.evidence.payload['record']['dimension']) for a in witnesses}
            # The engine's independence contract was checked globally. Recheck the
            # minimum collector domains on this subject, without extrapolating.
            domains = {root[0] for a in witnesses
                       for root in origins[a.evidence.evidence_id]}
            if not set(claim.rule.required) <= requirements or len(domains) < 2:
                continue
            value = row[claim.field]
            if claim.rule.kind == 'number' and Decimal(str(value)) < 0:
                rejected.append({'resource': resource, 'identity': identity,
                                 'reason': 'negative measure requires return/reversal semantics',
                                 'evidence_ids': [str(ev.evidence_id)]})
                continue
            targets = {a.evidence.payload['record']['related_resource'] for a in witnesses}
            refs = sorted({str(ev.evidence_id), str(study.base.schema.evidence.evidence_id),
                           *(str(k) for k in claim.hypothesis.supporting_evidence)})
            cells[claim.rule.role] = {
                'value': value, 'field': claim.field, 'status': 'VALIDATED_SEMANTIC_ROLE',
                'unit': claim.rule.required[0][2], 'evidence_ids': refs,
                'claim_id': str(claim.hypothesis.hypothesis_id),
                'support_fraction': claim.support_fraction,
                'target_resource': next(iter(targets)) if len(targets) == 1 else None,
            }
        normalized.append({'resource': resource, 'identity': identity, 'cells': cells,
                           'evidence_ids': [str(ev.evidence_id)],
                           'read_window': [scope.start.isoformat(), scope.end.isoformat()]})
    normalized.sort(key=lambda r: (r['resource'], r['identity']))
    findings = []

    def refs(rows):
        return sorted({k for r in rows for c in r['cells'].values() for k in c['evidence_ids']})

    def add(code, category, text, rows, *, value=None, unit=None, severity='information',
            uncertainty=(), next_step='Review completeness under a separate bounded read grant.'):
        ids = refs(rows)
        if not ids or not set(ids) <= set(evidence):
            raise ValueError('finding without traceable evidence')
        period = sorted({r['cells']['business_event_date']['value'] for r in rows
                         if 'business_event_date' in r['cells']})
        findings.append({'code': code, 'category': category, 'finding': text,
            'severity': severity, 'value': str(value) if value is not None else None, 'unit': unit,
            'evidence_ids': ids, 'support_fraction': min(c['support_fraction'] for r in rows
                                                      for c in r['cells'].values()),
            'confidence_meaning': 'semantic witness agreement, not calibrated probability',
            'affected_entities': sorted({r['resource'] + '/' + r['identity'] for r in rows}),
            'period': [period[0], period[-1]] if period else None,
            'uncertainty': list(uncertainty), 'recommended_investigation': next_step,
            'execution_allowed': False, 'action_authority': 'NONE'})

    def select(*roles):
        return [r for r in normalized if set(roles) <= set(r['cells'])]

    def number(r, role):
        return Decimal(str(r['cells'][role]['value']))

    def target(r, role):
        c = r['cells'][role]
        return c['target_resource'], c['value']

    sales = select('gross_sales', 'served_units', 'served_item', 'business_event_date')
    servings = select('served_units', 'served_item', 'business_event_date')
    purchases = select('received_quantity', 'ingredient', 'business_event_date')
    purchase_costs = select('receipt_cost', 'ingredient', 'business_event_date')
    stocks = select('counted_stock', 'ingredient', 'business_event_date')
    recipes = select('recipe_quantity', 'prepared_item', 'ingredient', 'business_event_date')
    wastes = select('recorded_waste', 'ingredient', 'business_event_date')
    costs = select('unit_cost', 'business_event_date')
    daily = {}
    for r in sales:
        day = r['cells']['business_event_date']['value']
        daily[day] = daily.get(day, Decimal(0)) + number(r, 'gross_sales')
    if sales:
        total = sum(daily.values())
        add('sample_gross_sales', 'INFERRED_CONCLUSION',
            'Sum of independently grounded gross sales in the admitted sample; not net revenue.',
            sales, value=total, unit='USD', uncertainty=('Ledger completeness is unknown.',))
        days = sorted(daily)
        if len(days) >= 2:
            add('sample_sales_change', 'INFERRED_CONCLUSION',
                'Last observed day minus first observed day, without filling missing days.', sales,
                value=daily[days[-1]] - daily[days[0]], unit='USD')
        if len(days) >= 3 and all((date.fromisoformat(b)-date.fromisoformat(a)).days == 1
                                 for a, b in pairwise(days)):
            add('next_day_sample_sales', 'PREDICTION',
                'Arithmetic mean baseline for the next day of comparable observed coverage.', sales,
                value=sum(daily.values()) / Decimal(len(days)), unit='USD',
                uncertainty=('Not a total-business forecast; coverage/stationarity unproven.',
                             (f'Observed range [{min(daily.values())}, {max(daily.values())}] '
                             'is descriptive, not a prediction interval.')))
            findings[-1]['prediction'] = {
                'model_version': 'sample-daily-mean-v1', 'training_days': days,
                'evidence_as_of': max(evidence[k]['observed_at'] for k in refs(sales)),
                'target_date': (date.fromisoformat(days[-1])+timedelta(days=1)).isoformat(),
                'actual_outcome': None, 'error': None,
            }
        else:
            unknowns.add('Prediction requires at least three consecutive observed business dates.')
    else:
        unknowns.add('No fully grounded sales rows; sales and forecast are UNKNOWN.')
    if purchase_costs:
        add('sample_purchase_cost', 'INFERRED_CONCLUSION',
            'Sum of received purchase costs in the admitted sample.', purchase_costs,
            value=sum(number(r, 'receipt_cost') for r in purchase_costs), unit='USD',
            uncertainty=('Ledger completeness is unknown.',))
    if purchases:
        add('sample_purchase_quantity', 'INFERRED_CONCLUSION',
            'Sum of received mass in the admitted sample.', purchases,
            value=sum(number(r, 'received_quantity') for r in purchases), unit='kg',
            uncertainty=('Ledger completeness is unknown.',))

    # Quantity signals do not depend on cost or recipe availability.
    for ingredient in sorted({target(r, 'ingredient') for r in stocks + wastes}):
        counts = sorted([r for r in stocks if target(r, 'ingredient') == ingredient],
                        key=lambda r: r['cells']['business_event_date']['value'])
        if (len(counts) == 2 and counts[0]['cells']['business_event_date']['value']
                != counts[1]['cells']['business_event_date']['value']):
            add('observed_count_change:' + '/'.join(ingredient), 'INFERRED_CONCLUSION',
                'Last admitted stock count minus first admitted stock count.', counts,
                value=number(counts[1], 'counted_stock') - number(counts[0], 'counted_stock'),
                unit='kg', uncertainty=('Counts do not establish cause of movement.',))
        discarded = [r for r in wastes if target(r, 'ingredient') == ingredient]
        if discarded:
            add('sample_recorded_waste:' + '/'.join(ingredient), 'INFERRED_CONCLUSION',
                'Sum of independently witnessed waste records in the sample.', discarded,
                value=sum(number(r, 'recorded_waste') for r in discarded), unit='kg',
                uncertainty=('Does not include unrecorded waste.',))

    # Relational joins use validated target resource AND identity, never bare IDs.
    theoretical = {}
    dependencies = {}
    for sale in servings:
        matches = [r for r in recipes if target(r, 'prepared_item') == target(sale, 'served_item')]
        # No silent selection among versions or duplicate ingredient definitions.
        ingredients = [target(r, 'ingredient') for r in matches]
        if (not matches or len(set(ingredients)) != len(ingredients)
                or any(r['cells']['business_event_date']['value'] >
                       sale['cells']['business_event_date']['value'] for r in matches)):
            unknowns.add('Missing or competing recipe definitions prevent complete consumption.')
            theoretical.clear()
            break
        for recipe in matches:
            ingredient = target(recipe, 'ingredient')
            theoretical[ingredient] = theoretical.get(ingredient, Decimal(0)) + (
                number(sale, 'served_units') * number(recipe, 'recipe_quantity'))
            dependencies.setdefault(ingredient, []).extend((sale, recipe))
    for ingredient, quantity in sorted(theoretical.items()):
        used = dependencies[ingredient]
        add('theoretical_consumption:' + '/'.join(ingredient), 'INFERRED_CONCLUSION',
            'Served quantities multiplied by observed recipe coefficients.', used,
            value=quantity, unit='kg', uncertainty=('Recipe completeness and effective period unknown.',))
        matched_costs = [r for r in costs if (r['resource'], r['identity']) == ingredient]
        if len(matched_costs) != 1:
            unknowns.add('Missing/ambiguous unit cost prevents valuation for ' + '/'.join(ingredient))
        unit_cost = number(matched_costs[0], 'unit_cost') if len(matched_costs) == 1 else None
        if unit_cost is not None:
            add('theoretical_food_cost:' + '/'.join(ingredient), 'INFERRED_CONCLUSION',
                'Sample recipe consumption valued at the observed unit cost.', used + matched_costs,
                value=quantity * unit_cost, unit='USD',
                uncertainty=('Not accounting COGS; price validity and recipe completeness unproven.',))
        counts = sorted([r for r in stocks if target(r, 'ingredient') == ingredient],
                        key=lambda r: r['cells']['business_event_date']['value'])
        if len(counts) != 2 or counts[0]['cells']['business_event_date']['value'] == counts[1]['cells']['business_event_date']['value']:
            unknowns.add('Two distinct stock count dates required for a movement scenario.')
            continue
        start, end = (r['cells']['business_event_date']['value'] for r in counts)
        receipts = [r for r in purchases if target(r, 'ingredient') == ingredient]
        discarded = [r for r in wastes if target(r, 'ingredient') == ingredient]
        all_rows = used + matched_costs + counts + receipts + discarded
        if any(not start <= r['cells']['business_event_date']['value'] <= end
               for r in all_rows):
            unknowns.add('Incompatible evidence periods prevent a stock reconciliation scenario.')
            continue
        depletion = (number(counts[0], 'counted_stock')
                     + sum(number(r, 'received_quantity') for r in receipts)
                     - number(counts[1], 'counted_stock'))
        waste = sum(number(r, 'recorded_waste') for r in discarded)
        variance = depletion - quantity - waste
        add('conditional_stock_variance:' + '/'.join(ingredient), 'HYPOTHESIS',
            'Opening + received - closing - theoretical use - recorded waste; '
            'unexplained difference under an inclusive-date, complete-movements scenario.',
            all_rows, value=variance, unit='kg', severity='review' if variance else 'information',
            uncertainty=('Missing movements, timing, count errors and recipe changes can explain this.',
                         'This does not establish waste, theft, leakage or financial loss.'),
            next_step='Request bounded count timestamps, adjustments and recipe-effective evidence.')
        if unit_cost is not None:
            findings[-1]['estimated_business_impact'] = {
                'value': str(variance * unit_cost), 'unit': 'USD', 'status': 'CONDITIONAL_SCENARIO'}
    states = [{'claim_id': str(c.hypothesis.hypothesis_id), 'resource': c.resource,
               'field': c.field, 'role': c.rule.role, 'status': c.hypothesis.status,
               'support_fraction': c.support_fraction, 'reason': c.reason,
               'supporting': list(map(str, c.hypothesis.supporting_evidence)),
               'contradicting': list(map(str, c.contradicting))} for c in claims]
    dates = sorted({r['cells']['business_event_date']['value'] for r in normalized
                    if 'business_event_date' in r['cells']})
    resource_summary = [{'resource': resource,
        'observed_identity_count': sum(r['resource'] == resource for r in normalized),
        'validated_sample_roles': sorted({role for r in normalized if r['resource'] == resource
                                          for role in r['cells']})}
        for resource in sorted({f[0] for f in study.base.facts})]
    relationships = [{'resource': r['resource'], 'identity': r['identity'], 'role': role,
                      'target_resource': cell['target_resource'], 'target_identity': cell['value'],
                      'status': cell['status'], 'evidence_ids': cell['evidence_ids']}
                     for r in normalized for role, cell in r['cells'].items()
                     if role in ('served_item', 'ingredient', 'prepared_item')]
    result = {'version': VERSION, 'tenant': tenant_id, 'company': company, 'source': source_id,
        'scope': 'IDENTIFIED_ADMITTED_SAMPLES_ONLY', 'execution_allowed': False,
        'allow_live_customer_access': False, 'action_authority': 'NONE',
        'resources': sorted({f[0] for f in study.base.facts}),
        'resource_summary': resource_summary, 'relationships': relationships,
        'business_period': [dates[0], dates[-1]] if dates else None,
        'normalized_records': normalized, 'excluded_cells': rejected,
        'semantic_claims': states, 'evidence': evidence,
        'coverage_review': review_coverage(study, normalized, review),
        'observed_facts': [{'category': 'OBSERVED_FACT', 'resource': o.evidence.payload['resource'],
                            'record': dict(o.evidence.payload['record']),
                            'evidence_id': str(o.evidence.evidence_id)} for o, _ in records],
        'audit': {'semantic_audit_id': review.audit_id,
                  'checkpoint_sha256': review.checkpoint_sha256,
                  'evaluator_version': review.evaluator_version,
                  'evidence_classes': [(str(k), v) for k, v in review.evidence_classes],
                  'collector_roots': [(str(k), list(v)) for k, v in review.evidence_origins],
                  'review_required': review.decision is not None,
                  'review_decision_id': str(review.decision.decision_id) if review.decision else None,
                  'execution_status': review.execution_status, 'execution_allowed': False},
        'findings': findings, 'unknowns': sorted(unknowns),
        'recommendations': [{'category': 'RECOMMENDATION', 'text': f['recommended_investigation'],
                             'evidence_ids': f['evidence_ids'], 'execution_allowed': False,
                             'authorization_required': 'separate_record_grant'} for f in findings],
        'contradictions': [c for c in states if c['contradicting']],
        'world_model_claim_ids': sorted(str(c.hypothesis.hypothesis_id) for c in claims),
        'rules': [asdict(r) for r in study.rules]}
    # Recheck after composition; assessment identity changes when evidence/revisions change.
    if review_semantic_study(study, tenant_id=tenant_id, company=company,
                             source_id=source_id).audit_id != review.audit_id:
        raise ValueError('study changed during assessment')
    result['assessment_id'] = sha256(_json(result).encode()).hexdigest()
    return result


def owner_report(assessment):
    """Render the canonical artifact without another calculation or model call."""
    lines = ['ORION — admitted restaurant sample assessment',
             'Read-only. No action authority. Coverage is not proven complete.']
    for f in assessment['findings']:
        lines.append(f"{f['category']}: {f['finding']} "
                     f"{f['value'] or ''} {f['unit'] or ''}\n"
                     f"Evidence: {', '.join(f['evidence_ids'])}\n"
                     f"Uncertainty: {'; '.join(f['uncertainty'])}\n"
                     f"Investigate: {f['recommended_investigation']}")
    lines.append('Coverage/timing: ' + _json(assessment['coverage_review']))
    lines.append('UNKNOWN: ' + '\n'.join(assessment['unknowns']))
    return '\n\n'.join(lines)
