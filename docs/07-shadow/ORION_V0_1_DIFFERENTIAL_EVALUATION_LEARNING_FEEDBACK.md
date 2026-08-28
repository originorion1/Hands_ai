# ORION v0.1 — Differential Evaluation & Learning Feedback Engine

## Purpose

Convert Shadow-mode comparisons into structured evidence that can improve ORION's understanding, patterns, confidence, and automation candidates without conflating success with truth.

## Core Principle

A Shadow result is **evidence**, not authority. Every evaluation must preserve the distinction between observed behavior, ORION's prediction, validated equivalence, unresolved variance, and safety status.

## Evaluation Flow

```text
Human/reference path
        │
        ├──────────────┐
        ↓              ↓
   Reference      ORION Shadow
    outcome          outcome
        │              │
        └──────┬───────┘
               ↓
        Differential Engine
               ↓
        Outcome Classification
               ↓
        Evidence + Feedback
               ↓
   ┌───────────┼────────────┐
   ↓           ↓            ↓
Model update Pattern update Opportunity update
```

## Outcome Classifications

- `EXACT_MATCH` — same relevant action and outcome.
- `EQUIVALENT_OUTCOME` — materially different path with an equivalent valid business result.
- `ACCEPTABLE_VARIATION` — difference explicitly supported by policy or validated business semantics.
- `MATERIAL_VARIANCE` — meaningful unexplained deviation.
- `INCORRECT` — outcome violates the validated target or business rule.
- `UNSAFE` — potential unacceptable consequence or policy violation.
- `INDETERMINATE` — insufficient evidence to classify reliably.

## Comparison Dimensions

The engine should compare, where applicable:

- trigger interpretation
- entities and records selected
- fields and values
- transformations
- validation decisions
- workflow/state transitions
- downstream effects
- timing/order dependencies
- exceptions
- business outcome
- verification results

Comparison must be semantic where possible; literal equality is not required when two paths are demonstrably equivalent.

## Evidence Record

Every evaluation produces a provenance-linked record containing:

- evaluation ID
- replay/run ID
- tenant scope
- source evidence references
- system-understanding version
- pattern/knowledge versions
- capability version
- policy version
- model/agent versions
- reference outcome
- ORION outcome
- comparison dimensions
- classification
- confidence before/after
- contradictions discovered
- learning recommendation
- reviewer/validator information when applicable
- timestamps

## Learning Actions

An evaluation may recommend one or more of:

```text
REFINE_PATTERN
REFINE_SYSTEM_MODEL
CREATE_PATTERN
CREATE_EXCEPTION
LOWER_CONFIDENCE
RAISE_CONFIDENCE
REQUIRE_MORE_EVIDENCE
RE-RANK_OPPORTUNITY
BLOCK_CAPABILITY
NO_CHANGE
```

The engine must not directly promote a capability. Promotion remains the responsibility of the Confidence & Promotion Engine and governance policy.

## Confidence Updates

Confidence changes must account for:

- evidence strength
- evidence independence
- coverage
- scenario diversity
- temporal stability
- exception rate
- contradiction severity
- verification quality

Repeated observations from the same narrow scenario must not create artificial confidence.

## Exception Learning

Material differences should be retained as first-class evidence. ORION should determine whether a difference represents:

- an expected exception
- an undiscovered business rule
- a missing dependency
- a system-model error
- a pattern boundary
- data-quality behavior
- an unsafe condition
- an unknown condition

## Drift Detection

The engine should detect degradation over time and trigger investigation or demotion when:

- match rates decline
- exception distributions change
- system configuration changes
- workflow behavior changes
- validation rules change
- dependencies change
- knowledge becomes stale

## Learning Safety

A failed or unsafe result must not become positive training evidence. Feedback must be labeled according to its evidentiary meaning before entering any learning process.

## Cross-Tenant Knowledge Boundary

Customer data, identifiers, documents, transactions, and private operational details remain tenant-scoped. Only approved generalized knowledge may cross tenant boundaries through the Knowledge Governance layer.

## Acceptance Criteria

1. Every Shadow result can be compared against a reference outcome where available.
2. Equivalent outcomes can be recognized without requiring identical execution paths.
3. Variances are classified explicitly.
4. Every material conclusion has provenance.
5. Feedback can update patterns and system understanding without directly authorizing execution.
6. Confidence updates are multidimensional and resistant to narrow-sample inflation.
7. Exceptions and contradictions are retained as learning evidence.
8. Drift can trigger investigation or capability demotion.
9. Customer data remains isolated from generalized knowledge.
10. The design remains ERP-neutral and compatible with future counterfactual/digital-twin evaluation.
