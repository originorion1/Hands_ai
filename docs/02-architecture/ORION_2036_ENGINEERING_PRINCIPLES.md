# ORION 2036 Engineering Principles

**Status:** Accepted
**Scope:** All future ORION architecture and engineering work
**Current implementation horizon:** 2026 prototype
**Architectural horizon:** 2036

## Purpose

This document establishes the forward-looking engineering discipline for ORION. The 2036 horizon is an architectural compass, not a requirement to build unnecessary infrastructure today.

## Core rule

Build the smallest 2026 implementation that preserves the contracts and replacement points required for plausible 2036 evolution.

## Decision test

Every material architectural decision should answer:

1. Does it solve the current requirement?
2. Is the implementation proportionate to the current stage?
3. Does it unnecessarily lock ORION to a provider, database, ERP, deployment model, or agent design?
4. Can the implementation be replaced without rewriting stable domain contracts?
5. Can ORION evolve to more companies, systems, agents, models, modalities, and autonomy without kernel redesign?

## 2036 capabilities to preserve for

The architecture should remain capable of evolving toward:

- many companies and industries
- many ERP and business systems
- large populations of specialized agents
- multiple AI/model providers and local models
- multimodal organizational inputs
- continuously evolving organizational/world models
- validated generalized knowledge with strict tenant isolation
- autonomous discovery and capability acquisition
- human/AI collaborative workflows
- stronger policy, authority, verification, and audit systems
- distributed execution where justified
- continuous evaluation and improvement
- replacement of models and infrastructure without domain rewrites

## Engineering boundaries

The following must remain explicit and replaceable:

- identity
- epistemics and knowledge
- provenance
- policy and authority
- events
- agent contracts
- model providers and routing
- system connectors
- capabilities
- Hands/execution
- storage/search
- observability
- deployment

## Anti-overengineering rule

2036 readiness does not justify implementing speculative distributed infrastructure, dozens of agents, complex UI, autonomous self-modification, or other capabilities before evidence requires them.

Use interfaces, contracts, versioning, and dependency inversion to preserve future options; implement infrastructure only when the current product need and measured evidence justify it.

## Evolution rule

When a future requirement becomes concrete, prefer extending an existing stable contract over introducing a parallel concept that duplicates responsibility. If a contract is fundamentally wrong, supersede it explicitly with migration and provenance rather than silently breaking consumers.

## Data and knowledge rule

Customer operational data remains customer-scoped. Reusable knowledge must be generalized, validated, governed, and separated from customer-identifying or commercially sensitive data.

## Autonomy rule

Intelligence does not imply authority. Agent reasoning, capability selection, authorization, execution, and verification remain separate even as ORION becomes more autonomous.

## Engineering workflow

```text
2036 architectural question
        ↓
2026 minimum viable implementation
        ↓
Laboratory experiment
        ↓
Measurement / review
        ↓
Approval
        ↓
Shadow validation
        ↓
Production
        ↓
Observed learning
        ↓
Architecture evolves deliberately
```

## Definition of success

ORION succeeds when increasing intelligence and automation reduce repetitive human data-entry work while preserving correctness, customer isolation, provenance, governance, and the ability to evolve.
