"""Opaque schemas exist only inside the fake environment, never in grant inputs."""
import random
from dataclasses import replace
from urllib.parse import parse_qs, urlsplit

import pytest
from test_erpnext_metadata_adapter import FakeResponse
from test_pilot_metadata import grant, launch, request

from orion.discovery.erpnext_pilot_metadata import ERPNextPilotMetadataReader
from orion.discovery.pilot_metadata import ScopeProposal
from orion.understanding.schema_evidence import FieldDeclaration, interpret_schema


class UnfamiliarEnvironment:
    def __init__(self, seed):
        rng = random.Random(seed)
        self.resource = 'r_' + format(rng.getrandbits(96), 'x')
        self.names = tuple('f_' + format(rng.getrandbits(96), 'x') for _ in range(5))
        self.kinds = ('Float', 'Date', 'Date', 'Link', 'Float')
        self.calls = []

    def open(self, req, timeout):
        self.calls.append(req)
        target = urlsplit(req.full_url)
        if target.path == '/api/resource/DocType':
            return FakeResponse({'data':[{'name': self.resource}]}, url=req.full_url)
        assert target.path == '/api/method/frappe.desk.form.load.getdoctype'
        assert parse_qs(target.query) == {'doctype':[self.resource]}
        fields = [{'fieldname':name,'fieldtype':kind,'hidden':int(i == 4)}
                  for i,(name,kind) in enumerate(zip(self.names,self.kinds,strict=True))]
        return FakeResponse({'message':{'docs':[{'name':self.resource,'fields':fields}]}},url=req.full_url)


def discover(environment, lookup=lambda key: grant()):
    # The application knows the protocol and authorized origin, not the schema.
    adapter = ERPNextPilotMetadataReader(source_id=request().source_id,
        api_key='fixture',api_secret='fixture',opener=environment.open)
    return launch(adapter, lookup=lookup)


@pytest.mark.parametrize('seed',[9,83,412])
def test_discovers_opaque_names_and_explains_candidates_without_answers(seed):
    environment = UnfamiliarEnvironment(seed)
    assert environment.resource not in repr(grant())
    assert all(name not in repr(grant()) for name in environment.names)
    result = discover(environment)
    assert len(environment.calls) == 2
    assert result.catalog == (environment.resource,)
    proposal = result.proposals[0]
    assert proposal.fields == proposal.date_fields == ()  # No executable mapping invented.
    interpretation = proposal.interpretation
    assert {c.declaration.name for c in interpretation.candidates} == set(environment.names[:4])
    assert [c.role for c in interpretation.candidates] == [
        'number_candidate','date_candidate','date_candidate','reference_candidate']
    for candidate in interpretation.candidates:
        assert candidate.location == (environment.resource, candidate.declaration.name)
        assert len(candidate.evidence_sha256) == 64
    assert 'date_role_ambiguous' in interpretation.unknowns
    assert 'tenant_filter_unconfirmed' in interpretation.unknowns
    assert result.review_required and not result.record_reads_allowed
    evidence = result.observations[0].evidence
    assert evidence.tenant_id == request().tenant_id
    assert evidence.payload['source_id'] == request().source_id
    assert evidence.payload['authorization_id'] == grant().authorization_id
    assert evidence.payload['scope_sha256']
    frozen = evidence.payload['proposals'][0]['interpretation']['candidates'][0]
    with pytest.raises(TypeError):
        frozen['declaration']['kind'] = 'confirmed'


def test_interpretation_follows_evidence_types_not_names():
    environment = UnfamiliarEnvironment(78)
    first = discover(environment).proposals[0].interpretation
    environment.kinds = ('Date',*environment.kinds[1:])
    second = discover(environment).proposals[0].interpretation
    assert first.candidates[0].declaration.name == second.candidates[0].declaration.name
    assert first.candidates[0].role != second.candidates[0].role
    assert first.candidates[0].evidence_sha256 != second.candidates[0].evidence_sha256


def test_discovery_denied_without_metadata_authority_and_exclusions_stop_schema():
    environment = UnfamiliarEnvironment(2)
    with pytest.raises(ValueError):
        discover(environment,lookup=lambda key:None)
    assert environment.calls == []
    result = discover(environment,lookup=lambda key:replace(grant(),
        excluded_resources=(environment.resource,)))
    assert len(environment.calls)==1 and result.proposals == ()


def test_cross_resource_or_tampered_field_provenance_rejected():
    declaration=FieldDeclaration('opaque','opaque_field','date','Date')
    with pytest.raises(ValueError):
        interpret_schema('different',(declaration,))
    interpretation=interpret_schema('opaque',(declaration,))
    corrupted=replace(interpretation,candidates=(replace(interpretation.candidates[0],
                                                        evidence_sha256='forged'),))
    with pytest.raises(ValueError):
        ScopeProposal('opaque',(),(),corrupted)


def test_unfamiliar_discovery_does_not_silently_create_record_authorization():
    from test_pilot_read import launch as record_launch
    from test_pilot_read import reader as record_reader
    result=discover(UnfamiliarEnvironment(17))
    calls=[]
    with pytest.raises(ValueError):
        record_launch(record_reader(calls),grants=lambda key:result.proposals[0])
    assert calls == []
