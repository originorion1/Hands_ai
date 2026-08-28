# ORION v0.1 — System Graph Contracts & Relationship Inference

## Status

Approved architecture for the System Graph boundary.

## Purpose

Represent discovered enterprise structure, behavior, processes, dependencies, capabilities, and evidence relationships in a vendor-neutral model that can evolve as ORION learns.

## Core Principle

The graph is a representation of understanding, not a copy of the customer system. Every material relationship must have provenance and an explicit confidence/state.

## Graph Model

```text
Node
  ├── stable identity
  ├── node type
  ├── tenant scope
  ├── attributes
  ├── semantic state
  ├── confidence
  └── provenance references

Relationship
  ├── stable identity
  ├── relationship type
  ├── source node
  ├── target node
  ├── direction
  ├── confidence
  ├── status
  ├── validity interval
  └── provenance references
```

## Node Categories

Initial generic categories include:

- entity
- attribute
- actor
- process
- action
- state
- event
- system
- component
- capability
- condition
- outcome
- evidence reference
- knowledge concept

The model must allow new node types without breaking existing consumers.

## Relationship Categories

Examples include:

- `RELATES_TO`
- `CONTAINS`
- `HAS_ATTRIBUTE`
- `DEPENDS_ON`
- `TRIGGERS`
- `PRECEDES`
- `TRANSITIONS_TO`
- `REQUIRES`
- `PRODUCES`
- `CONSUMES`
- `PERFORMED_BY`
- `AUTHORIZES`
- `SUPPORTED_BY`
- `CONTRADICTED_BY`

Relationship vocabulary should remain semantic rather than vendor-specific.

## Tenant Boundary

Tenant scope is mandatory for customer-derived graph elements. Cross-tenant generalized relationships require explicit governance and must not expose customer identifiers or raw customer data.

## Provenance

Every inferred relationship must record the evidence and inference context supporting it.

```text
Relationship
    ↓
Evidence references
    ↓
Observations
    ↓
Source / method
```

Inferred facts must be distinguishable from directly observed facts.

## Confidence and Status

Graph elements may carry states such as:

- `OBSERVED`
- `SUPPORTED`
- `INFERRED`
- `VALIDATED`
- `CONTRADICTED`
- `STALE`
- `RETIRED`
- `UNKNOWN`

Confidence is multidimensional and must not be reduced to a permanent scalar truth value.

## Temporal Semantics

Relationships may change over time. The model should support validity intervals or versioned observations so ORION can reason about historical and current system behavior without rewriting history.

## Relationship Inference

The inference engine may propose relationships from:

- repeated observations
- structural metadata
- behavioral traces
- workflow sequences
- semantic similarity
- documented rules
- validated outcomes
- human corrections

Inference is a hypothesis-producing operation. It does not directly create execution authority.

## Contradiction Handling

Contradictory relationships remain represented rather than silently overwritten.

```text
A ──REQUIRES──> B
A ──NOT_REQUIRES──> B
        ↓
   contradiction
        ↓
 contextual investigation
```

Context may reveal that both are valid under different conditions.

## Query Contract

The graph boundary should eventually support queries such as:

- What depends on this entity?
- Which process consumes this event?
- What states can this entity transition through?
- Which capabilities rely on this workflow?
- What evidence supports this relationship?
- What changed since the previous system-understanding version?
- Which relationships are uncertain or contradictory?

## Persistence Independence

The domain contract must not assume a graph database. An in-memory implementation is suitable for early tests; relational, graph, document, or distributed persistence can be introduced behind the same contract when scale and query patterns justify it.

## Performance Principles

Avoid unrestricted graph traversal. Queries should define bounded depth, tenant scope, and resource limits. Expensive inference should be asynchronous or explicitly budgeted.

## Integrity

Stable identifiers, schema versions, provenance references, and integrity metadata must permit reproducible reconstruction of important graph states.

## Future Extensions

The contract must accommodate:

- system digital twins
- process simulation
- counterfactual reasoning
- temporal graphs
- multi-agent observations
- physical-device relationships
- dependency-impact analysis
- active-learning discovery planning

These extensions must not leak into the Kernel prematurely.

## Acceptance Criteria

1. Graph semantics are vendor-neutral.
2. Nodes and relationships have stable identities.
3. Tenant scope is explicit.
4. Inference is distinguishable from observation.
5. Material relationships have provenance.
6. Contradictions are retained and investigated rather than silently overwritten.
7. Historical versions can be represented.
8. Persistence technology is replaceable.
9. Traversal and inference have resource boundaries.
10. The contract can evolve toward digital-twin, counterfactual, multi-agent, and physical-world reasoning without redesigning the Kernel.
