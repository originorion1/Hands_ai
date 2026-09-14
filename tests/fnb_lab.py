"""Explicitly synthetic offline F&B laboratory; never customer configuration.

The source encoding and independently collected witness logs are separate inputs.
Only the test oracle knows their correspondence. ORION receives admitted schemas,
records and instrument observations, not that correspondence.
Run: PYTHONPATH=src:tests python -m fnb_lab
"""
import json
import random
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

from semantic_lab import Organization
from test_erpnext_metadata_adapter import FakeResponse
from test_pilot_read import NOW

from orion.business.fnb import FNB_RULES, assess_restaurant, owner_report
from orion.discovery.erpnext_pilot_metadata import ERPNextPilotMetadataReader
from orion.discovery.pilot_metadata import (
    MetadataAuthorization,
    MetadataRequest,
    launch_pilot_metadata,
)
from orion.understanding.role_study import RoleStudy
from orion.understanding.semantic_study import (
    AGGREGATE_FIELDS,
    ANCHOR_FIELDS,
    Instrument,
    Origin,
    SemanticStudy,
)


class Restaurant(Organization):
    def __init__(self, seed=41, *, tenant='t_synthetic', reorder=False, note=''):
        rng = random.Random(seed)
        opaque = lambda prefix: prefix + format(rng.getrandbits(64), 'x')
        self.resources = tuple(opaque('r_') for _ in range(6))
        self.kinds = (
            ('Float', 'Float', 'Date', 'Date', 'Date', 'Link'),
            ('Float', 'Float', 'Date', 'Date', 'Date', 'Link'),
            ('Float', 'Date', 'Date', 'Date', 'Link'),
            ('Float', 'Date', 'Date', 'Date', 'Link', 'Link'),
            ('Float', 'Date', 'Date', 'Date', 'Link'),
            ('Float', 'Date', 'Date', 'Date'))
        self.columns = tuple(tuple(opaque('f_') for _ in kinds) for kinds in self.kinds)
        self.identity, self.partition = opaque('f_'), opaque('f_')
        self.tenant, self.company, self.source = tenant, 'c_synthetic', 'https://restaurant.test'
        self.archive, self.calls, self.counter = {}, [], 0
        self.now, self.journal_factory = NOW, None
        self._reorder, self._note = reorder, note
        self.instruments = (
            Instrument('https://process.test', 'r_a1', 'synthetic-process', ('process',)),
            Instrument('https://witness.test', 'r_b2', 'synthetic-witness',
                       ('aggregate', 'temporal', 'relationship')))
        request = MetadataRequest(tenant, self.company, self.source)
        grant = MetadataAuthorization('m_fnb', request, NOW + timedelta(hours=1), True, 9, 6, ())
        discovered = launch_pilot_metadata(request, authorization_id='m_fnb',
            lookup=lambda _: grant, adapter=ERPNextPilotMetadataReader(source_id=self.source,
                api_key='fixture', api_secret='fixture', opener=self.metadata), clock=lambda: self.now)
        self.archive.update((o.evidence.evidence_id, o) for o in discovered.observations)
        self.base = RoleStudy(discovered.observations[0], evidence_lookup=self.archive.get)
        self.study = SemanticStudy(self.base, instruments=self.instruments,
                                  evidence_lookup=self.archive.get, rules=FNB_RULES)
        self.discovered = discovered
        # Source rows are an opaque encoding of the synthetic operational ledger.
        self.values = (
            ((100, 10, '2024-06-01', '2024-06-04', '2024-06-05', 'p_0'),
             (144, 12, '2024-06-02', '2024-06-04', '2024-06-05', 'p_1'),
             (121, 11, '2024-06-03', '2024-06-04', '2024-06-05', 'p_0')),
            ((20, 5, '2024-06-01', '2024-06-04', '2024-06-05', 'p_2'),
             (32, 8, '2024-06-02', '2024-06-04', '2024-06-05', 'p_2')),
            ((100, '2024-06-01', '2024-06-04', '2024-06-05', 'p_2'),
             (75, '2024-06-03', '2024-06-04', '2024-06-05', 'p_2')),
            ((0.2, '2024-06-01', '2024-06-04', '2024-06-05', 'p_0', 'p_2'),
             (0.3, '2024-06-01', '2024-06-04', '2024-06-05', 'p_1', 'p_2')),
            ((1, '2024-06-02', '2024-06-04', '2024-06-05', 'p_2'),
             (2, '2024-06-03', '2024-06-04', '2024-06-05', 'p_2')),
            ((7, '2024-06-01', '2024-06-04', '2024-06-05'),
             (9, '2024-06-01', '2024-06-04', '2024-06-05'),
             (4, '2024-06-01', '2024-06-04', '2024-06-05')))
        # Independent process witness values: no reading self.values or field lookup.
        self.process = (
            {'gross_sales': (100, 144, 121), 'served_units': (10, 12, 11),
             'business_event_date': ('2024-06-01', '2024-06-02', '2024-06-03'),
             'served_item': ('p_0', 'p_1', 'p_0')},
            {'receipt_cost': (20, 32), 'received_quantity': (5, 8),
             'business_event_date': ('2024-06-01', '2024-06-02'), 'ingredient': ('p_2', 'p_2')},
            {'counted_stock': (100, 75), 'business_event_date': ('2024-06-01', '2024-06-03'),
             'ingredient': ('p_2', 'p_2')},
            {'recipe_quantity': (0.2, 0.3), 'business_event_date': ('2024-06-01', '2024-06-01'),
             'prepared_item': ('p_0', 'p_1'), 'ingredient': ('p_2', 'p_2')},
            {'recorded_waste': (1, 2), 'business_event_date': ('2024-06-02', '2024-06-03'),
             'ingredient': ('p_2', 'p_2')},
            {'unit_cost': (7, 9, 4), 'business_event_date': ('2024-06-01',) * 3})
        # A second synthetic collector's independently supplied reconciliation components.
        self.components = (
            {'gross_sales': ((40, 60), (70, 74), (60, 61)),
             'served_units': ((4, 6), (5, 7), (5, 6))},
            {'receipt_cost': ((9, 11), (15, 17)), 'received_quantity': ((2, 3), (3, 5))},
            {'counted_stock': ((40, 60), (35, 40))},
            {'recipe_quantity': ((0.1, 0.1), (0.125, 0.175))},
            {'recorded_waste': ((0.5, 0.5), (1, 1))},
            {'unit_cost': ((3, 4), (4, 5), (2, 2))})
        self.witness = (
            {'business_event_date': ('2024-06-01', '2024-06-02', '2024-06-03'),
             'served_item': ('p_0', 'p_1', 'p_0')},
            {'business_event_date': ('2024-06-01', '2024-06-02'), 'ingredient': ('p_2', 'p_2')},
            {'business_event_date': ('2024-06-01', '2024-06-03'), 'ingredient': ('p_2', 'p_2')},
            {'business_event_date': ('2024-06-01', '2024-06-01'),
             'prepared_item': ('p_0', 'p_1'), 'ingredient': ('p_2', 'p_2')},
            {'business_event_date': ('2024-06-02', '2024-06-03'), 'ingredient': ('p_2', 'p_2')},
            {'business_event_date': ('2024-06-01',) * 3})

    def metadata(self, req, timeout):
        self.calls.append('metadata')
        parsed = urlsplit(req.full_url)
        if parsed.path == '/api/resource/DocType':
            return FakeResponse({'data': [{'name': r} for r in sorted(self.resources)]},
                                url=req.full_url)
        resource = parse_qs(parsed.query)['doctype'][0]
        index = self.resources.index(resource)
        declarations = [{'fieldname': f, 'fieldtype': k, 'description': self._note}
                        for f, k in zip(self.columns[index], self.kinds[index], strict=True)]
        return FakeResponse({'message': {'docs': [{'name': resource, 'fields':
            list(reversed(declarations)) if self._reorder else declarations}]}}, url=req.full_url)

    def records(self):
        for n, resource in enumerate(self.resources):
            rows = [dict(zip((*self.columns[n], self.identity, self.partition),
                (*v, f'p_{i}' if n == 5 else f'x_{i}', self.company), strict=True))
                for i, v in enumerate(self.values[n])]
            when = self.columns[n][self.kinds[n].index('Date')]
            observations, scope = self.admit(self.source, resource,
                (*self.columns[n], self.identity, self.partition), when, rows)
            self.base.observe(observations, request=scope)

    def ground(self, *, shared=False, omit=(), units=None):
        self.anchor_batches = []
        for n, process in enumerate(self.process):
            for role, values in process.items():
                if (n, role) in omit:
                    continue
                rule = next(r for r in FNB_RULES if r.role == role)
                for j, instrument in enumerate(self.instruments):
                    rows = []
                    for i, expected in enumerate(values):
                        self.counter += 1
                        parts = self.components[n][role][i] if j and rule.kind == 'number' else None
                        value = (sum(parts) if parts else self.witness[n][role][i]) if j else expected
                        row = dict(zip(ANCHOR_FIELDS, (f'a_{self.counter}', self.company,
                            '2024-06-06', self.source, self.resources[n],
                            f'p_{i}' if n == 5 else f'x_{i}', *rule.required[j], value,
                            self.resources[5] if rule.kind == 'reference' else '', ''), strict=True))
                        if units and role in units:
                            row['dimension'] = units[role]
                        if parts:
                            row.update(zip(AGGREGATE_FIELDS, parts, strict=True))
                        rows.append(row)
                    fields = ANCHOR_FIELDS + (AGGREGATE_FIELDS if j and rule.kind == 'number' else ())
                    observations, _ = self.admit(instrument.source_id, instrument.resource,
                        fields, 'on', rows, provenance=instrument.provenance_source,
                        identity='id', partition='partition')
                    for i, obs in enumerate(observations):
                        self.archive[('origin', obs.evidence.evidence_id)] = Origin((
                            (f'collector_{0 if shared else j}', f'{n}_{role}_{i}'),))
                    self.study.observe(observations)
                    self.anchor_batches.append(observations)
        return self

    def populate(self, **kw):
        self.records()
        return self.ground(**kw)

    def assessment(self):
        return assess_restaurant(self.study, tenant_id=self.tenant,
                                 company=self.company, source_id=self.source)


if __name__ == '__main__':
    result = Restaurant().populate().assessment()
    print(json.dumps(result, sort_keys=True, indent=2))
    print(owner_report(result))
