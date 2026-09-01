# ORION v0.1 — Clean Architecture Adoption

## Status

Approved architectural enhancement.

## Objective

Adopt the useful principles of Clean Architecture while adapting them to ORION's autonomous-learning, multi-system, multi-tenant nature. ORION must not become a literal copy of any framework or textbook structure.

## Core Dependency Rule

Dependencies must point toward stable ORION domain contracts. The core must not depend on vendor-specific technologies.

```text
                    ORION CORE
                       ↑
              STABLE DOMAIN CONTRACTS
                       ↑
       ┌───────────────┼────────────────┐
       │               │                │
   ERP Adapters     AI Adapters      Storage
       │               │                │
  ERPNext/Odoo     Claude/etc.     SQL/NoSQL/etc.
  Dynamics/etc.    other models      future stores
```

The arrows represent dependency direction: outer implementations satisfy inner contracts.

## What ORION Adopts

### Dependency inversion

Business rules and use cases depend on abstractions, not concrete ERP, AI, database, or infrastructure implementations.

### Boundary isolation

System understanding, evidence, learning, knowledge, governance, capabilities, shadow execution, and execution remain distinct responsibilities.

### Use-case orientation

Application behavior should be expressed in terms of ORION capabilities and use cases, such as:

- discover system
- reconstruct process
- evaluate pattern
- simulate capability
- compare outcomes
- promote capability
- execute governed capability

These are preferred over vendor-centric service names.

### Framework independence

Frameworks, SDKs, databases, model providers, and ERP clients remain replaceable infrastructure.

### Testability

Core rules should be testable without connecting to an ERP, external model provider, production database, or live customer environment.

## What ORION Does Not Adopt Literally

ORION will not create layers, interfaces, factories, controllers, or abstractions merely because a textbook diagram contains them.

Every abstraction must have a real ownership or change boundary.

Avoid:

- abstraction for abstraction's sake
- factory chains with no variability
- generic service interfaces hiding unrelated behavior
- excessive indirection that makes execution flow hard to follow
- premature microservices

## Multi-System Principle

ORION must treat enterprise platforms as replaceable environments.

```text
                  ORION
                    │
             Generic Contracts
                    │
       ┌────────────┼────────────┐
       ↓            ↓            ↓
   ERPNext       Dynamics      Odoo
   Adapter       Adapter       Adapter
       │            │            │
       └────────────┼────────────┘
                    ↓
            Customer Environment
```

Future systems such as Scope or other ERP/business platforms must be introduced by adding an adapter and mapping their observed behavior into ORION contracts, not by modifying the Kernel into an ERP switchboard.

## AI Provider Independence

The same rule applies to AI systems. Claude, Codex, other model providers, local models, specialist agents, and future reasoning systems are workers behind explicit contracts.

Changing an AI provider must not require rewriting ORION's domain logic.

## Autonomous Learning Boundary

Clean Architecture is extended with an ORION-specific separation between probabilistic intelligence and authority.

```text
Probabilistic systems
      ↓
Proposals / hypotheses
      ↓
Evidence
      ↓
Validation
      ↓
Governance
      ↓
Authority
      ↓
Execution
```

No model, agent, or learning component can authorize itself.

## Data and Knowledge Boundary

Customer data remains tenant-scoped. Generalized knowledge is a separate governed artifact. Infrastructure choices must not blur this distinction.

## Change Isolation Matrix

| Change | Expected impact |
|---|---|
| Replace ERP | Add/replace adapter; core unchanged |
| Replace AI provider | Replace provider adapter; core unchanged |
| Replace database | Replace persistence implementation; domain contracts unchanged |
| Add new agent type | Add worker/adapter; governance unchanged |
| Change business rule | Change governed domain/use-case logic and revalidate affected capabilities |
| Change execution mechanism | Replace execution infrastructure behind contract |
| Add new knowledge backend | Add implementation behind KnowledgeStore contract |

## Definition of Success

A future customer using a different ERP should be able to enter ORION's discovery process without creating a new ORION architecture.

The ideal outcome is:

```text
NEW CUSTOMER
     ↓
DISCOVERY ADAPTER
     ↓
GENERIC EVIDENCE
     ↓
ORION UNDERSTANDING
     ↓
PATTERNS / KNOWLEDGE
     ↓
CAPABILITIES
```

## 2036 Principle

ORION should become more capable as new systems, models, agents, and execution technologies appear, without becoming more vendor-dependent.

The architecture therefore optimizes for **stable meaning and replaceable mechanisms**.
