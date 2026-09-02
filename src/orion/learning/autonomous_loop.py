"""Vendor-neutral autonomous study planning, authorization, and memory loop."""
from __future__ import annotations

import math
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

from ..discovery.planner import validate_discovery_target


class StudyStopReason(StrEnum):
    EXHAUSTED="EXHAUSTED"; CYCLE_LIMIT="CYCLE_LIMIT"; EVIDENCE_BUDGET_LIMIT="EVIDENCE_BUDGET_LIMIT"; NO_AUTHORIZED_OPPORTUNITY="NO_AUTHORIZED_OPPORTUNITY"; NO_INFORMATION_GAIN="NO_INFORMATION_GAIN"; CONFLICT="CONFLICT"

def _id(v: object, label: str):
    if not isinstance(v,str) or not v or v != v.strip(): raise ValueError(f"invalid {label}")
    validate_discovery_target(v)

@dataclass(frozen=True, slots=True)
class LearningObjective:
    objective_id:str; description:str; desired_effects:tuple[str,...]=(); prohibited_effects:tuple[str,...]=(); aim_weights:tuple[tuple[str,float],...]=(("reduce_human_input",1.0),("increase_evidence_coverage",1.0),("increase_predictability",1.0),("reduce_uncertainty_error",1.0))
    def __post_init__(self):
        _id(self.objective_id,"objective_id")
        if not isinstance(self.description,str) or not self.description.strip(): raise ValueError("objective requires description")
        if len({n for n,_ in self.aim_weights}) != len(self.aim_weights) or not self.aim_weights or any(not isinstance(n,str) or not n.strip() or not isinstance(w,(int,float)) or not math.isfinite(w) or w<0 for n,w in self.aim_weights): raise ValueError("invalid objective weights")

@dataclass(frozen=True, slots=True)
class EvidenceCoverage:
    entity:str; field:str; observations_seen:int=0; valid_observations:int=0; distinct_value_count:int=0; missing_count:int=0; prior_prediction_attempts:int=0; prior_prediction_coverage:float=0.0; prior_error:float|None=None; study_count:int=0
    def __post_init__(self):
        _id(self.entity,"entity"); _id(self.field,"field")
        if any(type(v) is not int or v<0 for v in (self.observations_seen,self.valid_observations,self.distinct_value_count,self.missing_count,self.prior_prediction_attempts,self.study_count)) or self.valid_observations>self.observations_seen: raise ValueError("invalid coverage counts")
        if not isinstance(self.prior_prediction_coverage,(int,float)) or not math.isfinite(self.prior_prediction_coverage) or not 0<=self.prior_prediction_coverage<=1: raise ValueError("invalid coverage ratio")
        if self.prior_error is not None and (not isinstance(self.prior_error,(int,float)) or not math.isfinite(self.prior_error)): raise ValueError("invalid error")

@dataclass(frozen=True, slots=True)
class AuthorizationEnvelope:
    tenant_id:str; objective_id:str|None=None; allowed_metadata_entities:frozenset[str]=frozenset(); allowed_record_entities:frozenset[str]=frozenset(); allowed_record_fields:tuple[tuple[str,tuple[str,...]],...]=(); max_entities_per_cycle:int=1; max_fields_per_proposal:int=3; max_records_per_proposal:int=100; max_cycles:int=10; max_cumulative_records:int=1000; max_metadata_targets:int=10; allowed_observation_modes:frozenset[str]=frozenset({"READ_ONLY"})
    def __post_init__(self):
        if not isinstance(self.tenant_id,str) or not self.tenant_id.strip(): raise ValueError("tenant required")
        if self.objective_id is not None: _id(self.objective_id,"objective_id")
        for s in (self.allowed_metadata_entities,self.allowed_record_entities):
            for v in s: _id(v,"entity")
        if len(dict(self.allowed_record_fields)) != len(self.allowed_record_fields): raise ValueError("duplicate field scope")
        for entity,fields in self.allowed_record_fields:
            _id(entity,"entity")
            if len(set(fields)) != len(fields): raise ValueError("duplicate fields")
            for field in fields: _id(field,"field")
        if any(type(v) is not int or v<0 for v in (self.max_entities_per_cycle,self.max_fields_per_proposal,self.max_records_per_proposal,self.max_cycles,self.max_cumulative_records,self.max_metadata_targets)) or self.max_cycles==0 or self.max_fields_per_proposal==0 or self.max_metadata_targets==0: raise ValueError("invalid budget")
        if self.allowed_observation_modes != frozenset({"READ_ONLY"}): raise ValueError("READ_ONLY only")

@dataclass(frozen=True, slots=True)
class StudyOpportunity: entity:str; fields:tuple[str,...]; score:float; score_components:tuple[tuple[str,float],...]; rationale:str; study_kind:str="record_evidence"
@dataclass(frozen=True, slots=True)
class StudyIntent: tenant_id:str; entity:str; fields:tuple[str,...]; study_kind:str; requested_records:int; hypothesis:str; expected_evidence:str; rationale:str; mode:str="READ_ONLY"
@dataclass(frozen=True, slots=True)
class AuthorizedStudyRequest: intent:StudyIntent; tenant_id:str
@dataclass(frozen=True, slots=True)
class StudyOutcome:
    entity:str; fields:tuple[str,...]; observations_acquired:int; valid_count:int; coverage_change:float; uncertainty_reduction:float; information_gain:str; hypothesis_state:str; conflict:bool=False; recommendation_allowed:bool=False; promotion_allowed:bool=False; execution_allowed:bool=False
@dataclass(frozen=True, slots=True)
class LearningMemory:
    attempted:tuple[tuple[str,str],...]=(); outcomes:tuple[StudyOutcome,...]=(); coverage:tuple[EvidenceCoverage,...]=()
    def __post_init__(self):
        keys=[(c.entity,c.field) for c in self.coverage]
        if len(set(keys)) != len(keys): raise ValueError("duplicate coverage scope")
@dataclass(frozen=True, slots=True)
class StudyRun: intents:tuple[StudyIntent,...]; outcomes:tuple[StudyOutcome,...]; memory:LearningMemory; stop_reason:str
@dataclass(frozen=True, slots=True)
class LearningCheckpoint: version:int; tenant_id:str; objective_id:str; sequence:int; memory:LearningMemory

def discover_opportunities(objective:LearningObjective, understanding:Any, coverage:tuple[EvidenceCoverage,...], memory:LearningMemory|None=None):
    if getattr(understanding,"tenant_id",None) is not None and not understanding.tenant_id: raise ValueError("understanding tenant required")
    memory=memory or LearningMemory(); covered={(c.entity,c.field):c for c in coverage}; weights=dict(objective.aim_weights); understood={e.doctype for e in understanding.entities}; out=[]
    for entity in understanding.entities:
        for structural in entity.fields:
            name,field=entity.doctype,structural.fieldname
            if structural.read_only or structural.hidden: continue
            state=covered.get((name,field),EvidenceCoverage(name,field)); gap=3.0 if state.observations_seen==0 else max(0.0,2.0-state.prior_prediction_coverage)+state.missing_count*.1; importance=2.0 if structural.required else .5; relation=.5 if structural.options else 0.0; penalty=min(2.0,state.study_count*.5)+(1.5 if (name,field) in memory.attempted else 0)
            comps=(("human_entry",1.0),("importance",importance),("gap",gap),("relationship",relation),("diminishing_returns",-penalty)); score=weights.get("reduce_human_input",0)+gap*weights.get("increase_evidence_coverage",0)+importance*weights.get("increase_predictability",0)+(1.0 if state.prior_error is not None else 0)*weights.get("reduce_uncertainty_error",0)+relation-penalty
            out.append(StudyOpportunity(name,(field,),score,comps,"generic structural and evidence gap","record_evidence"))
            if structural.options and structural.options not in understood: out.append(StudyOpportunity(structural.options,(),weights.get("increase_evidence_coverage",0)-penalty,(("metadata_gap",1.0),),"unresolved structural relationship","metadata_gap"))
    return tuple(sorted(out,key=lambda x:(-x.score,x.entity,x.fields)))

def generate_intent(opportunity,tenant_id,max_records=100): return StudyIntent(tenant_id,opportunity.entity,opportunity.fields,opportunity.study_kind,0 if opportunity.study_kind=="metadata_gap" else min(max_records,100),"observed evidence will reduce uncertainty","aggregate observations",opportunity.rationale)

def authorize_intent(intent,envelope,understanding=None):
    if intent.tenant_id!=envelope.tenant_id or intent.mode!="READ_ONLY" or intent.study_kind not in {"metadata_gap","record_evidence"}: raise ValueError("study intent outside envelope")
    validate_discovery_target(intent.entity)
    if intent.study_kind=="metadata_gap":
        if intent.entity not in envelope.allowed_metadata_entities or intent.fields or intent.requested_records!=0: raise ValueError("metadata scope denied")
    else:
        if intent.entity not in envelope.allowed_record_entities or not intent.fields or len(intent.fields)>envelope.max_fields_per_proposal or intent.requested_records<1 or intent.requested_records>envelope.max_records_per_proposal: raise ValueError("record scope denied")
        scope = dict(envelope.allowed_record_fields).get(intent.entity,())
        if not scope and understanding is not None and getattr(understanding, "tenant_id", None) is None:
            scope = tuple(intent.fields)  # compatibility for legacy synthetic models only
        if not scope and understanding is None:
            scope = tuple(intent.fields)
        if not set(intent.fields).issubset(scope): raise ValueError("field scope denied")
        for field in intent.fields: validate_discovery_target(field)
    if understanding is not None:
        if getattr(understanding,"tenant_id",envelope.tenant_id)!=envelope.tenant_id: raise ValueError("understanding tenant mismatch")
        entities={e.doctype:e for e in understanding.entities}; entity=entities.get(intent.entity)
        if intent.study_kind=="record_evidence" and (entity is None or not set(intent.fields).issubset({f.fieldname for f in entity.fields})): raise ValueError("target not governed")
    return AuthorizedStudyRequest(intent,envelope.tenant_id)

def _merge_coverage(base,extra):
    result={(c.entity,c.field):c for c in base}
    for c in extra:
        key=(c.entity,c.field); old=result.get(key)
        if old is None: result[key]=c; continue
        if old.observations_seen==c.observations_seen and old.valid_observations==c.valid_observations and old.study_count==c.study_count: raise ValueError("duplicate aggregate coverage")
        result[key]=EvidenceCoverage(c.entity,c.field,old.observations_seen+c.observations_seen,old.valid_observations+c.valid_observations,max(old.distinct_value_count,c.distinct_value_count),old.missing_count+c.missing_count,old.prior_prediction_attempts+c.prior_prediction_attempts,(old.prior_prediction_coverage+c.prior_prediction_coverage)/2,old.prior_error if old.prior_error is not None else c.prior_error,old.study_count+c.study_count)
    return tuple(result.values())

def run_autonomous_loop(objective,understanding,coverage,envelope,runner,*,memory=None):
    if (getattr(understanding,"tenant_id",envelope.tenant_id)!=envelope.tenant_id) or (envelope.objective_id is not None and envelope.objective_id!=objective.objective_id): raise ValueError("structural boundary mismatch")
    memory=memory or LearningMemory(); merged=_merge_coverage(coverage,memory.coverage); current=LearningMemory(memory.attempted,memory.outcomes,merged); intents=[]; outcomes=[]; records=0
    for _ in range(envelope.max_cycles):
        ops=discover_opportunities(objective,understanding,merged+current.coverage,current); authorized=None
        for op in ops:
            try: authorized=authorize_intent(generate_intent(op,envelope.tenant_id,envelope.max_records_per_proposal),envelope,understanding); break
            except ValueError: continue
        if authorized is None: return StudyRun(tuple(intents),tuple(outcomes),current,StudyStopReason.NO_AUTHORIZED_OPPORTUNITY if ops else StudyStopReason.EXHAUSTED)
        if authorized.intent.study_kind=="record_evidence" and records+authorized.intent.requested_records>envelope.max_cumulative_records: return StudyRun(tuple(intents),tuple(outcomes),current,StudyStopReason.EVIDENCE_BUDGET_LIMIT)
        outcome=runner(authorized); _validate_outcome(outcome,authorized,envelope.max_cumulative_records-records); intents.append(authorized.intent); outcomes.append(outcome); records+=outcome.observations_acquired; current=_learn(current,authorized,outcome)
        if outcome.conflict: return StudyRun(tuple(intents),tuple(outcomes),current,StudyStopReason.CONFLICT)
        if outcome.information_gain in {"none", "low"} and not any(o.score > 1.0 and (o.entity in envelope.allowed_record_entities or o.entity in envelope.allowed_metadata_entities) for o in discover_opportunities(objective, understanding, merged + current.coverage, current)):
            return StudyRun(tuple(intents), tuple(outcomes), current, StudyStopReason.NO_INFORMATION_GAIN)
    return StudyRun(tuple(intents),tuple(outcomes),current,StudyStopReason.CYCLE_LIMIT)

def resume_checkpoint(checkpoint,envelope):
    if checkpoint.version!=1 or checkpoint.sequence<1 or checkpoint.tenant_id!=envelope.tenant_id or envelope.objective_id!=checkpoint.objective_id: raise ValueError("checkpoint boundary mismatch")
    for entity,field in checkpoint.memory.attempted:
        scope = dict(envelope.allowed_record_fields).get(entity,())
        if entity not in envelope.allowed_record_entities or (scope and field not in scope): raise ValueError("checkpoint scope exceeds fresh authorization")
    return checkpoint.memory

def _learn(memory,request,outcome):
    updated=[]
    for item in memory.coverage:
        if item.entity==outcome.entity and item.field in outcome.fields: updated.append(EvidenceCoverage(item.entity,item.field,item.observations_seen+outcome.observations_acquired,item.valid_observations+outcome.valid_count,item.distinct_value_count,item.missing_count,item.prior_prediction_attempts+1,item.prior_prediction_coverage+outcome.coverage_change,item.prior_error,item.study_count+1))
        else: updated.append(item)
    for field in outcome.fields:
        if not any(c.entity==outcome.entity and c.field==field for c in updated): updated.append(EvidenceCoverage(outcome.entity,field,outcome.observations_acquired,outcome.valid_count,prior_prediction_attempts=1,prior_prediction_coverage=outcome.coverage_change,study_count=1))
    return LearningMemory(memory.attempted+tuple((outcome.entity,f) for f in outcome.fields),memory.outcomes+(outcome,),tuple(updated))

def _validate_outcome(outcome,request,remaining_budget):
    if not isinstance(outcome,StudyOutcome) or outcome.entity!=request.intent.entity or outcome.fields!=request.intent.fields or type(outcome.observations_acquired) is not int or not 0<=outcome.observations_acquired<=request.intent.requested_records or outcome.observations_acquired>remaining_budget or type(outcome.valid_count) is not int or not 0<=outcome.valid_count<=outcome.observations_acquired or outcome.information_gain not in {"high","medium","low","none"} or outcome.hypothesis_state not in {"SUPPORTED","NOT_SUPPORTED","INCONCLUSIVE"} or any(not isinstance(v,(int,float)) or not math.isfinite(v) for v in (outcome.coverage_change,outcome.uncertainty_reduction)) or not 0<=outcome.coverage_change<=1 or not -1<=outcome.uncertainty_reduction<=1 or outcome.recommendation_allowed or outcome.promotion_allowed or outcome.execution_allowed: raise ValueError("runner outcome violates study contract")
