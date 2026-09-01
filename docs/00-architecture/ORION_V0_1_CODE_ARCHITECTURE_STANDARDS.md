# ORION v0.1 — Code Architecture & Professional Engineering Standards

## Status

Approved engineering baseline for all new ORION implementation work.

## Objective

Keep ORION understandable, testable, replaceable, and extensible as the system grows from one prototype and one ERP environment into a multi-system, multi-tenant autonomous platform.

## Architectural North Star

```text
                 ORION KERNEL
                      │
        ┌─────────────┼─────────────┐
        ↓             ↓             ↓
   Understanding   Learning      Governance
        │             │             │
        └─────────────┼─────────────┘
                      ↓
                 Capabilities
                      ↓
                Execution Policy
                      ↓
                Execution Runtime
                      ↓
                  Adapters
                      ↓
             External Systems
```

The Kernel coordinates. It must not become the storage layer, ERP implementation, model implementation, agent runtime, or business-rule dumping ground.

## Design Rules

### 1. Stable contracts, replaceable implementations

Depend on small interfaces/protocols. Implementations may change without forcing consumers to change.

### 2. Dependency direction

Dependencies point toward stable domain contracts. Infrastructure and vendor-specific adapters remain at the edges.

```text
Domain contracts ← application services ← infrastructure/adapters
```

### 3. Bounded contexts

Separate system understanding, evidence, learning, knowledge, capabilities, governance, execution, and tenant management. Do not create a universal service or utility module.

### 4. Single ownership of state

Every persistent state has one authoritative owner. Other components receive references or projections rather than silently maintaining competing copies.

### 5. Immutable evidence and audit history

Evidence, decisions, evaluations, and governance events should be append-oriented. Corrections create new records rather than rewriting history.

### 6. Explicit provenance

Material conclusions must be traceable to source evidence, model/agent versions, policy versions, and relevant system-understanding/knowledge versions.

### 7. Idempotent side effects

Any operation that can mutate an external system must have an explicit idempotency strategy, retry semantics, and duplicate-prevention mechanism.

### 8. Fail closed at authority boundaries

Missing evidence, ambiguous scope, unavailable policy, invalid authorization, or uncertain critical state must not silently degrade into execution.

### 9. No hidden global state

Avoid module-level mutable state, singleton service locators, implicit caches, and ambient tenant context. Dependencies and scopes must be explicit.

### 10. Typed boundaries

Use explicit domain types for identifiers, states, modes, outcomes, and policies where ambiguity could create defects. Avoid unbounded dictionaries as the permanent contract for important domain objects.

### 11. Error taxonomy

Errors should be classified by recoverability and ownership. Do not use generic exceptions as control flow. Preserve root cause and correlation identifiers.

### 12. Time correctness

Store timestamps with explicit timezone semantics. Never mix naive and aware datetimes. Business-local time belongs at explicit boundary layers.

### 13. Concurrency correctness

Assume asynchronous execution, retries, duplicate delivery, partial failure, and concurrent updates. Use optimistic concurrency/version checks where state can race.

### 14. Resource boundaries

Every external operation must have bounded timeout, retry, concurrency, memory, payload, and rate behavior appropriate to its risk.

### 15. Security by construction

Secrets never enter source control, tests, logs, evidence payloads, or error messages. Tenant scope and authorization are explicit at security-sensitive boundaries.

### 16. Agent isolation

Agents are workers. They may propose observations, analyses, or actions through contracts, but cannot grant themselves permissions, change governance state, or bypass policy.

## Repository Structure

Prefer capability-oriented packages over a flat collection of files.

```text
src/orion/
├── kernel/          # orchestration and lifecycle coordination
├── contracts/       # stable public domain contracts
├── understanding/   # system discovery and system models
├── evidence/        # evidence ingestion and provenance
├── learning/        # pattern/model learning
├── knowledge/       # governed reusable knowledge
├── capabilities/    # capability definitions and runtime preparation
├── governance/      # authorization, promotion, demotion, policy
├── shadow/          # replay, simulation, differential evaluation
├── execution/       # governed execution runtime
├── adapters/        # ERP/system/vendor-specific implementations
├── tenancy/         # tenant isolation and context boundaries
└── observability/   # metrics, tracing, structured diagnostics
```

A package should be created when it has a real ownership boundary, not merely to increase directory count.

## API and Contract Evolution

Public contracts must be versioned deliberately. Prefer additive, backward-compatible changes. Breaking changes require a new contract version and migration/deprecation plan.

Never expose internal persistence schemas as stable external contracts.

## Data Discipline

Distinguish:

- raw customer data
- evidence references
- derived observations
- learned patterns
- generalized knowledge
- capability metadata
- governance records

These are not interchangeable and must not share accidental storage semantics.

## Testing Pyramid

```text
                 E2E / production-like
                       ▲
                  Integration
                       ▲
                 Contract tests
                       ▲
                  Unit/property
```

Critical invariants require automated tests. Safety boundaries require negative tests proving prohibited behavior is rejected.

## Observability

Every meaningful workflow receives a correlation/run identifier. Logs are structured and must avoid secrets and unnecessary customer data. Metrics should measure both performance and learning quality.

## Refactoring Rules

Before changing a shared contract:

1. identify consumers;
2. identify persisted representations;
3. identify integration boundaries;
4. add compatibility where practical;
5. migrate incrementally;
6. remove obsolete paths only after validation.

Never solve architectural drift by adding another hidden compatibility layer indefinitely.

## Complexity Budget

Prefer the simplest design that satisfies current requirements while preserving explicit extension points. Do not introduce distributed systems, event buses, vector databases, agent swarms, or elaborate abstractions without a demonstrated requirement.

## 2036 Compatibility Principle

Future capability must be possible without making present code speculative. Extension points should exist at genuine boundaries: contracts, adapters, stores, policy engines, model providers, simulation runtimes, and agent workers.

## Definition of Done for Code

A production-bound component is not complete until it has:

- clear ownership
- explicit dependencies
- stable contracts
- validation of inputs
- deterministic behavior where required
- explicit error behavior
- bounded external operations
- security/tenant boundaries
- observability
- tests for normal and failure paths
- documentation for non-obvious decisions
- migration/versioning consideration
- no known secret leakage

## Anti-Patterns Prohibited by Default

- God classes
- God modules
- circular dependencies
- hidden global mutable state
- hard-coded tenant behavior
- vendor concepts leaking into the Kernel
- direct database access from arbitrary modules
- unbounded retries
- silent exception swallowing
- implicit authorization
- model output treated as authority
- customer data embedded into reusable knowledge
- duplicated sources of truth
- premature microservices
- speculative framework layers

## Final Rule

**Make ORION easy to understand after it becomes large.**

Every architectural decision must reduce the future cost of changing, testing, replacing, or extending the system rather than merely making today's implementation shorter.
