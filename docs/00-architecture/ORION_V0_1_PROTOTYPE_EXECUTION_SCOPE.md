# ORION v0.1 — Prototype Execution Scope

## Status
Approved. This document is the execution guardrail for the first live prototype.

## Objective

Deliver a usable ORION prototype quickly while preserving clean architectural boundaries for future expansion.

The prototype is successful when ORION can study a real customer environment, build a bounded understanding of a selected first-level data-entry workflow, assist or execute that workflow under governance, audit the work, and escalate uncertain/high-risk cases to the responsible human.

## 2036 Guardrail

Architecture must avoid vendor lock-in, overloaded agents, irreversible knowledge coupling, and monolithic design. Future capabilities are represented by stable contracts where useful, but are not implemented until they are required by the prototype or validated by evidence.

## Prototype Critical Path

```text
Core Runtime
    ↓
Observation / Discovery
    ↓
Evidence
    ↓
System Understanding
    ↓
Governed Knowledge
    ↓
Customer Specialist Agent
    ↓
Bounded Orchestration
    ↓
Human Escalation when required
    ↓
Controlled Execution
    ↓
Verification + Audit
```

## Explicitly Deferred

The following are architectural future targets, not v0.1 implementation requirements:

- phone/WhatsApp customer communications
- physical I/O and robotics
- broad device ecosystems
- multi-customer knowledge federation beyond governed generalization
- agent marketplace
- full digital twins
- advanced simulation/counterfactual infrastructure
- premature distributed infrastructure
- mandatory graph-database adoption
- large-scale autonomous model training infrastructure

## Agent Boundary

The prototype must use a focused specialist responsibility rather than a general-purpose super-agent. The orchestrator assigns work and coordinates specialists. Governance remains independent from the acting agent.

## Fact-First Boundary

No consequential action may be based solely on model output, inference, or unverified knowledge. Required facts/evidence, applicable policy, authorization, and verification must be available according to the risk of the action.

## Knowledge Integrity

Continuous learning may run continuously, but promotion to trusted knowledge is governed. Customer data remains customer-scoped. Reusable knowledge is generalized and validated before entering common knowledge.

## Human Escalation

For high-risk, ambiguous, contradictory, or out-of-scope cases, the orchestrator may consult, request confirmation from, or transfer the case to the responsible human according to policy.

## Engineering Quality Gates

Before calling a prototype capability live-ready:

1. Domain logic is separated from infrastructure.
2. Inputs are validated at boundaries.
3. Errors fail safely and are observable.
4. Tests cover critical invariants and failure modes.
5. Customer and common knowledge boundaries are enforced.
6. Consequential actions are auditable.
7. Secrets and credentials are never embedded in source code.
8. External systems remain behind replaceable adapters/contracts.
9. Changes are documented and committed incrementally.
10. The prototype path remains small enough to operate and debug.

## Definition of Done

A v0.1 slice is considered done when it works end-to-end in a controlled environment, has automated tests for its critical invariants, emits sufficient audit/provenance information, and can be demonstrated against the selected customer workflow without requiring a redesign of the Kernel.

## Operating Principle

> Build the smallest reliable system that proves ORION's core capability: learn and understand an unfamiliar enterprise environment, then safely perform useful work with minimal human interaction.
