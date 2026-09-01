# ORION v0.1 — Engineering Standards & Evolution Architecture

## Purpose

Establish the engineering rules for building ORION as a durable, testable, modular system that can expand for years without becoming a tangled collection of customer-specific automations.

## North Star

ORION must optimize for **changeability** as aggressively as functionality.

A feature is not complete if it works today but creates unnecessary coupling, hidden state, unclear ownership, or an expensive migration path tomorrow.

## Architectural Laws

### 1. Kernel minimalism

The Kernel orchestrates. It must not become a dumping ground for:

- ERP-specific semantics
- customer rules
- model prompts
- persistence implementations
- provider SDKs
- UI concerns
- credentials
- workflow-specific code

### 2. Stable contracts, replaceable implementations

Interfaces/contracts are the seams between subsystems. Implementations may change without requiring downstream redesign.

Preferred dependency direction:

```text
Domain contracts
      ↑
Application/orchestration
      ↑
Ports
      ↑
Adapters / infrastructure
```

Concrete infrastructure must not leak into domain contracts.

### 3. Dependency inversion

Core code depends on abstractions. External systems depend on adapters. Provider-specific SDKs stay outside the core.

### 4. One responsibility per module

Modules should have one clear reason to change. Avoid giant utility modules, god objects, global managers, and cross-cutting convenience imports.

### 5. Explicit state

Long-lived state must have an identified owner and lifecycle. Avoid mutable module globals and hidden singleton state.

### 6. Immutable evidence

Observations, evidence, evaluation results, and governance events should be append-oriented. Corrections create new records rather than rewriting history.

### 7. Provenance everywhere it matters

Any learned conclusion that can affect automation must be traceable to its evidence, model/system versions, policies, and evaluation history.

### 8. Fail closed at safety boundaries

Missing authorization, missing critical evidence, unresolved critical contradictions, invalid state, or unknown execution scope must not silently degrade into permission to act.

### 9. Idempotency by design

Any operation capable of causing an external side effect must have an explicit idempotency strategy. Retries must not create duplicate business effects.

### 10. Time is explicit

Use timezone-aware UTC timestamps for persisted events. Never depend on local machine time for business logic without an explicit timezone policy.

### 11. Errors are typed and actionable

Avoid broad exception swallowing. Distinguish validation, authorization, dependency, transient, permanent, policy, and invariant failures where useful.

### 12. Deterministic boundaries

Where reproducibility matters, capture versions, configuration, seeds, prompts/context identifiers, and input evidence references sufficient to reconstruct the decision.

## Layered Architecture

```text
                ORION KERNEL
                     │
        ┌────────────┼────────────┐
        ↓            ↓            ↓
   Discovery      Learning     Governance
     Ports          Ports         Ports
        │            │            │
        └────────────┼────────────┘
                     ↓
                  Ports
                     ↓
                 Adapters
                     ↓
       External systems / providers
```

Persistence, AI providers, ERP systems, messaging, and other infrastructure remain replaceable.

## Bounded Contexts

As implementation grows, keep major concerns separately bounded:

- `discovery` — observe and reconstruct systems
- `evidence` — immutable evidence and provenance
- `understanding` — system models and hypotheses
- `learning` — patterns and knowledge promotion inputs
- `shadow` — replay and simulation
- `evaluation` — differential evaluation and drift
- `governance` — confidence, policy, promotion, demotion
- `capabilities` — governed capability definitions
- `execution` — controlled side-effecting runtime
- `adapters` — external-system implementations

Do not create all of these as empty packages prematurely. Introduce a boundary when there is a real responsibility or independent change pressure.

## Data Boundaries

Separate at minimum:

```text
Customer Data
     │ tenant-scoped
     ↓
Evidence / Observations
     ↓
Learned Patterns
     ↓
Generalized Knowledge
```

Customer data must never be copied into generalized knowledge merely because it was useful for learning.

## Configuration and Secrets

- Configuration is externalized and environment-aware.
- Secrets never live in source code, tests, fixtures, logs, or committed configuration.
- Secret values are represented by references/identifiers, not raw values.
- Production credentials are never reused as development fixtures.

## Testing Pyramid

```text
                 E2E
              /      \
          Integration
          /            \
       Contract       Contract
       /                  \
                 Unit tests
```

Prioritize fast deterministic unit tests for domain logic, contract tests at boundaries, integration tests for real adapters, and a small number of high-value end-to-end tests.

## Property and Invariant Testing

For high-value core behavior, test invariants such as:

- Shadow cannot write to production.
- Unauthorized capability cannot execute.
- Tenant boundaries cannot be crossed.
- Evidence identifiers remain stable.
- Ledger history is append-only.
- Retries do not duplicate side effects.

Property-based testing may be introduced where state spaces are large or edge cases are difficult to enumerate.

## Observability

Production-grade components should expose structured logs, metrics, and traces with correlation/run IDs while avoiding sensitive payload leakage.

At minimum, operational events should allow reconstruction of:

```text
request → decision → policy → action → outcome → verification
```

## API and Contract Evolution

Contracts must evolve compatibly where practical. Prefer additive changes, explicit versioning for breaking changes, and migration adapters when necessary.

Never silently reinterpret an existing field's meaning.

## Dependency Hygiene

- Minimize dependencies.
- Pin or bound versions appropriately for reproducibility.
- Prefer mature, maintained libraries.
- Isolate vendor SDKs behind adapters.
- Review dependency security and license implications.

## Concurrency and Reliability

Do not introduce asynchronous/concurrent execution merely for speed. First define ownership, ordering, cancellation, retry, timeout, and idempotency semantics.

All external calls should have explicit timeout and retry policy appropriate to the operation.

## Resource Safety

Bound memory, payload size, recursion depth, execution duration, queue growth, and fan-out where untrusted or customer-controlled inputs can influence them.

## AI/Agent Engineering

AI agents are workers, not authorities.

Agent outputs must pass through deterministic contracts, policy gates, validation, and governance before side effects.

Prompts are implementation artifacts, not business truth. Business rules must be represented in explicit, versioned policy/knowledge structures where appropriate.

## Code Organization Rules

Prefer:

- small cohesive modules
- explicit imports
- typed public interfaces
- dependency injection at boundaries
- pure functions for deterministic transformations
- immutable value objects where practical
- descriptive names
- narrow functions
- explicit return types for public APIs

Avoid:

- circular imports
- magic constants
- hidden I/O
- implicit network calls in domain code
- catch-all exceptions
- duplicated business rules
- giant configuration dictionaries with undocumented semantics
- premature framework abstractions

## Quality Gates

Before merging material code:

1. format/lint passes;
2. type checking is clean where configured;
3. unit tests pass;
4. relevant contract/integration tests pass;
5. security-sensitive changes receive explicit review;
6. public contracts and migrations are documented;
7. observability is adequate;
8. rollback/demotion behavior is defined for side-effecting changes.

## Architecture Decision Records

Material architectural decisions should be recorded as ADRs with:

- context
- decision
- alternatives
- consequences
- migration implications
- status

This prevents institutional knowledge from disappearing into chat history or individual developers' memory.

## Refactoring Rule

Refactor when a boundary becomes painful, not merely because a different pattern looks fashionable. Every refactor should reduce coupling, improve clarity, improve testability, or remove a known risk.

## 2036 Evolution Rule

Design extension points for future capabilities, but do not implement speculative complexity without a concrete requirement.

The architecture must be able to absorb:

- new AI providers
- new agent runtimes
- new learning methods
- new simulation technologies
- new external systems
- multimodal observation
- causal/digital-twin reasoning
- distributed execution
- multi-tenant scale

without making the Kernel dependent on any one of them.

## Definition of Done for Architecture

A subsystem is architecturally healthy when a future engineer can answer quickly:

- What does this own?
- What does it depend on?
- What contracts does it expose?
- Where does state live?
- What can it mutate?
- How is it tested?
- How is it observed?
- How does it fail?
- How is it versioned?
- How can it be replaced?

If these answers are unclear, the subsystem is not ready to scale.
