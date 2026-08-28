# ORION v0.1 — Runnable Vertical Slice

## Current Goal

Get a small, testable ORION loop running before expanding the platform.

## Implemented Loop

```text
Authorized Discovery Adapter
        ↓
Observation
        ↓
Evidence Store
        ↓
System Graph Projection
        ↓
Prototype Report
```

The current implementation is intentionally read-only. It does not write to an external customer system.

## Why This Slice First

It proves the architectural seam between external observation, evidence storage, and system understanding. Once this seam is stable, real adapters can be attached without moving customer-specific logic into the Kernel.

## Current Files

- `src/orion/prototype.py` — composition of the first vertical slice
- `tests/test_prototype.py` — smoke/invariant tests
- existing contracts, Kernel, evidence store, System Graph, inference, and validation layers

## Current Limitation

The included `StaticObservationAdapter` is a deterministic laboratory adapter. It is a test/demo surface, not a customer integration and not a claim that ORION is already live against an ERP.

## Next Delivery Target

Build the first real authorized discovery adapter for the selected customer environment, starting read-only and bounded. It should collect heterogeneous evidence without making the core ORION model ERP-specific.

## Definition of Progress

A real customer adapter is considered ready for the next stage when ORION can observe a bounded workflow, retain provenance, project its structure, identify uncertainty, and produce a reviewable understanding without external writes.
