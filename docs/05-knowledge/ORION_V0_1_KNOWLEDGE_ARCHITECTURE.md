# ORION v0.1 Knowledge Architecture

**Status:** Approved baseline
**Phase:** Laboratory — Step 3.1
**Architectural horizon:** 2036

## 1. Governing principle

The Kernel governs knowledge but does not accumulate knowledge. Knowledge is persisted and evolved by the Knowledge System through explicit contracts and governed lifecycle transitions.

## 2. Knowledge classes

| Class | Name | Scope | Default reuse |
|---|---|---|---|
| K0 | ORION System Knowledge | ORION | Global |
| K1 | Common Knowledge | Global | Very high |
| K2 | Industry Knowledge | Industry | High |
| K3 | Organization Knowledge | Customer organization | Restricted |
| K4 | System Knowledge | Organization/system | Restricted |
| K5 | Workflow / Pattern Knowledge | Workflow/industry/organization | Governed |
| K6 | Case / Episodic Knowledge | Customer/case | Private |

Classes are semantic/scope classifications, not quality rankings.

## 3. Persistence

For v0.1, PostgreSQL is the authoritative persistence layer behind KnowledgePort. Structured records, provenance references, lifecycle state, versions, and retrieval metadata are stored there. Semantic/vector retrieval may initially use PostgreSQL-compatible capabilities; a separate vector or graph database is not required until measured workload justifies it.

The physical store is an implementation detail. Core/domain code depends on ports, not PostgreSQL APIs.

## 4. Conceptual stores

The implementation SHALL keep these concepts distinct even when persisted in the same database:

- knowledge
- evidence
- observations
- hypotheses
- patterns
- cases
- mappings
- procedures
- evaluations
- versions

An observation is not automatically knowledge. Evidence is not itself a conclusion. A hypothesis is not an approved rule.

## 5. Knowledge lifecycle

```text
Observation
  -> Hypothesis / Candidate
  -> Evidence collection
  -> Evaluation
  -> Validated
  -> Active
  -> Generalized (when eligible)
  -> Reusable
```

Active knowledge may later be revised or superseded when contradictory or higher-quality evidence is accepted.

## 6. Knowledge object minimum metadata

Every knowledge object SHOULD have:

- stable identifier
- class
- scope
- owner/tenant
- source and evidence references
- provenance
- confidence/quality information
- validation status
- applicability constraints
- sensitivity classification
- retention policy
- created/updated timestamps
- version
- supersession relationship where applicable

## 7. Tenant and privacy boundary

Customer operational data and customer-specific knowledge are tenant-scoped. K3, K4, and K6 objects must not become global or cross-customer knowledge by implicit behavior.

Cross-customer reuse requires an explicit generalization/promotion process that removes or excludes customer-identifying, confidential, commercially sensitive, or otherwise restricted information.

## 8. Promotion to reusable knowledge

A customer-derived pattern may become reusable only when:

1. sufficient evidence exists;
2. the pattern is generalized beyond the individual case;
3. restricted customer information is removed;
4. applicability is understood;
5. validation criteria are satisfied;
6. promotion is explicitly recorded and authorized.

No amount of repetition alone automatically grants global scope.

## 9. Retrieval

Agents and services access knowledge through a KnowledgePort/Gateway. Retrieval SHALL apply tenant scope, authorization, applicability, lifecycle state, and sensitivity constraints before returning knowledge.

The retrieval layer may combine structured filters, full-text search, semantic/vector retrieval, and future graph traversal behind one stable interface.

## 10. Evidence and provenance

Knowledge must remain traceable to supporting evidence where applicable. The system should be able to answer:

- what is believed;
- why it is believed;
- which evidence supports it;
- who/what produced it;
- when it was produced;
- what scope applies;
- whether it has been validated;
- which version is active.

## 11. Learning boundary

Learning services propose changes. They do not bypass policy to write trusted knowledge directly.

```text
Agent / observation
      ↓
Learning service
      ↓
Candidate knowledge
      ↓
Evidence + evaluation
      ↓
Policy / validation
      ↓
Knowledge Gateway
      ↓
Knowledge Store
```

## 12. ERP/system independence

ERPNext is only the first system from which ORION may learn. Knowledge objects must describe business/system semantics in ORION's canonical language where possible, not expose ERPNext-specific structures as the universal domain model.

ERPNext-specific metadata belongs in the ERPNext adapter/system-knowledge boundary.

## 13. 2036 evolution

The architecture must permit future specialized stores, distributed knowledge services, graph representations, multimodal evidence, stronger semantic retrieval, federated knowledge, and advanced learning systems without changing the Kernel's core contracts.

These are future options, not v0.1 implementation requirements.

## 14. Non-negotiable invariants

- Knowledge is outside the Kernel.
- Customer data is not reusable knowledge by default.
- Scope is explicit.
- Provenance is retained.
- Knowledge changes are versioned.
- Promotion is governed.
- Retrieval is authorized.
- Provider and storage implementations remain replaceable.
- No automatic global promotion from customer activity.
