"""Evidence-grounded semantic comparison; no collection or authorization API.

The admission archive and reviewed instrument registry are trusted dependencies.
Independent origin identities are supplied by that registry, never by model text.
This is an offline epistemic boundary, not hostile-Python process isolation.
"""
from dataclasses import asdict, dataclass, replace
from datetime import date
from hashlib import sha256
from math import isfinite
from types import MappingProxyType
from uuid import NAMESPACE_URL, UUID, uuid5

from ..contracts import EvidenceKind, Observation, ObservationMode
from ..discovery.pilot_read import PilotRequest
from .graph import GraphNode, GraphRelationship, GraphStatus, GraphStore, NodeType, RelationshipType
from .hypotheses import Hypothesis
from .role_checkpoint import _json, _observation_digest, _scope_digest, checkpoint_study
from .role_study import ObservationRequirement, RoleStudy
from .semantic_rules import RULES, SemanticRule

SEMANTIC_EVALUATOR_VERSION = "semantic-rules-v1"

# Protocol mechanics, not identifiers in the unfamiliar business schema.
ANCHOR_FIELDS = ('id', 'partition', 'on', 'subject_source', 'subject_resource',
                 'subject_id', 'evidence_class', 'channel', 'dimension', 'value',
                 'related_resource', 'replaces')
AGGREGATE_FIELDS = ('component_a', 'component_b')


@dataclass(frozen=True, slots=True)
class Instrument:
    source_id: str
    resource: str
    provenance_source: str
    classes: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class Origin:
    """Reviewed archive lineage. Copies share roots even across URLs/versions.

    Roots are (collector independence domain, original fact identity), not UUIDs
    assigned to transformed observations. The original collector must establish
    these identities; asserting fresh strings in untrusted data is insufficient.
    """
    roots: tuple[tuple[str, str], ...]
    parents: tuple[UUID, ...] = ()


@dataclass(frozen=True, slots=True)
class SemanticClaim:
    hypothesis: Hypothesis
    resource: str
    field: str
    rule: SemanticRule
    supporting: tuple[UUID, ...]
    contradicting: tuple[UUID, ...]
    independent: tuple[UUID, ...]
    support_fraction: float
    missing: tuple[tuple[str, str, str], ...]
    alternatives: tuple[UUID, ...]
    reason: str


@dataclass(frozen=True, slots=True)
class SemanticRevision:
    revision_id: str
    previous: str | None
    evidence_ids: tuple[UUID, ...]
    claims: tuple[SemanticClaim, ...]


class SemanticStudy:
    __slots__ = (
        '_base',
        '_base_digest',
        '_batches',
        '_fingerprints',
        '_history',
        '_instruments',
        '_lookup',
        '_observations',
        '_rules',
    )

    @property
    def base(self):
        return self._base

    @property
    def instruments(self):
        return self._instruments

    @property
    def rules(self):
        return self._rules

    def __init__(self, base, *, instruments, evidence_lookup, rules=RULES):
        if type(base) is not RoleStudy or type(instruments) is not tuple:
            raise ValueError('structural study and reviewed instruments required')
        if not instruments or len(instruments) > 8 or type(rules) is not tuple or not rules:
            raise ValueError('bounded explicit policy required')
        for item in instruments:
            if (type(item) is not Instrument or item.source_id == base.source
                    or not item.resource or not item.provenance_source
                    or type(item.classes) is not tuple or not item.classes
                    or not set(item.classes) <= {'process', 'temporal', 'relationship',
                                                 'aggregate', 'organizational'}):
                raise ValueError('independent instrument contract required')
        if len({i.source_id for i in instruments}) != len(instruments):
            raise ValueError('ambiguous instrument source')
        for rule in rules:
            if type(rule) is not SemanticRule:
                raise ValueError('reviewed rule required')
            rule.__post_init__()
        if len({r.role for r in rules}) != len(rules) or len(rules) > 16:
            raise ValueError('duplicate or excessive rules')
        if sum(sum(r.kind == f[2] for r in rules) for f in base.facts) > 128:
            raise ValueError('semantic hypothesis budget exceeded')
        self._base, self._instruments, self._rules = base, instruments, rules
        self._lookup = evidence_lookup
        self._observations = {}
        self._fingerprints = {}
        self._history = ()
        self._batches = ()
        self._base_digest = None

    def _origin(self, identity, trail=()):
        if identity in trail or len(trail) > 8:
            raise ValueError('cyclic or excessive evidence lineage')
        origin = self._lookup(('origin', identity))
        if (type(origin) is not Origin or type(origin.roots) is not tuple
                or not 1 <= len(origin.roots) <= 8
                or type(origin.parents) is not tuple or len(origin.parents) > 8
                or len(set(origin.roots)) != len(origin.roots)):
            raise ValueError('reviewed origin lineage required')
        for root in origin.roots:
            if (type(root) is not tuple or len(root) != 2
                    or any(type(v) is not str or not v or len(v) > 128 for v in root)):
                raise ValueError('invalid origin identity')
        if origin.parents:
            inherited = set()
            for parent in origin.parents:
                obs = self._lookup(parent)
                if (type(parent) is not UUID or type(obs) is not Observation
                        or obs.evidence.evidence_id != parent):
                    raise ValueError('original parent evidence missing')
                # Parents must themselves have admitted, same-scope instrument provenance.
                self._verify(obs, lineage=False)
                inherited.update(self._origin(parent, (*trail, identity)).roots)
            if set(origin.roots) != inherited:
                raise ValueError('derived evidence cannot invent independent origins')
        return origin

    def _verify(self, observation, *, lineage=True):
        if type(observation) is not Observation or observation.mode is not ObservationMode.READ_ONLY:
            raise ValueError('read-only admitted evidence required')
        ev = observation.evidence
        req = self._lookup(('scope', ev.evidence_id))
        if (self._lookup(ev.evidence_id) != observation or type(req) is not PilotRequest
                or (req.tenant_id, req.company) != (self.base.tenant, self.base.company)):
            raise ValueError('archive or tenant/company mismatch')
        req.__post_init__()
        instrument = next((i for i in self.instruments if i.source_id == req.source_id), None)
        p = ev.payload
        provenance = p.get('provenance', {})
        if (instrument is None or ev.kind is not EvidenceKind.EXPERIMENT
                or ev.tenant_id != self.base.tenant or ev.source != instrument.provenance_source
                or req.resource != instrument.resource or p.get('resource') != req.resource
                or provenance.get('source_id') != req.source_id
                or not provenance.get('authorization_id') or not provenance.get('scope_sha256')
                or not set(ANCHOR_FIELDS) <= set(req.fields)
                or not set(req.fields) <= set(ANCHOR_FIELDS + AGGREGATE_FIELDS)
                or req.date_field != 'on'):
            raise ValueError('independent evidence provenance or scope mismatch')
        row = p.get('record', {})
        if (set(row) != set(req.fields) or row['partition'] != self.base.company
                or row['id'] != provenance.get('source_record_id')
                or row['subject_source'] != self.base.source
                or row['subject_resource'] not in {f[0] for f in self.base.facts}
                or row['evidence_class'] not in instrument.classes
                or not req.start <= date.fromisoformat(row['on']) <= req.end):
            raise ValueError('anchor subject or bounded row mismatch')
        if row['evidence_class'] == 'aggregate':
            if not set(AGGREGATE_FIELDS) <= set(row):
                raise ValueError('aggregate components missing')
            numbers = (row['value'], row['component_a'], row['component_b'])
            if (any(type(v) not in (int, float) or not isfinite(v) for v in numbers)
                    or row['value'] != row['component_a'] + row['component_b']):
                raise ValueError('aggregate evidence does not reconcile')
        elif set(row) != set(ANCHOR_FIELDS):
            raise ValueError('unnecessary aggregate fields')
        for key in set(ANCHOR_FIELDS) - {'value', 'related_resource', 'replaces'}:
            if type(row[key]) is not str or not row[key] or len(row[key]) > 200:
                raise ValueError('invalid anchor protocol value')
        if row['related_resource'] and row['related_resource'] not in {f[0] for f in self.base.facts}:
            raise ValueError('relationship target not discovered')
        if type(row['replaces']) is not str or type(row['related_resource']) is not str:
            raise ValueError('invalid revision or relationship target')
        origin = self._origin(ev.evidence_id) if lineage else None
        return req, row, origin

    def _fingerprint(self, obs):
        req, _, origin = self._verify(obs)
        def ancestry(identity, trail=()):
            if identity in trail or len(trail) > 8:
                raise ValueError('invalid ancestry')
            parent = self._lookup(identity)
            scope, _, lineage = self._verify(parent)
            return (_observation_digest(parent), _scope_digest(scope), asdict(lineage),
                    tuple(ancestry(k, (*trail, identity)) for k in lineage.parents))
        return (_observation_digest(obs), _scope_digest(req), _json(asdict(origin)),
                _json(tuple(ancestry(k) for k in origin.parents)))

    def evidence_snapshot(self):
        self.base.evidence_snapshot()
        if self._base_digest and checkpoint_study(self.base) != self._base_digest:
            raise ValueError('base sample changed; start an explicitly new study scope')
        for identity, obs in self._observations.items():
            if self._fingerprint(obs) != self._fingerprints[identity]:
                raise ValueError('semantic evidence, scope or lineage changed')
        return tuple(self._observations[k] for k in sorted(self._observations))

    def observe(self, observations):
        if type(observations) is not tuple or not 1 <= len(observations) <= 25:
            raise ValueError('bounded semantic evidence batch required')
        self.evidence_snapshot()
        proposed = dict(self._observations)
        fingerprints = dict(self._fingerprints)
        for obs in observations:
            fingerprint = self._fingerprint(obs)
            identity = obs.evidence.evidence_id
            if identity in fingerprints and fingerprint != fingerprints[identity]:
                raise ValueError('evidence identity reused with changed content')
            proposed[identity], fingerprints[identity] = obs, fingerprint
        if len(proposed) > 100:
            raise ValueError('semantic evidence budget exceeded')
        # Evaluate prior to mutation; invalid revision chains reject the complete batch.
        claims = self._evaluate(tuple(proposed.values()))
        new_ids = tuple(sorted(set(proposed) - set(self._observations)))
        if not new_ids:
            return
        previous = self._history[-1].revision_id if self._history else None
        revision_id = sha256(_json((previous, sorted(
            (str(k), v) for k, v in fingerprints.items()))).encode()).hexdigest()
        self._base_digest = checkpoint_study(self.base)
        self._observations, self._fingerprints = proposed, fingerprints
        self._batches += (new_ids,)
        self._history += (SemanticRevision(revision_id, previous,
                           tuple(sorted(proposed)), claims),)

    def _active(self, observations):
        entries = [(obs, *self._verify(obs)[1:]) for obs in observations]
        by_key = {}
        for obs, row, origin in entries:
            key = (obs.evidence.payload['provenance']['source_id'], row['id'])
            if key in by_key:
                old = by_key[key]
                if dict(old[1]) != dict(row) or old[2] != origin:
                    raise ValueError('source fact changed without explicit revision')
            by_key[key] = (obs, row, origin)
        superseded = set()
        for key, (obs, row, origin) in by_key.items():
            if not row['replaces']:
                continue
            old_key = (key[0], row['replaces'])
            if old_key == key or old_key not in by_key or old_key in superseded:
                raise ValueError('missing, cyclic or branched revision')
            previous, old, old_origin = by_key[old_key]
            if (any(row[k] != old[k] for k in ('subject_resource', 'subject_id',
                    'evidence_class', 'channel', 'dimension'))
                    or origin.roots != old_origin.roots
                    or obs.evidence.observed_at <= previous.evidence.observed_at):
                raise ValueError('revision must preserve origin and advance acquisition time')
            superseded.add(old_key)
        return tuple(value for key, value in sorted(by_key.items()) if key not in superseded)

    def _evaluate(self, observations):
        records = self.base.evidence_snapshot()
        active = self._active(observations)
        results = []
        for resource, field, kind, schema_id in self.base.facts:
            for rule in (r for r in self.rules if r.kind == kind):
                supporting, contradicting, independent = set(), set(), set()
                satisfied = {requirement: set() for requirement in rule.required}
                domains, targets = set(), set()
                ambiguous = False
                used_roots = set()
                for obs, row, origin in active:
                    requirement = (row['evidence_class'], row['channel'], row['dimension'])
                    if requirement not in satisfied or row['subject_resource'] != resource:
                        continue
                    matches = [o for o, _ in records if
                        o.evidence.payload['resource'] == resource and
                        o.evidence.payload['provenance']['source_record_id'] == row['subject_id']
                        and field in o.evidence.payload['record']]
                    if not matches:
                        continue
                    values = {_json(o.evidence.payload['record'][field]) for o in matches}
                    if len(values) != 1:
                        ambiguous = True
                        continue
                    value = matches[0].evidence.payload['record'][field]
                    expected = row['value']
                    if kind == 'number' and any(type(v) not in (int, float)
                                                or not isfinite(v) for v in (value, expected)):
                        continue
                    if kind in ('date', 'reference') and any(type(v) is not str or not v
                                                             for v in (value, expected)):
                        continue
                    if kind == 'date':
                        try:
                            if any(date.fromisoformat(v).isoformat() != v for v in (value, expected)):
                                continue
                        except ValueError:
                            continue
                    if kind == 'reference':
                        target_matches = [o for o, _ in records if
                            o.evidence.payload['resource'] == row['related_resource'] and
                            o.evidence.payload['provenance']['source_record_id'] == row['value']]
                        if not target_matches:
                            continue
                        targets.add(row['related_resource'])
                    else:
                        target_matches = []
                    ids = {obs.evidence.evidence_id, *(o.evidence.evidence_id for o in matches),
                           *(o.evidence.evidence_id for o in target_matches)}
                    if value != row['value']:
                        contradicting.update(ids)
                        continue
                    supporting.update(ids)
                    # A transformed copy cannot satisfy another required class. Shared
                    # roots count once; deterministic ordering never implies independence.
                    if used_roots.intersection(origin.roots):
                        continue
                    used_roots.update(origin.roots)
                    independent.add(obs.evidence.evidence_id)
                    domains.update(root[0] for root in origin.roots)
                    satisfied[requirement].add(row['subject_id'])
                missing = tuple(k for k, subjects in satisfied.items()
                                if len(subjects) < rule.minimum_subjects)
                status = 'invalidated' if contradicting else (
                    'validated' if not missing and len(domains) >= rule.minimum_independent_sources
                    and not ambiguous and len(targets) <= 1 else 'unknown')
                identity = uuid5(NAMESPACE_URL, _json(('semantic-v1', self.base.tenant,
                    self.base.company, self.base.source, resource, field, asdict(rule))))
                hypothesis = Hypothesis(identity, self.base.tenant,
                    f'{resource}/{field}: may satisfy {rule.role}',
                    (schema_id, *sorted(supporting)), status)
                reason = ('counterexample' if contradicting else 'rule_satisfied' if
                    status == 'validated' else 'independent_grounding_missing_or_ambiguous')
                results.append(SemanticClaim(hypothesis, resource, field, rule,
                    tuple(sorted(supporting)), tuple(sorted(contradicting)),
                    tuple(sorted(independent)), len(supporting) / max(1, len(supporting | contradicting)),
                    missing, (), reason))
        final = []
        for claim in results:
            alternatives = tuple(c.hypothesis.hypothesis_id for c in results if
                c.resource == claim.resource and c.hypothesis.hypothesis_id != claim.hypothesis.hypothesis_id
                and (c.field == claim.field or c.rule.role == claim.rule.role)
                and c.hypothesis.status != 'invalidated')
            collisions = [c for c in results if c.hypothesis.hypothesis_id in alternatives
                          and c.hypothesis.status == 'validated']
            if claim.hypothesis.status == 'validated' and collisions:
                claim = replace(claim, hypothesis=replace(claim.hypothesis, status='unknown'),
                                reason='competing_validated_matches')
            final.append(replace(claim, alternatives=alternatives))
        return tuple(sorted(final, key=lambda c: str(c.hypothesis.hypothesis_id)))

    def claims(self):
        return self._evaluate(self.evidence_snapshot())

    @property
    def history(self):
        self.evidence_snapshot()
        return self._history

    def world_model(self):
        graph = GraphStore()
        statuses = {'validated': GraphStatus.VALIDATED, 'invalidated': GraphStatus.CONTRADICTED,
                    'unknown': GraphStatus.UNKNOWN}
        for claim in self.claims():
            graph.add_node(GraphNode(NodeType.KNOWLEDGE, self.base.tenant,
                str(claim.hypothesis.hypothesis_id), MappingProxyType({
                    'resource': claim.resource, 'field': claim.field, 'role': claim.rule.role,
                    'epistemic_status': claim.hypothesis.status, 'company': self.base.company,
                    'source': self.base.source, 'reason': claim.reason,
                    'revision': self._history[-1].revision_id if self._history else None,
                    'rule': _json(asdict(claim.rule)), 'sample_only': True}),
                statuses[claim.hypothesis.status], claim.support_fraction,
                tuple(sorted(set(claim.hypothesis.supporting_evidence + claim.contradicting))),
                claim.hypothesis.hypothesis_id))
        for obs in (self.base.schema, *(o for o, _ in self.base.evidence_snapshot()),
                    *self.evidence_snapshot()):
            ev = obs.evidence
            graph.add_node(GraphNode(NodeType.EVIDENCE, self.base.tenant, str(ev.evidence_id),
                MappingProxyType({'epistemic_status': 'fact', 'source': ev.source,
                    'observed_at': ev.observed_at.isoformat(), 'company': self.base.company,
                    'provenance': ev.payload.get('provenance', MappingProxyType({
                        'authorization_id': ev.payload.get('authorization_id'),
                        'scope_sha256': ev.payload.get('scope_sha256'),
                        'source_id': ev.payload.get('source_id')}))}),
                GraphStatus.OBSERVED, provenance_ids=(ev.evidence_id,), node_id=ev.evidence_id))
        previous = {}
        for revision in self.history:
            for claim in revision.claims:
                key = claim.hypothesis.hypothesis_id
                node_id = uuid5(key, revision.revision_id)
                graph.add_node(GraphNode(NodeType.KNOWLEDGE, self.base.tenant, str(node_id),
                    MappingProxyType({'epistemic_status': claim.hypothesis.status,
                        'revision': revision.revision_id, 'role': claim.rule.role,
                        'current_claim': str(key), 'historical': True}),
                    statuses[claim.hypothesis.status], provenance_ids=tuple(sorted(set(
                        claim.hypothesis.supporting_evidence + claim.contradicting))), node_id=node_id))
                if key in previous:
                    graph.add_relationship(GraphRelationship(RelationshipType.RELATES_TO,
                        previous[key], node_id, self.base.tenant, GraphStatus.OBSERVED,
                        provenance_ids=revision.evidence_ids,
                        relationship_id=uuid5(node_id, 'revision-of')))
                previous[key] = node_id
        return graph

    def report(self):
        claims = self.claims()
        return MappingProxyType({
            'what_i_observed': MappingProxyType({
                'structural': self.base.facts,
                'process': tuple(MappingProxyType({
                    'epistemic_status': 'fact', 'evidence_id': obs.evidence.evidence_id,
                    'source': obs.evidence.source, 'observed_at': obs.evidence.observed_at,
                    'record': obs.evidence.payload['record'],
                    'provenance': MappingProxyType({k: obs.evidence.payload['provenance'][k]
                        for k in ('authorization_id', 'scope_sha256', 'source_id', 'source_record_id')}),
                }) for obs in self.evidence_snapshot()),
            }),
            'what_i_believe': claims,
            'what_i_validated': tuple(c.hypothesis.hypothesis_id for c in claims
                                     if c.hypothesis.status == 'validated'),
            'what_i_invalidated': tuple(c.hypothesis.hypothesis_id for c in claims
                                       if c.hypothesis.status == 'invalidated'),
            'what_remains_unknown': tuple(c.hypothesis.hypothesis_id for c in claims
                                         if c.hypothesis.status == 'unknown'),
            'required_next_evidence': tuple(sorted({r for c in claims for r in c.missing})),
            'authorization_required': 'separate_record_grant', 'execution_allowed': False,
            'not_allowed': ('collect_without_grant', 'issue_grants', 'write', 'execute'),
        })

    def next_observation(self, *, start, end, sensitivity, budget=24):
        if (type(start) is not date or type(end) is not date or not 0 <= (end-start).days <= 30
                or type(budget) is not int or not 1 <= budget <= 300):
            raise ValueError('bounded proposal window and budget required')
        if not self.base.evidence_snapshot():
            return self.base.next_observation(start=start, end=end, budget=budget,
                sensitivity={(r, f): sensitivity.get((self.base.source, r, f))
                             for r, f, *_ in self.base.facts})
        unresolved = [c for c in self.claims() if c.hypothesis.status != 'validated']
        options = []
        observations = self.evidence_snapshot()
        for instrument in self.instruments:
            for evidence_class in instrument.classes:
                required = {(c.rule.role, k) for c in unresolved for k in c.missing
                            if k[0] == evidence_class}
                if not required:
                    continue
                seen = {(row['channel'], row['dimension'], row['subject_id'])
                        for obs in observations
                        if obs.evidence.payload['provenance']['source_id'] == instrument.source_id
                        and (scope := self._lookup(('scope', obs.evidence.evidence_id))).start <= start
                        and scope.end >= end
                        and (row := obs.evidence.payload['record'])['evidence_class'] == evidence_class}
                relevant = [c for c in unresolved if any(k[0] == evidence_class
                    and len({subject for channel, dimension, subject in seen
                             if (channel, dimension) == k[1:]}) < c.rule.minimum_subjects
                    for k in c.missing)]
                if not relevant:
                    continue
                fields = ANCHOR_FIELDS + (AGGREGATE_FIELDS if evidence_class == 'aggregate' else ())
                if any(sensitivity.get((instrument.source_id, instrument.resource, f)) != 'public'
                       for f in fields):
                    continue
                needed = min(max(1, c.rule.minimum_subjects - len({subject
                    for channel, dimension, subject in seen if (channel, dimension) == k[1:]}))
                    for c in relevant for k in c.missing if k[0] == evidence_class)
                cost = len(fields) * needed
                if cost > budget:
                    continue
                score = sum(2 if c.contradicting else 1 for c in relevant) / cost
                proposal = ObservationRequirement(self.base.tenant, self.base.company,
                    instrument.source_id, instrument.resource, fields, start, end, needed,
                    tuple(c.hypothesis.hypothesis_id for c in relevant),
                    'Compare independent process anchors against unresolved alternatives; '
                    'counterexamples receive double priority. Missing evidence: ' +
                    _json(sorted({k for c in relevant for k in c.missing
                                  if k[0] == evidence_class})) +
                    '. Existing evidence: ' + _json(sorted({str(k) for c in relevant
                        for k in (*c.supporting, *c.contradicting)})) +
                    '. Sensitivity: public. No collection is authorized.', cost)
                options.append((-score, instrument.source_id, evidence_class, proposal))
        return min(options, key=lambda item: item[:3])[3] if options else None
