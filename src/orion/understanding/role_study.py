"""Scoped observable-role experiments, never a business ontology or grant issuer.

Trusted evidence lookup must contain only launcher-admitted observations. Injected
Python code is trusted; this is not a sandbox. Validation means a predicate held
in the identified sample, not that its business interpretation is established.
"""
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
from itertools import combinations
from types import MappingProxyType
from uuid import NAMESPACE_URL, UUID, uuid5

from ..contracts import EvidenceKind, Observation, ObservationMode
from ..discovery.pilot_metadata import ScopeProposal
from ..discovery.pilot_read import PilotRequest
from .graph import GraphNode, GraphStatus, GraphStore, NodeType
from .hypotheses import Hypothesis
from .schema_evidence import FieldDeclaration, interpret_schema


@dataclass(frozen=True, slots=True)
class RoleClaim:
    hypothesis: Hypothesis
    resource: str
    fields: tuple[str, ...]
    predicate: str
    supporting: tuple[UUID, ...] = ()
    contradicting: tuple[UUID, ...] = ()
    confidence: float = 0.0
    unknowns: tuple[str, ...] = ('business_meaning_unidentified', 'sample_evidence_missing')


@dataclass(frozen=True, slots=True)
class ObservationRequirement:
    tenant_id: str
    company: str
    source_id: str
    resource: str
    fields: tuple[str, ...]
    start: date
    end: date
    max_records: int
    hypotheses: tuple[UUID, ...]
    reason: str
    cost: int
    authorization_required: str = 'separate_record_grant'
    execution_allowed: bool = False
    blockers: tuple[str, ...] = ('review_identity_company_and_date_filter_bindings',)


class RoleStudy:
    """One tenant/source schema snapshot and an append-only, bounded evidence set."""

    __slots__ = (
        '_claims',
        '_company',
        '_facts',
        '_lookup',
        '_rows',
        '_schema',
        '_scopes',
        '_source',
        '_tenant',
    )

    @property
    def schema(self):
        return self._schema

    @property
    def tenant(self):
        return self._tenant

    @property
    def company(self):
        return self._company

    @property
    def source(self):
        return self._source

    @property
    def facts(self):
        return self._facts

    def __init__(self, schema: Observation, *, evidence_lookup):
        self._lookup = evidence_lookup
        self._verify(schema)
        ev = schema.evidence
        p = ev.payload
        if (ev.kind is not EvidenceKind.METADATA or ev.source != 'pilot-metadata-discovery'
                or not ev.tenant_id or p.get('record_reads_allowed') is not False
                or not p.get('authorization_id') or not p.get('scope_sha256')):
            raise ValueError('admitted schema provenance required')
        self._schema = schema
        self._tenant = ev.tenant_id
        self._company = p['company_context']
        self._source = p['source_id']
        self._claims = []
        self._rows = {}
        self._scopes = {}
        self._facts = []
        for proposal in p['proposals']:
            raw = proposal['interpretation']
            if raw is None:
                continue
            declarations = tuple(FieldDeclaration(**dict(c['declaration']))
                                 for c in raw['candidates'])
            interpretation = interpret_schema(proposal['resource'], declarations)
            ScopeProposal(proposal['resource'], (), (), interpretation)
            # Do not trust a caller's separately supplied interpretation object.
            dates = []
            for candidate in interpretation.candidates:
                d = candidate.declaration
                self._facts.append((d.resource, d.name, d.kind, ev.evidence_id))
                if d.kind == 'date':
                    dates.append(d.name)
                elif d.kind == 'number':
                    self._add(d.resource, (d.name,), 'nonnegative')
                    self._add(d.resource, (d.name,), 'nonpositive')
                elif d.kind == 'reference':
                    self._add(d.resource, (d.name,), 'repeated_reference')
                    self._add(d.resource, (d.name,), 'distinct_reference')
            for left, right in combinations(dates, 2):
                self._add(interpretation.resource, (left, right), 'precedes')
                self._add(interpretation.resource, (right, left), 'precedes')
        self._facts = tuple(self._facts)
        self._claims = tuple(self._claims)

    def _verify(self, observation):
        if (type(observation) is not Observation
                or observation.mode is not ObservationMode.READ_ONLY
                or self._lookup(observation.evidence.evidence_id) != observation):
            raise ValueError('observation not in trusted admission archive')

    def _add(self, resource, fields, predicate):
        if len(self._claims) >= 128:
            raise ValueError('hypothesis budget exhausted')
        identity = repr((self.tenant, self.source, self.schema.evidence.evidence_id,
                         resource, fields, predicate))
        hypothesis = Hypothesis(uuid5(NAMESPACE_URL, 'orion:role:v1:' + identity),
            self.tenant, f'{fields!r}: {predicate} in the observed sample of {resource}',
            (self.schema.evidence.evidence_id,))
        self._claims.append(RoleClaim(hypothesis, resource, fields, predicate))

    def observe(self, observations: tuple[Observation, ...], *, request: PilotRequest):
        """Consume already admitted data; never collect records or issue grants.

        All batch checks precede mutation. Archive membership is the authenticity
        boundary, not caller-provided UUIDs or request construction.
        """
        request.__post_init__()
        self._verify(self.schema)
        if (request.tenant_id, request.company, request.source_id) != (
                self.tenant, self.company, self.source):
            raise ValueError('study scope mismatch')
        if request.resource not in {fact[0] for fact in self.facts}:
            raise ValueError('resource not discovered')
        if type(observations) is not tuple or len(observations) > min(request.max_records, 25):
            raise ValueError('bounded immutable sample required')
        proposed = dict(self._rows)
        scopes = dict(self._scopes)
        for observation in observations:
            self._verify(observation)
            ev = observation.evidence
            p = ev.payload
            provenance = p.get('provenance', {})
            record = p.get('record')
            if (ev.kind is EvidenceKind.METADATA or ev.tenant_id != self.tenant
                    or p.get('resource') != request.resource
                    or provenance.get('source_id') != self.source
                    or not provenance.get('authorization_id') or not provenance.get('scope_sha256')
                    or not isinstance(record, Mapping) or set(record) != set(request.fields)):
                raise ValueError('record provenance or scope mismatch')
            # The admitted row's company binding was checked by the launcher.
            # Require its archived acquisition scope as well, supplied by trusted lookup.
            archived_request = self._lookup(('scope', ev.evidence_id))
            if archived_request != request:
                raise ValueError('trusted acquisition scope required')
            if not request.start <= date.fromisoformat(record[request.date_field]) <= request.end:
                raise ValueError('sample outside reviewed window')
            proposed[ev.evidence_id] = observation
            scopes[ev.evidence_id] = request
        if len(proposed) > 100:
            raise ValueError('study evidence budget exhausted')
        self._rows, self._scopes = proposed, scopes

    def claims(self):
        self._verify(self.schema)
        for observation in self._rows.values():
            self._verify(observation)
        result = []
        for claim in self._claims:
            rows = [(key, obs.evidence.payload['record']) for key, obs in self._rows.items()
                    if obs.evidence.payload['resource'] == claim.resource
                    and set(claim.fields) <= set(obs.evidence.payload['record'])]
            support, against = [], []
            identities = {key: self._rows[key].evidence.payload['provenance']['source_record_id']
                          for key, _ in rows}
            for key, row in rows:
                value = row[claim.fields[0]]
                outcome = None
                if claim.predicate in {'nonnegative', 'nonpositive'}:
                    if type(value) in (int, float):
                        outcome = value >= 0 if claim.predicate == 'nonnegative' else value <= 0
                elif claim.predicate == 'precedes':
                    try:
                        a, b = (date.fromisoformat(row[field]) for field in claim.fields)
                        outcome = None if a == b else a < b
                    except (ValueError, TypeError):
                        pass
                elif isinstance(value, str) and value and len(rows) >= 2:
                    repeated = len({identities[k] for k, r in rows
                                    if r[claim.fields[0]] == value}) > 1
                    outcome = repeated if claim.predicate == 'repeated_reference' else not repeated
                if outcome is True:
                    support.append(key)
                elif outcome is False:
                    against.append(key)
            # Counterexamples defeat the universal sample predicate, regardless of score.
            distinct_support = len({identities[key] for key in support})
            status = 'invalidated' if against else 'validated' if distinct_support >= 2 else 'unknown'
            confidence = len(support) / len(rows) if rows else 0.0
            unknowns = ('business_meaning_unidentified', 'generalization_unproven')
            if status == 'unknown':
                unknowns += ('insufficient_discriminating_evidence',)
            if support and against:
                unknowns += ('contradiction_requires_investigation',)
            hypothesis = Hypothesis(claim.hypothesis.hypothesis_id, self.tenant,
                claim.hypothesis.statement, (self.schema.evidence.evidence_id, *sorted(support)), status)
            result.append(RoleClaim(hypothesis, claim.resource, claim.fields, claim.predicate,
                tuple(sorted(support)), tuple(sorted(against)), confidence, unknowns))
        return tuple(result)

    def next_observation(self, *, start: date, end: date, sensitivity: Mapping,
                         budget: int = 8, max_records: int = 2):
        """Proposal only. Sensitivity must be classified; absent classifications deny.

        Score = unresolved alternatives / field-cell cost. Contradictions double
        priority. This is an explicit heuristic, not measured information gain.
        """
        if (type(start) is not date or type(end) is not date or start > end
                or (end - start).days > 31 or type(budget) is not int or budget < 1
                or type(max_records) is not int or not 2 <= max_records <= 25):
            raise ValueError('explicit bounded observation policy required')
        groups = {}
        for claim in self.claims():
            key = (claim.resource, tuple(sorted(claim.fields)))
            groups.setdefault(key, []).append(claim)
        choices = []
        for (resource, fields), claims in groups.items():
            if any(sensitivity.get((resource, field)) != 'public' for field in fields):
                continue
            unresolved = [c for c in claims if c.hypothesis.status == 'unknown'
                          or (c.supporting and c.contradicting)]
            if not unresolved:
                continue
            # Do not repeatedly request a scope already observed in this window.
            if any(s.resource == resource and set(fields) <= set(s.fields)
                   and s.start <= start and end <= s.end for s in self._scopes.values()):
                continue
            cost = len(fields) * max_records
            if cost > budget:
                continue
            value = sum(2 if c.supporting and c.contradicting else 1 for c in unresolved)
            choices.append((-value / cost, resource, fields, claims, cost))
        if not choices:
            return None
        _, resource, fields, claims, cost = min(choices, key=lambda c: c[:3])
        return ObservationRequirement(self.tenant, self.company, self.source, resource,
            fields, start, end, max_records, tuple(c.hypothesis.hypothesis_id for c in claims),
            'Distinguish competing sample predicates; business interpretation remains unknown.', cost)

    def world_model(self):
        """Return a fresh current snapshot: no stale validated nodes after revision."""
        graph = GraphStore()
        for claim in self.claims():
            if claim.hypothesis.status not in {'validated', 'invalidated'}:
                continue
            provenance = (self.schema.evidence.evidence_id, *claim.supporting, *claim.contradicting)
            graph.add_node(GraphNode(NodeType.KNOWLEDGE, self.tenant,
                claim.hypothesis.statement,
                MappingProxyType({'resource': claim.resource, 'fields': claim.fields,
                                  'sample_only': True, 'business_meaning': 'unknown'}),
                GraphStatus.VALIDATED if claim.hypothesis.status == 'validated'
                else GraphStatus.CONTRADICTED, claim.confidence, provenance,
                node_id=claim.hypothesis.hypothesis_id))
        return graph
