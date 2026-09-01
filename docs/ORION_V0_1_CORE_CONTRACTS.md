# ORION v0.1 Core Contracts

Status: Laboratory — Step 2.3
Authority: Derived from `ORION_V0_1_ARCHITECTURE_CONTRACT.md`

## Purpose

Define the smallest stable domain contracts that implementation code must depend on. These contracts are intentionally provider-, ERP-, database-, and deployment-neutral.

## 1. Identity

Every durable ORION object MUST have a stable identifier and explicit scope.

Required concepts:

- `id`
- `type`
- `version`
- `tenant_id` where applicable
- `scope`
- lifecycle/status where applicable

Scopes MUST distinguish at least:

- global
- industry
- tenant/customer
- execution instance

## 2. Evidence

Evidence is the foundation for durable knowledge.

Minimum contract:

- evidence id/version
- source reference
- source type
- observed timestamp
- tenant/customer scope
- content or content reference
- integrity/reference metadata
- provenance

Evidence MUST NOT silently become knowledge.

## 3. Observation

An observation records what ORION observed, without requiring an interpretation.

Minimum contract:

- observation id
- event/source reference
- observed object/type
- observed timestamp
- scope
- evidence references
- producer/version

Observations SHOULD be immutable.

## 4. Hypothesis

A hypothesis is a provisional interpretation.

Minimum contract:

- hypothesis id/version
- claim
- scope
- evidence references
- confidence/epistemic status
- created/updated timestamps
- proposer
- validation state

A hypothesis MUST be distinguishable from validated knowledge.

## 5. Knowledge

Knowledge is a durable, scoped conclusion supported by evidence.

Minimum contract:

- knowledge id/version
- claim/semantic representation
- scope
- epistemic status
- confidence where meaningful
- evidence references
- provenance
- lifecycle status
- supersedes/superseded-by relationships where applicable
- created/updated timestamps

Customer-specific knowledge MUST remain customer-scoped by default.

## 6. Agent

Agents are replaceable workers, not privileged infrastructure owners.

Minimum contract:

- agent id/version
- purpose
- declared input/event types
- declared output/event types
- allowed knowledge scopes
- allowed capabilities
- model policy reference
- timeout/retry policy
- provenance requirements
- observability metadata

An agent MAY propose a capability request but MUST NOT bypass policy or invoke privileged infrastructure directly.

## 7. Model

Models are accessed through a provider-neutral contract.

The domain MUST NOT depend on an individual model vendor SDK.

Minimum concepts:

- model identifier/version
- capability profile
- request context
- structured input/output
- usage metadata
- provenance/reference
- failure classification

Provider routing belongs outside the core.

## 8. Capability

A capability describes an operation ORION may request.

Minimum contract:

- capability id/version
- purpose
- input schema reference
- output schema reference
- risk level
- required authority
- allowed scope
- implementation reference
- verification strategy
- idempotency semantics

Read and write capabilities MUST remain distinguishable.

## 9. Authorization / Policy

Authority is separate from intelligence.

A policy decision MUST identify:

- subject/agent
- requested capability
- scope
- authority context
- decision
- policy/version
- timestamp
- reason/reference

A capability request without an applicable authorization decision MUST NOT execute a privileged Hand.

## 10. Hand

A Hand is an external-effect adapter.

Minimum contract:

- hand id/version
- supported capability versions
- input/output schemas
- execution context
- idempotency behavior
- result status
- external reference
- verification result/reference
- provenance

A Hand MUST NOT determine its own authority.

## 11. Event

Events are immutable facts about something that occurred.

Minimum contract:

- event id
- event type/version
- tenant/customer scope
- timestamp
- producer/version
- correlation id
- causation id when applicable
- payload/reference
- provenance

Consumers MUST tolerate retries and duplicate delivery.

## 12. Canonical Representation

ORION reasoning MUST use semantic representations independent of ERP-specific schemas.

External adapters translate between canonical representations and external system representations.

The canonical model MUST NOT encode ERPNext-specific field names merely because ERPNext is the first connector.

## 13. Invariants

The following are non-negotiable:

1. Customer data is not global knowledge.
2. Evidence precedes durable knowledge.
3. Knowledge retains provenance.
4. Prior knowledge does not replace local validation.
5. Intelligence does not imply authority.
6. Capability access does not imply execution authority.
7. Execution is incomplete until verified.
8. External effects are idempotent or explicitly non-idempotent with compensating controls.
9. Core contracts do not import infrastructure implementations.
10. Provider changes must not require domain redesign.
11. ERPNext changes must not require domain redesign.
12. Versioned contracts are required for durable cross-component communication.

## 14. Implementation Rule

These contracts define boundaries, not premature implementation technology. Implementations MUST remain replaceable behind these contracts.

The next implementation step may choose concrete language and infrastructure, but any choice that violates these boundaries requires an explicit architecture review and ledger entry.
