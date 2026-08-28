# ORION v0.1 Architecture Contract

## Purpose

ORION v0.1 is a production-oriented prototype whose first measurable objective is to eliminate the first level of repetitive human data entry in a real organization. The initial external environment is ERPNext.

The prototype must learn the organization rather than encode the organization's rules by hand.

## Architectural principles

1. ORION Core remains independent of ERPNext and any specific model provider.
2. Customer operational data remains customer-scoped and is not global knowledge.
3. Agents reason through contracts and capabilities; they do not directly own infrastructure or arbitrary credentials.
4. Intelligence, authority, execution, and verification remain separate.
5. Evidence precedes durable knowledge; provenance is mandatory for important conclusions.
6. Generalized knowledge may be reused only when it is legitimately generalizable and permitted by governance, privacy, contract, and law.
7. Prior knowledge accelerates discovery but never replaces local validation.
8. Interfaces/ports are preferred at external boundaries so implementations can change without rewriting the domain.
9. Events and idempotency are first-class concerns so retries, replay, and future distributed execution do not create duplicate effects.
10. Versioned contracts, schemas, policies, capabilities, prompts/model configuration, and knowledge states are required for auditability and evolution.
11. The v0.1 implementation should favor reusable learning machinery over customer-specific hard-coded business logic.
12. Write/execution authority is initially narrow and graduated; read/discovery can be broad within the customer's authorization boundary.

## Stable conceptual boundaries

### Core

- identity
- epistemic state
- provenance
- policy/authority concepts
- events
- domain contracts

Core must not depend on ERPNext SDKs, LLM SDKs, vector databases, or deployment-specific infrastructure.

### Services

- discovery
- knowledge
- pattern analysis
- organizational/world model
- research
- capability registry

Services implement ORION behavior behind stable contracts.

### Agents

Agents are replaceable workers registered with the runtime. Initial agents:

- Discovery Agent: studies system structure, metadata, workflows, permissions, APIs, and authorized source code.
- Organizational Analyst: studies customer operations, entities, transactions, workflows, and patterns.
- Research Agent: resolves unknowns using authoritative external sources.

Future agents must not require kernel redesign.

### Adapters

External implementations live behind ports/adapters:

- ERPNext adapter
- future ERP/system adapters
- model providers/router
- storage/search implementations

No agent should contain ERPNext-specific assumptions when a generic contract is sufficient.

### Hands

Hands are controlled execution/perception adapters. They are the only layer permitted to perform privileged external effects. A capability request must pass policy/authority before a Hand executes it. Execution result is not considered success until verified.

## Knowledge and data separation

The following are distinct concepts:

1. Customer operational data — authoritative data in the customer's systems.
2. Observation — ORION's record that something was observed.
3. Evidence — source material supporting an interpretation.
4. Hypothesis — a provisional interpretation.
5. Customer knowledge — validated knowledge specific to one organization.
6. Industry/general knowledge — legitimately reusable generalized knowledge.
7. Capability knowledge — knowledge of how an operation can be performed, including inputs, authority, risk, and verification.

Customer-specific identities, transactions, prices, contracts, policies, credentials, and commercially sensitive facts remain customer-scoped by default.

## Agent contract

An agent should have, at minimum:

- stable identifier and version
- declared purpose
- allowed knowledge scopes
- allowed capabilities
- model policy
- input/event types
- output/event types
- timeout/retry policy
- provenance requirements
- observability metadata

Agents propose observations, hypotheses, knowledge updates, investigations, or capability requests. They do not bypass policy by directly invoking privileged infrastructure.

## Model abstraction

Model use must pass through a provider-neutral interface/router. ORION must be able to use different reasoning models without embedding provider SDK calls throughout the codebase. Model selection is configuration/policy, not domain logic.

## Knowledge contract

Knowledge operations should support at least:

- observe
- record evidence
- propose hypothesis
- retrieve
- validate
- supersede/invalidate
- generalize
- scope by tenant/customer/industry/global

Important knowledge must retain provenance, confidence/epistemic status, timestamps, version information, and supporting evidence references.

## Capability contract

Every capability should declare:

- identifier/version
- purpose
- input schema
- output schema
- risk level
- required authority
- allowed scope
- implementation/Hand
- verification strategy
- idempotency behavior

Read capabilities are separate from write capabilities.

## Event contract

Events should be immutable records with:

- event id
- event type/version
- tenant/customer scope
- timestamp
- producer
- correlation id
- causation id when applicable
- payload reference
- provenance

Consumers must be designed for idempotent processing.

## v0.1 learning loop

```text
External system
  -> Discovery/Observation
  -> Evidence
  -> Agent analysis
  -> Pattern detection
  -> Hypothesis
  -> Validation
  -> Customer knowledge / generalized knowledge
  -> Organizational model
  -> Unknowns
  -> Investigation
  -> Repeat
```

## v0.1 business loop

```text
Human/source data
  -> ORION intake
  -> understand
  -> extract
  -> normalize to canonical representation
  -> map to customer system
  -> validate
  -> policy/authority check
  -> draft or controlled execution
  -> verify
  -> learn
```

The first production KPI is reduction in repetitive human data-entry effort, not agent count or model sophistication.

## Canonical representation

ORION should reason over semantic/domain representations rather than ERPNext field structures. ERPNext mapping belongs in the adapter layer. This prevents the first connector from becoming the internal ontology of ORION.

## Dependency direction

```text
Core <- Services <- Agents/Adapters/Infrastructure
```

Core contracts must not import infrastructure implementations. Dependency inversion is mandatory at external boundaries.

## Prototype discipline

Do not prematurely build:

- a large UI
- dozens of agents
- unrestricted autonomous execution
- customer-specific rule engines
- a provider-specific architecture
- distributed infrastructure without demonstrated need

Build the smallest implementation that satisfies these contracts while leaving replacement points explicit.

## Graduation path

```text
Read
 -> Analyze
 -> Recommend
 -> Simulate
 -> Human-approved draft
 -> Narrow controlled execution
 -> Verify
 -> Expand authority based on evidence
```

No capability graduates merely because an agent requests it. Authority is explicit and independently governed.
