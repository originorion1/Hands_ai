# ORION v0.1 System Understanding Principles

**Status:** Approved baseline
**Phase:** Laboratory — Step 3.2
**Architectural horizon:** 2036

## 1. Primary objective

ORION is built to understand business systems, not merely integrate with their APIs. An API is one observation/action surface among many and must never become ORION's canonical representation of a system.

## 2. Understanding model

ORION should progressively construct a system understanding from multiple evidence sources:

```text
Source code / metadata / configuration / documentation / APIs / UI / events / logs / observed outcomes
                                  ↓
                              Discovery
                                  ↓
                          Evidence + observations
                                  ↓
                         Canonical system model
                                  ↓
                        Hypotheses / relationships
                                  ↓
                           Pattern learning
                                  ↓
                       Validated understanding
```

No single source is assumed to be the complete truth.

## 3. Discovery surfaces

A system adapter may expose any combination of:

- identity and version
- schema and metadata
- entities and relationships
- workflows and state transitions
- permissions and authority boundaries
- APIs and available operations
- source-code structure where legitimately available
- configuration
- UI structure and behavior where legitimately observable
- events and logs where authorized
- operational outcomes
- documentation

Adapters translate these observations into ORION's canonical discovery contracts.

## 4. API independence

The core MUST NOT model the business domain as endpoint paths, request payloads, response JSON, or vendor-specific API objects.

API-specific objects belong inside the relevant adapter boundary.

For example, an ERPNext endpoint may reveal a Purchase Invoice, but ORION's understanding should represent the underlying business concept, relationships, constraints, workflow, and behavior independently of ERPNext's API vocabulary.

## 5. Source-code understanding

Where source code is legally and technically available to ORION, it may be analyzed as evidence of declared system behavior. Source analysis must be cross-checked against configuration and observed runtime behavior before high-confidence conclusions are promoted.

Source code must never be treated as proof that production behavior exactly matches the declared implementation.

## 6. Canonical system representation

ORION's system model SHOULD represent at least:

- system identity
- capabilities
- entities
- attributes
- relationships
- operations
- state machines/workflows
- business rules
- permissions
- dependencies
- integrations
- configuration facts
- observations
- evidence links
- confidence
- version and temporal validity

## 7. Learning versus integration

Integration answers:

> "How can I call this system?"

Understanding answers:

> "What is this system, how does it behave, why does it behave this way, what business concepts does it implement, and how do people operate it?"

ORION requires the second. Integration mechanisms support it but do not replace it.

## 8. Organizational understanding

System understanding must eventually be combined with organizational understanding. ORION should distinguish:

- what the software permits;
- what the organization configures;
- what policy requires;
- what users actually do;
- what repeatedly happens in practice.

This distinction is essential for finding first-level human data-entry work.

## 9. Evidence hierarchy

A candidate understanding may be supported by multiple evidence types. ORION should preserve the source, timestamp, scope, provenance, and confidence of each observation and avoid silently collapsing contradictory evidence.

Contradictions become learning/review signals rather than being overwritten.

## 10. ERPNext boundary

ERPNext is the first target environment and Adapter #1. It is a laboratory for validating ORION's generic discovery and learning architecture.

ERPNext concepts must not become ORION's universal domain model. ERPNext-specific implementation belongs below the adapter boundary.

## 11. Safety boundary

Discovery and understanding are distinct from execution authority. Learning that an operation exists does not grant permission to execute it.

```text
understand capability
        ≠
authorize capability
```

Execution remains governed by the Kernel, policy, capability registry, and Hands.

## 12. Success criterion

The first meaningful success is not "ORION can call ERPNext." It is:

> ORION can inspect and observe a real business environment, construct a defensible model of its structure and behavior, identify recurring patterns, explain the evidence behind its conclusions, and identify candidate repetitive human data-entry work without requiring humans to manually encode the system's architecture.

## 13. 2036 evolution

The discovery architecture must remain open to future systems that expose radically different interfaces, including APIs, event streams, GUIs, source repositories, local applications, agent protocols, files, multimodal interfaces, and systems not designed for machine discovery.

The canonical understanding layer must therefore remain independent of any particular transport, vendor, ERP, model provider, or interface technology.
