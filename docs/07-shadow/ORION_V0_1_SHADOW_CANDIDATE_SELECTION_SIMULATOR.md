# ORION v0.1 — Shadow Candidate Selection & Simulator

## Purpose

Define the safe transition from a ranked automation opportunity to a reproducible Shadow candidate. Shadow execution must demonstrate what ORION would do without mutating the customer's production system.

## Core Principle

**Simulation is evidence generation, not execution authority.**

A candidate cannot enter Shadow merely because it has a high score. It must satisfy explicit understanding, evidence, risk, reversibility, and verification gates.

## Pipeline

```text
Ranked Opportunity
      ↓
Candidate Eligibility Gate
      ↓
Execution Specification
      ↓
Input Snapshot / Replay Envelope
      ↓
Deterministic Simulation
      ↓
Proposed Action Set
      ↓
Expected Outcome
      ↓
Comparison with Actual Outcome
      ↓
Shadow Result
      ↓
Learning + Validation
```

## Candidate Eligibility

A Shadow candidate should have:

- sufficiently understood trigger and process;
- identified inputs and outputs;
- adequate provenance for material decisions;
- known or bounded exception behavior;
- independently checkable expected outcomes;
- acceptable data-sensitivity classification;
- bounded and reversible proposed effects;
- an explicit authorized observation boundary;
- no unresolved critical contradiction.

## Simulation Boundary

The simulator must not call production write operations. It operates on a controlled representation of the observed state and produces a proposed action set.

```text
Production Read / Authorized Observation
                ↓
          Replay Envelope
                ↓
             Simulator
                ↓
       Proposed Action Set
                ↓
          Expected State
```

## Replay Envelope

A replay envelope contains the minimum information required to reproduce the decision while respecting tenant isolation and data-minimization requirements:

- trigger/event identity;
- relevant input observations;
- referenced entities;
- relevant configuration snapshot;
- applicable policy/rule versions;
- model/agent version;
- knowledge references;
- provenance references;
- timestamp/version context.

Sensitive customer data must not be copied into generalized knowledge merely because it was used in a replay.

## Proposed Action Set

Every simulated action must be represented generically:

- target capability;
- target entity/resource;
- intended state transition;
- parameters or derived values;
- preconditions;
- validation checks;
- expected side effects;
- rollback/recovery expectation;
- confidence;
- evidence references.

The representation must remain independent of ERPNext.

## Shadow Result Classes

```text
EXACT_MATCH
ACCEPTABLE_VARIATION
MINOR_DIFFERENCE
MATERIAL_DIFFERENCE
UNSAFE
UNKNOWN
```

A result is never reduced to a binary pass/fail because meaningful learning can occur from controlled differences.

## Comparison

Compare ORION's proposed outcome against the observed human/system outcome at multiple levels:

1. trigger equivalence;
2. entity selection;
3. field/value selection;
4. rule application;
5. workflow/state transition;
6. validation decisions;
7. side-effect expectations;
8. final business outcome.

## Safety Gates

A Shadow candidate must fail closed when:

- required evidence is missing;
- a critical contradiction is unresolved;
- the simulation depends on unauthorized access;
- the proposed effect is not bounded;
- the expected outcome cannot be independently checked;
- data isolation cannot be demonstrated;
- confidence falls below the process-specific threshold.

## Learning Feedback

Shadow results feed the learning loop:

```text
Shadow Difference
      ↓
Root-cause classification
      ↓
New observation / hypothesis
      ↓
System Understanding update
      ↓
Pattern update
      ↓
Opportunity re-score
```

The system should learn from both matches and mismatches.

## Promotion Boundary

Shadow success does not itself grant production authority. Promotion requires a separate governed authorization process and an explicit capability/policy binding.

```text
SHADOW
  ≠
PRODUCTION EXECUTION
```

## 2036 Engineering Direction

The simulator contract should support future digital twins, deterministic replay, probabilistic simulation, causal experimentation, counterfactual analysis, multimodal interaction replay, and increasingly autonomous validation without redesigning the Kernel.

## Acceptance Criteria

ORION v0.1 should be able to:

1. select an eligible opportunity using explicit gates;
2. construct a replay envelope from authorized evidence;
3. simulate the proposed workflow without production writes;
4. produce a complete generic action set and expected outcome;
5. compare proposed and actual outcomes;
6. classify differences;
7. preserve provenance and tenant isolation;
8. feed mismatches back into learning;
9. fail closed on critical uncertainty or safety violations;
10. keep Shadow separate from execution authority.
