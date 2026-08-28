# ORION Recovery Points

This file records stable recovery anchors for the laboratory branch.

## Current stable anchor
- Branch: `laboratory/orion-v0.1`
- Recovery commit: `9135cc8355301dfa8b7ccb26cd4350462545ab5c`
- PR: #1 (`ORION v0.1 prototype vertical slice`)
- Safety state: shadow-only; external mutation disabled
- Purpose: recover the latest known integrated prototype before subsequent changes

## Recovery policy
1. Never rewrite or force-push a recorded stable recovery commit.
2. Before a risky architectural change, create a new recovery entry with the current commit SHA.
3. Keep experimental work isolated on branches until validated.
4. A recovery point is not declared stable merely because code was committed; it should have a known test/verification status.
5. Record the reason, scope, and verification state for every future stable anchor.

## Verification ledger
| Anchor | Status | Verification |
|---|---|---|
| `9135cc8355301dfa8b7ccb26cd4350462545ab5c` | stable laboratory anchor | CI workflow added; workflow execution not yet observed |
