"""Operational roles grounded in an independently instrumented trace protocol.

The trace protocol has known semantics; the business schema does not. Archive
and protocol configuration are trusted composition, not record authorization.
No collection, grant creation, network or business-domain ontology lives here.
"""
from dataclasses import dataclass
from datetime import date
from types import MappingProxyType
from uuid import NAMESPACE_URL, uuid5

from ..contracts import EvidenceKind, Observation, ObservationMode
from ..discovery.pilot_read import PilotRequest, _text
from .graph import GraphNode, GraphStatus, GraphStore, NodeType
from .hypotheses import Hypothesis
from .role_study import ObservationRequirement, RoleClaim, RoleStudy

COMMON = ('trace_id', 'partition', 'recorded_on', 'subject_source',
          'subject_resource', 'subject_id')
TEMPORAL = (*COMMON, 'occurred_on')
RELATIONAL = (*COMMON, 'affected_resource', 'affected_id')


@dataclass(frozen=True, slots=True)
class TraceProtocol:
    source_id: str
    resource: str
    provenance_source: str

    def __post_init__(self):
        for value in (self.source_id, self.resource, self.provenance_source):
            _text(value)


class ProcessRoleStudy:
    """Compose structural discovery with separately admitted local process traces."""

    def __init__(self, study: RoleStudy, *, protocol: TraceProtocol, evidence_lookup):
        if type(study) is not RoleStudy or type(protocol) is not TraceProtocol:
            raise TypeError('explicit study and trusted trace protocol required')
        protocol.__post_init__()
        if protocol.source_id == study.source:
            raise ValueError('separate trace origin required; independence still requires review')
        self._study, self._protocol, self._lookup = study, protocol, evidence_lookup
        self._traces = {}

    def _verify(self, observation, request):
        study, protocol = self._study, self._protocol
        if type(request) is not PilotRequest:
            raise ValueError('archived trace scope required')
        request.__post_init__()
        if (request.tenant_id, request.company, request.source_id, request.resource) != (
                study.tenant, study.company, protocol.source_id, protocol.resource):
            raise ValueError('trace scope mismatch')
        if (type(observation) is not Observation
                or observation.mode is not ObservationMode.READ_ONLY
                or self._lookup(observation.evidence.evidence_id) != observation
                or self._lookup(('scope', observation.evidence.evidence_id)) != request):
            raise ValueError('trusted trace admission archive required')
        ev = observation.evidence
        p = ev.payload
        if (ev.kind is not EvidenceKind.EXPERIMENT or ev.tenant_id != study.tenant
                or ev.source != protocol.provenance_source
                or p.get('resource') != protocol.resource):
            raise ValueError('trace protocol provenance mismatch')
        provenance = p.get('provenance', {})
        if (provenance.get('source_id') != protocol.source_id
                or not provenance.get('authorization_id') or not provenance.get('scope_sha256')):
            raise ValueError('trace grant provenance missing')
        fields = set(request.fields)
        allowed = set(TEMPORAL) | set(RELATIONAL)
        if (not set(COMMON) <= fields or not fields <= allowed
                or not (set(TEMPORAL) <= fields or set(RELATIONAL) <= fields)
                or request.date_field != 'recorded_on'):
            raise ValueError('bounded trace protocol fields required')
        row = p['record']
        if (set(row) != fields or row['partition'] != study.company
                or provenance.get('source_record_id') != row['trace_id']):
            raise ValueError('trace row scope mismatch')
        for key in COMMON:
            _text(row[key])
        recorded = date.fromisoformat(row['recorded_on'])
        if not request.start <= recorded <= request.end:
            raise ValueError('trace outside authorized window')
        if row['subject_source'] != study.source:
            raise ValueError('trace refers to another source')
        resources = {fact[0] for fact in study.facts}
        if row['subject_resource'] not in resources:
            raise ValueError('trace subject resource not discovered')
        if 'occurred_on' in fields and date.fromisoformat(row['occurred_on']) > recorded:
            raise ValueError('inconsistent trace chronology')
        if {'affected_resource','affected_id'} & fields:
            if not {'affected_resource','affected_id'} <= fields:
                raise ValueError('partial affected-object evidence')
            _text(row['affected_resource'])
            _text(row['affected_id'])
            if row['affected_resource'] not in resources:
                raise ValueError('affected resource not discovered')
        return row

    def observe_traces(self, observations, *, request):
        if (type(request) is not PilotRequest or type(observations) is not tuple
                or len(observations) > min(request.max_records, 25)):
            raise ValueError('bounded immutable trace batch required')
        proposed = dict(self._traces)
        self._study.evidence_snapshot()
        for observation in observations:
            self._verify(observation, request)
            proposed[observation.evidence.evidence_id] = (observation, request)
        if len(proposed) > 100:
            raise ValueError('trace budget exhausted')
        self._traces = proposed

    def claims(self):
        study = self._study
        records = study.evidence_snapshot()
        traces = [(obs, self._verify(obs, req)) for obs, req in self._traces.values()]
        result = []
        for resource, field, kind, schema_id in study.facts:
            roles = ('event_date','recording_date') if kind == 'date' else (
                ('affected_object_reference',) if kind == 'reference' else ())
            for role in roles:
                support, against, witnesses = set(), set(), set()
                ambiguous = False
                affected_resources = set()
                for trace, row in traces:
                    if row['subject_resource'] != resource:
                        continue
                    expected_key = {'event_date':'occurred_on','recording_date':'recorded_on',
                                    'affected_object_reference':'affected_id'}[role]
                    if expected_key not in row:
                        continue
                    matches = [obs for obs, _ in records
                               if obs.evidence.payload['resource'] == resource
                               and obs.evidence.payload['provenance']['source_record_id'] == row['subject_id']
                               and field in obs.evidence.payload['record']]
                    if not matches:
                        continue
                    # No source revision anchor: changing record versions are ambiguous.
                    if any(dict(obs.evidence.payload['record']) !=
                           dict(matches[0].evidence.payload['record']) for obs in matches[1:]):
                        ambiguous = True
                        continue
                    actual = matches[0].evidence.payload['record'][field]
                    if role != 'affected_object_reference':
                        try:
                            actual = date.fromisoformat(actual).isoformat()
                        except (TypeError, ValueError):
                            continue
                    elif not isinstance(actual, str) or not actual:
                        continue
                    ids = {trace.evidence.evidence_id, *(obs.evidence.evidence_id for obs in matches)}
                    if role == 'affected_object_reference':
                        affected_resources.add(row['affected_resource'])
                    if actual == row[expected_key]:
                        support.update(ids)
                        witnesses.add(row['subject_id'])
                    else:
                        against.update(ids)
                target_ambiguous = len(affected_resources) > 1
                status = 'invalidated' if against else (
                    'validated' if len(witnesses) >= 2 and not ambiguous and not target_ambiguous else 'unknown')
                unknowns = ('commercial_meaning_unknown', 'causation_unproven',
                            'sample_only', 'trace_instrumentation_trusted')
                if ambiguous:
                    unknowns += ('subject_revision_ambiguous',)
                if target_ambiguous:
                    unknowns += ('affected_resource_ambiguous',)
                identity = uuid5(NAMESPACE_URL, repr(('process-role-v1',schema_id,
                    study.tenant,study.source,resource,field,role)))
                h = Hypothesis(identity,study.tenant,
                    f'{field}: matches independently reported {role} in {resource}',
                    (schema_id,*sorted(support)),status)
                confidence = len(support) / (len(support)+len(against)) if support or against else 0.0
                result.append(RoleClaim(h,resource,(field,),role,tuple(sorted(support)),
                                        tuple(sorted(against)),confidence,unknowns))
        # Multiple fields matching the same semantic anchor do not identify a unique role.
        from dataclasses import replace
        evaluated = tuple(result)
        for i, claim in enumerate(evaluated):
            alternatives = [c for c in evaluated if c.resource == claim.resource
                            and c.predicate == claim.predicate and c.hypothesis.status == 'validated']
            overlapping_roles = [c for c in evaluated if c.resource == claim.resource
                                 and c.fields == claim.fields and c.predicate != claim.predicate
                                 and c.hypothesis.status == 'validated']
            if claim.hypothesis.status == 'validated' and (len(alternatives) > 1 or overlapping_roles):
                reasons = ('multiple_matching_fields',) if len(alternatives) > 1 else ()
                if overlapping_roles:
                    reasons += ('indistinguishable_trace_roles',)
                result[i] = replace(claim,hypothesis=replace(claim.hypothesis,status='unknown'),
                                    unknowns=claim.unknowns+reasons)
        return tuple(result)

    def next_observation(self, *, start, end, sensitivity, budget=20):
        if (type(start) is not date or type(end) is not date or start > end
                or (end-start).days > 31 or type(budget) is not int or budget < 1):
            raise ValueError('bounded trace investigation policy required')
        claims = self.claims()
        choices = []
        for fields, roles in ((TEMPORAL,{'event_date','recording_date'}),
                              (RELATIONAL,{'affected_object_reference'})):
            unresolved = [c for c in claims if c.predicate in roles and (
                c.hypothesis.status == 'unknown' or (c.supporting and c.contradicting))]
            if not unresolved or len(fields)*2 > budget:
                continue
            if any(sensitivity.get((self._protocol.resource,f)) != 'public' for f in fields):
                continue
            if any(set(fields) <= set(req.fields) and req.start <= start and end <= req.end
                   for _,req in self._traces.values()):
                continue
            score = sum(2 if c.supporting and c.contradicting else 1 for c in unresolved)
            choices.append((-score/(len(fields)*2),fields,unresolved))
        if not choices:
            return None
        _,fields,unresolved = min(choices,key=lambda v:(v[0],v[1]))
        return ObservationRequirement(self._study.tenant,self._study.company,self._protocol.source_id,
            self._protocol.resource,fields,start,end,2,
            tuple(c.hypothesis.hypothesis_id for c in unresolved),
            'Compare opaque fields with independently instrumented process evidence.',len(fields)*2)

    def evidence_snapshot(self):
        """Revalidated immutable trace references for recovery, not authority."""
        self.claims()
        return self._study, self._protocol, tuple(self._traces[key] for key in sorted(self._traces))

    def world_model(self):
        graph = GraphStore()
        for c in self.claims():
            if c.hypothesis.status not in {'validated','invalidated'}:
                continue
            graph.add_node(GraphNode(NodeType.KNOWLEDGE,self._study.tenant,c.hypothesis.statement,
                MappingProxyType({'resource':c.resource,'fields':c.fields,'sample_only':True,
                                  'commercial_meaning':'unknown'}),
                GraphStatus.VALIDATED if c.hypothesis.status == 'validated' else GraphStatus.CONTRADICTED,
                c.confidence,(self._study.schema.evidence.evidence_id,*c.supporting,*c.contradicting),
                node_id=c.hypothesis.hypothesis_id))
        return graph
