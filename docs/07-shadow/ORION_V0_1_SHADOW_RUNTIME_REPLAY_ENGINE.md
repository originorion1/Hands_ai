# ORION v0.1 — Shadow Runtime / Replay Engine

## Purpose

Define the runtime that replays observed business situations and evaluates what ORION would have done without producing production side effects.

## Core Rule

**Shadow execution is not production execution.** A Shadow run must be technically incapable of committing customer-system mutations.

## Runtime Pipeline

```text
Observed Event
  ↓
Replay Envelope
  ↓
Context Reconstruction
  ↓
ORION Decision
  ↓
Proposed Action Set
  ↓
Sandbox / Non-write Runtime
  ↓
Expected Outcome
  ↓
Observed/Reference Outcome
  ↓
Differential Evaluation
  ↓
Evidence + Learning Feedback
```

## Replay Envelope

Every replay contains:

- run identifier
- tenant scope
- source event references
- relevant system state snapshot or immutable references
- system-understanding version
- knowledge version
- capability version
- policy version
- model/agent versions
- timestamps
- provenance
- deterministic-seed information where applicable
- safety classification

## Context Reconstruction

The runtime reconstructs the minimum context required to evaluate the candidate workflow. It must avoid unnecessary customer-data duplication and preserve references to source evidence whenever possible.

## Action Isolation

Proposed actions are represented as intents against generic ORION capabilities. They are routed to a Shadow executor that cannot reach production write paths.

```text
ORION Intent
    ↓
Policy Gate
    ↓
Shadow Executor
    ↓
Simulated Side Effects
```

A production connector is not an acceptable Shadow executor.

## Determinism

Where deterministic replay is possible, the same input and version set should reproduce the same decision. Non-deterministic components must record relevant versions, seeds, prompts/context identifiers, and other reproducibility metadata.

## Side-Effect Model

The simulator should model expected side effects such as:

- record creation
- record update
- state transition
- notification
- downstream event
- calculated value
- approval request

No simulated side effect may escape the Shadow boundary.

## Outcome Model

A Shadow run should produce:

- proposed action set
- predicted outcome
- simulated outcome
- reference/human outcome where available
- validation checks
- confidence
- differences
- safety classification
- unresolved uncertainty

## Failure Modes

The runtime fails closed on:

- missing critical evidence
- unresolved critical contradiction
- insufficient context
- unauthorized scope
- attempted production write
- unsafe/unbounded side effect
- unverifiable critical outcome
- replay corruption

## Learning Feedback

Shadow results are evidence. They may trigger:

- pattern refinement
- system-model refinement
- confidence adjustment
- exception discovery
- new hypothesis generation
- automation-candidate reprioritization

A successful Shadow run does not automatically promote a capability.

## Digital-Twin Extension

The contract permits future simulation of state trajectories, counterfactual actions, dependency propagation, and predicted downstream consequences without changing the Kernel contract.

## Acceptance Criteria

1. A candidate workflow can be replayed without production writes.
2. Proposed actions are captured before simulated execution.
3. Relevant context and versions are provenance-linked.
4. Outcomes can be compared against reference behavior.
5. Differences are classified and retained as evidence.
6. Runtime fails closed when safety or evidence requirements are not met.
7. The design remains ERP-neutral.
8. The runtime can evolve toward digital-twin and counterfactual simulation.
