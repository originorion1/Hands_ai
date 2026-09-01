# ORION v0.1 — Capability Registry & Promotion Ledger

## Purpose

Provide the authoritative, append-only operational record of ORION capabilities as they move from discovery through validation, Shadow operation, approval, execution, demotion, and retirement.

## Core Principle

A capability is a governed object with identity, scope, evidence, authority, lifecycle state, and provenance. No subsystem may silently create production authority by learning alone.

## Lifecycle

```text
DISCOVERED
   ↓
CANDIDATE
   ↓
SUPPORTED
   ↓
VALIDATED
   ↓
SHADOW
   ↓
APPROVED
   ↓
SUPERVISED
   ↓
AUTONOMOUS
   ↓
DEGRADED / DEMOTED
   ↓
REVALIDATION
   ↓
RETIRED
```

Transitions must be explicit, policy-checked, and auditable.

## Capability Identity

Each capability receives a stable identity independent of implementation versions. The registry should record:

- capability ID
- human-readable name
- semantic purpose
- capability version
- implementation reference
- supported pattern IDs
- system-understanding version
- knowledge dependencies
- policy dependencies
- model/agent dependencies
- tenant scope
- workflow scope
- action scope
- risk classification
- lifecycle state

## Promotion Ledger

Every state transition produces an immutable ledger event containing:

- event ID
- capability ID
- previous state
- new state
- reason
- evidence references
- evaluation references
- confidence vector snapshot
- risk assessment
- authorization decision
- actor or governing policy identity
- timestamp
- relevant software/configuration versions

The ledger is append-only. Corrections are new events, never silent mutation of history.

## Authority Boundary

The registry records authority; it does not itself execute actions.

```text
Learning
   ↓
Evidence
   ↓
Promotion Engine
   ↓
Capability Registry
   ↓
Execution Policy
   ↓
Execution Runtime
```

No capability can infer expanded authority from its own registry entry.

## Scoped Authority

A capability's authority must be explicitly bounded by one or more of:

- tenant
- environment
- workflow
- action type
- data class
- transaction/value threshold
- external communication boundary
- time window
- approval requirement

Scope expansion requires a new promotion decision.

## Versioning

Capability versions must be immutable once published. A material change creates a new version and may require revalidation.

Dependencies should be pinned or range-bounded sufficiently to make promotion reproducible and auditable.

## Rollback and Demotion

The registry must support immediate state transition to a safer state when required.

```text
AUTONOMOUS
    ↓
DRIFT / FAILURE
    ↓
DEMOTED
    ↓
SHADOW
    ↓
REVALIDATION
```

Rollback conditions should be predeclared in the promotion package.

## Retirement

Retirement removes a capability from active execution eligibility while preserving its complete historical ledger and evidence references.

## Cross-Tenant Knowledge Boundary

The registry may reference generalized knowledge IDs, but customer data and tenant-specific evidence remain tenant-scoped. Cross-tenant reuse requires approved knowledge governance.

## Query Requirements

The registry should support queries such as:

- What capabilities exist for this tenant?
- Which are currently autonomous?
- Why was a capability promoted?
- What evidence supports it?
- What scope is authorized?
- Which capabilities were demoted and why?
- Which capabilities depend on a changed system version?
- Which capabilities require revalidation?

## Integrity Requirements

The ledger should support tamper-evident history through chained event identifiers or equivalent integrity mechanisms, with durable timestamps and provenance references.

## Acceptance Criteria

1. Every capability has a stable identity.
2. Lifecycle state is explicit.
3. Every promotion/demotion is auditable.
4. Historical ledger events are append-only.
5. Authority is explicitly scoped.
6. Capability versions are immutable.
7. Material changes can force revalidation.
8. Demotion and retirement preserve history.
9. Learning cannot directly create execution authority.
10. The registry remains ERP-neutral and extensible toward large-scale multi-tenant autonomy.
