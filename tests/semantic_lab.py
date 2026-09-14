"""Independent synthetic instruments; opaque business schemas, no field mapping input."""
import random
from datetime import date, timedelta
from urllib.parse import parse_qs, urlsplit

from test_erpnext_metadata_adapter import FakeResponse
from test_pilot_read import NOW

from orion.contracts import Evidence, EvidenceKind, Observation
from orion.discovery.erpnext_pilot_metadata import ERPNextPilotMetadataReader
from orion.discovery.pilot_metadata import (
    MetadataAuthorization,
    MetadataRequest,
    launch_pilot_metadata,
)
from orion.discovery.pilot_read import PilotAuthorization, PilotRequest, launch_pilot_read
from orion.discovery.read_window import ReviewedReadWindow
from orion.understanding.role_study import RoleStudy
from orion.understanding.semantic_study import (
    AGGREGATE_FIELDS,
    ANCHOR_FIELDS,
    Instrument,
    Origin,
    SemanticStudy,
)


class Organization:
    def __init__(self, variant='A', seed=17, tenant='t_01', reorder=False, note=''):
        rng = random.Random(seed)
        opaque = lambda prefix: prefix + format(rng.getrandbits(80), 'x')
        self.resources = tuple(opaque('r_') for _ in range(2))
        self.fields = tuple(opaque('f_') for _ in range(7 + (2 if variant == 'B' else 1 if variant == 'C' else 0)))
        self.other_fields = tuple(opaque('f_') for _ in range(2 if variant == 'A' else 3))
        self.identity, self.partition = opaque('f_'), opaque('f_')
        self.variant, self.tenant, self.company = variant, tenant, 'c_01'
        self.source = 'https://opaque.test'
        self.archive, self.calls = {}, []
        self.now = NOW
        self.counter = 0
        self.instruments = (
            Instrument('https://instrument-a.test', 'r_aa7', 'local-instrument-a', ('process',)),
            Instrument('https://instrument-b.test', 'r_bb9', 'local-instrument-b',
                       ('aggregate', 'temporal', 'relationship', 'organizational')),
        )
        self._reorder, self._note = reorder, note
        metadata_request = MetadataRequest(tenant, self.company, self.source)
        metadata_grant = MetadataAuthorization('m_01', metadata_request,
                                               NOW + timedelta(hours=1), True, 5, 2, ())
        adapter = ERPNextPilotMetadataReader(source_id=self.source,
            api_key='fixture', api_secret='fixture', opener=self.metadata)
        discovered = launch_pilot_metadata(metadata_request, authorization_id='m_01',
            lookup=lambda _: metadata_grant, adapter=adapter, clock=lambda: self.now)
        self.archive.update((o.evidence.evidence_id, o) for o in discovered.observations)
        self.base = RoleStudy(discovered.observations[0], evidence_lookup=self.archive.get)
        self.study = SemanticStudy(self.base, instruments=self.instruments,
                                   evidence_lookup=self.archive.get)
        self.discovered = discovered
        # These technical grant bindings belong to the independent fixture control plane.
        # The semantic engine receives no binding of business fields to expected roles.
        self.values = []
        for i in range(2):
            a, b = (11 + i * 7, 3 + i * 2) if variant == 'A' else (37 + i * 13, 8 + i * 3)
            self.values.append((a, b, '2024-06-01', '2024-06-03', '2024-06-05',
                                f'q_{i}', f'q_{1-i}',
                                *((19 + i, '2024-06-07') if variant == 'B' else
                                  ('z_01',) if variant == 'C' else ())))

    def metadata(self, req, timeout):
        self.calls.append('metadata')
        parsed = urlsplit(req.full_url)
        if parsed.path == '/api/resource/DocType':
            names = sorted(self.resources)
            return FakeResponse({'data': [{'name': r} for r in names]}, url=req.full_url)
        resource = parse_qs(parsed.query)['doctype'][0]
        fields = self.fields if resource == self.resources[0] else self.other_fields
        kinds = (*('Float', 'Float', 'Date', 'Date', 'Date', 'Link', 'Link'),
                 *(('Float', 'Date') if self.variant == 'B' else ('Link',)
                   if self.variant == 'C' else ())) if resource == self.resources[0] else (
            ('Date', 'Float') if self.variant == 'A' else ('Date', 'Float', 'Link'))
        declarations = [{'fieldname': f, 'fieldtype': k, 'description': self._note}
                        for f, k in zip(fields, kinds, strict=True)]
        if self._reorder:
            declarations.reverse()
        return FakeResponse({'message': {'docs': [{'name': resource, 'fields': declarations}]}},
                            url=req.full_url)

    def admit(self, source, resource, fields, when, rows, *, provenance='local-records',
              identity=None, partition=None, lookup=None):
        identity, partition = identity or self.identity, partition or self.partition
        scope = PilotRequest(self.tenant, self.company, source, resource, fields, when,
                             date(2024, 6, 1), date(2024, 6, 7), len(rows))
        grant = PilotAuthorization('g_' + provenance, source, ReviewedReadWindow(
            self.tenant, self.company, resource, fields, when, scope.start, scope.end,
            NOW + timedelta(hours=1)), identity, partition, provenance, EvidenceKind.EXPERIMENT,
            len(rows))
        parent = self

        class LocalReader:
            source_id = source

            def read(self, permit):
                permit.check(source)
                permit.claim_io(source)
                parent.calls.append('records')
                return tuple(Observation(Evidence(EvidenceKind.EXPERIMENT, provenance,
                    {'resource': resource, 'record': row}, observed_at=parent.now,
                    tenant_id=parent.tenant)) for row in rows)

        observations = launch_pilot_read(scope, authorization_id=grant.authorization_id,
            lookup=lookup if lookup is not None else lambda _: grant,
            adapter=LocalReader(), clock=lambda: self.now)
        for obs in observations:
            key = obs.evidence.evidence_id
            self.archive[key], self.archive[('scope', key)] = obs, scope
        return observations, scope

    def records(self):
        for n, resource in enumerate(self.resources):
            fields = self.fields if n == 0 else self.other_fields
            values = self.values if n == 0 else [
                ('2024-06-01', 11 + i, *(() if self.variant == 'A' else ('z_01',)))
                for i in range(2)]
            rows = [dict(zip((*fields, self.identity, self.partition),
                    (*v, f's_{i}' if n == 0 else f'q_{i}', self.company), strict=True))
                    for i, v in enumerate(values)]
            obs, req = self.admit(self.source, resource, (*fields, self.identity, self.partition),
                                 fields[2] if n == 0 else fields[0], rows)
            self.base.observe(obs, request=req)

    def anchors(self, role, *, override=None, replaces=(), origin_alias=None, observe=True):
        # Independent instruments observe process events and reconciliation, not business
        # schema fields. Values below originate in the synthetic process, not field lookup.
        specifications = {
            'monetary': ('settled_transfer', 'reconciled_transfer', 'currency', 'aggregate',
                         [11, 18] if self.variant == 'A' else [37, 50]),
            'completion': ('terminal_transition', 'terminal_transition', 'calendar', 'temporal',
                           ['2024-06-01'] * 2),
            'recording': ('ingestion_transition', 'ingestion_transition', 'calendar', 'temporal',
                          ['2024-06-03'] * 2),
            'recipient': ('outbound_participation', 'outbound_participation', 'identity',
                          'relationship', ['q_0', 'q_1']),
            'originator': ('inbound_participation', 'inbound_participation', 'identity',
                           'relationship', ['q_1', 'q_0']),
        }
        first, second, dimension, secondary, expected = specifications[role]
        # A separate collector's independently recorded reconciliation/components and
        # lifecycle log. It never reads the primary schema, rows or expected vector.
        ledger = [(4, 7), (8, 10)] if self.variant == 'A' else [(20, 17), (21, 29)]
        secondary_log = {
            'monetary': [sum(parts) for parts in ledger],
            'completion': ['2024-06-01', '2024-06-01'],
            'recording': ['2024-06-03', '2024-06-03'],
            'recipient': ['q_0', 'q_1'], 'originator': ['q_1', 'q_0'],
        }
        if override is not None:
            expected = override
            secondary_log[role] = override
            if role == 'monetary':
                ledger = [(value / 2, value / 2) for value in override]
        batches, all_obs = [], []
        for n, instrument in enumerate(self.instruments):
            rows = []
            for i in range(2):
                self.counter += 1
                old = replaces[n * 2 + i] if replaces else None
                rows.append(dict(zip(ANCHOR_FIELDS, (
                    f'a_{self.counter}', self.company, '2024-06-06', self.source,
                    self.resources[0], f's_{i}', 'process' if n == 0 else secondary,
                    first if n == 0 else second, dimension, expected[i] if n == 0 else secondary_log[role][i],
                    self.resources[1] if dimension == 'identity' else '',
                    old.evidence.payload['record']['id'] if old else ''), strict=True)))
            fields = ANCHOR_FIELDS
            if n == 1 and secondary == 'aggregate':
                fields += AGGREGATE_FIELDS
                for i, row in enumerate(rows):
                    row.update(zip(AGGREGATE_FIELDS, ledger[i], strict=True))
            obs, _ = self.admit(instrument.source_id, instrument.resource, fields,
                'on', rows, provenance=instrument.provenance_source, identity='id', partition='partition')
            for i, o in enumerate(obs):
                old = replaces[n * 2 + i] if replaces else None
                root = (f'collector_{n}', f'{role}_{i}')
                if old:
                    root = self.archive[('origin', old.evidence.evidence_id)].roots[0]
                if origin_alias is not None:
                    root = origin_alias
                self.archive[('origin', o.evidence.evidence_id)] = Origin((root,))
            batches.append(obs)
            all_obs.extend(obs)
        if observe:
            for batch in batches:
                self.study.observe(batch)
        return tuple(all_obs)

    def populate(self):
        self.records()
        if self.variant != 'C':
            for role in ('monetary', 'completion', 'recording', 'recipient', 'originator'):
                self.anchors(role)
        return self

    def results(self):
        return {(c.field, c.rule.role): c for c in self.study.claims()}

    def sensitivity(self):
        return {**{(i.source_id, i.resource, f): 'public' for i in self.instruments for f in ANCHOR_FIELDS + AGGREGATE_FIELDS},
                **{(self.source, r, f): 'public' for r, f, *_ in self.base.facts}}

    def plan(self, **kw):
        return self.study.next_observation(start=date(2024, 6, 1), end=date(2024, 6, 7),
            sensitivity=self.sensitivity(), **kw)

    def correct(self, role, previous, values):
        self.now += timedelta(seconds=1)
        return self.anchors(role, override=values, replaces=previous)


def normalized(lab):
    # Compare by fixture structural position only in the test oracle, never the engine.
    names = {f: (r, i) for r, fields in enumerate((lab.fields, lab.other_fields))
             for i, f in enumerate(fields)}
    return sorted((names[c.field], c.rule.role, c.hypothesis.status) for c in lab.study.claims())
