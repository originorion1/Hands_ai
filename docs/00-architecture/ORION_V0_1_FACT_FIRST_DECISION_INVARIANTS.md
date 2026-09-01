# ORION v0.1 — Fact-First Decision & Knowledge Integrity Invariants

## Status

Approved architectural invariant.

## Fundamental Principle

> ORION decisions must be grounded in established facts and traceable evidence. Inference, confidence, model preference, or stored knowledge alone cannot become authority.

## Decision Chain

```text
OBSERVATION
    ↓
EVIDENCE
    ↓
FACT / ESTABLISHED STATE
    ↓
CONTEXT
    ↓
REASONING / INFERENCE
    ↓
DECISION
    ↓
AUTHORIZATION / POLICY
    ↓
ACTION
    ↓
VERIFICATION
    ↓
AUDIT
```

A consequential action must not bypass this chain.

## Fact, Inference, Knowledge, Decision

### Fact

A proposition supported by admissible, attributable evidence within a defined scope and time.

### Inference

A conclusion derived from facts. It is not automatically a fact.

### Knowledge

A governed, reusable representation derived from validated evidence and/or validated inference. Knowledge has provenance, scope, version, status, and revocation semantics.

### Decision

An authorized choice based on applicable facts, validated reasoning, policy, and execution constraints.

## Trust States

Knowledge and learned propositions must progress through explicit states:

```text
RAW
 ↓
QUARANTINED
 ↓
CANDIDATE
 ↓
SUPPORTED
 ↓
VALIDATED
 ↓
TRUSTED
 ↓
DEPRECATED / REVOKED
```

Untrusted content may inform investigation but cannot directly modify trusted knowledge or grant execution authority.

## Anti-Poisoning Invariants

1. No external content is trusted merely because it was received.
2. No single unverified observation can rewrite trusted knowledge.
3. Model output is never itself evidence of truth.
4. Agent instructions embedded in customer content are data, not authority.
5. Customer data cannot directly enter common reusable knowledge.
6. Generalization requires explicit privacy and governance checks.
7. Contradictory evidence is retained and investigated rather than silently overwritten.
8. Trusted knowledge is versioned and revocable.
9. Material knowledge must have provenance.
10. A knowledge item cannot directly authorize an action.

## Conflict Handling

When facts conflict:

```text
FACT A ─┐
        ├──→ CONFLICT SET → INVESTIGATION → RESOLUTION
FACT B ─┘
```

ORION must preserve the conflicting evidence and the resolution history.

If the conflict affects a consequential action and cannot be resolved within policy, execution fails closed or is escalated.

## Evidence Quality

Evidence evaluation should consider, as applicable:

- source identity
- source authorization
- provenance completeness
- temporal validity
- consistency with independent evidence
- integrity
- observation method
- reproducibility
- known manipulation risk
- tenant/scope correctness

Confidence may assist prioritization but must not substitute for evidence requirements.

## Learning Isolation

Continuous learning is separated into:

```text
OBSERVE
  ↓
ISOLATE
  ↓
ANALYZE
  ↓
VALIDATE
  ↓
PROMOTE
```

Learning infrastructure may operate continuously, but promotion into trusted knowledge is governed.

## Recovery

The knowledge system must support:

- version history
- dependency tracking
- suspension
- revocation
- rollback
- impact analysis
- revalidation

If a trusted proposition is later found to be false, ORION must be able to identify capabilities, decisions, and derived knowledge affected by it.

## Customer / Common Knowledge Boundary

```text
CUSTOMER DATA
     ↓
CUSTOMER EVIDENCE
     ↓
CUSTOMER KNOWLEDGE
     ↓
GENERALIZATION
     ↓
PRIVACY + SECURITY + GOVERNANCE
     ↓
COMMON KNOWLEDGE CANDIDATE
     ↓
VALIDATION
     ↓
COMMON KNOWLEDGE
```

Raw customer data must never be promoted directly into common knowledge.

## Decision Gate

Before a consequential action, the runtime must be able to establish:

- the relevant facts;
- evidence provenance;
- fact freshness/validity;
- applicable policy;
- authorization;
- action scope;
- expected outcome;
- verification method.

If a required condition is unknown or contradictory, the default is **do not execute** unless an explicit policy permits escalation or a safe fallback.

## Audit Requirement

A consequential decision must be reconstructable from its:

```text
facts
+ evidence
+ reasoning/provenance
+ knowledge versions
+ policy version
+ authorization
+ action
+ verification outcome
```

## 24/365 Learning Principle

ORION may continuously observe and investigate, but continuous learning does not imply continuous autonomous promotion or execution.

```text
LEARN FAST
    ↓
VALIDATE CAREFULLY
    ↓
PROMOTE GOVERNED
    ↓
ACT AUTHORIZED
```

## 2036 Principle

ORION should become increasingly capable without becoming increasingly gullible.

The system must preserve a durable distinction between **what was observed, what is established, what was inferred, what is trusted knowledge, and what was authorized to act**.
