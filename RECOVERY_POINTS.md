# ORION Recovery Points

This file records stable recovery anchors for the laboratory branch.

## Recovery policy
1. Never rewrite or force-push a recorded stable recovery commit.
2. Before a risky architectural change, create a new recovery entry with the current commit SHA.
3. Keep experimental work isolated on branches until validated.
4. A recovery point is not declared stable merely because code was committed; record its verification state.

## Stable anchors

| Anchor | Commit | State | Verification |
|---|---|---|---|
| R0 | `9135cc8355301dfa8b7ccb26cd4350462545ab5c` | Initial integrated laboratory anchor | CI workflow added; execution not observed |
| R1 | `60243bbeead78c7acb572b98b487480582d90149` | Recovery ledger established | Repository state recorded |
| R2 | `ce366b94e177b14b85564f437931264448235324` | Pre-fix discovery baseline | Python 3.12 suite: 19 passed, 1 failed (`EvidenceKind` contract mismatch) |
| R3 | `bb81661c9d4430ad7e0448070dd33ab90d145bb4` | Discovery contract repaired | Python 3.12 suite: 20 passed |
| R4 | `bdfbf00e9a85aaa1c0795f32c958539bd581e8f9` | Mock ERPNext shadow-only vertical slice | Editable package install succeeded; Python 3.12 suite: 21 passed; local demo verified no execution authority |

## Current working anchor
- Branch: `laboratory/orion-v0.1`
- Current recovery baseline: `bdfbf00e9a85aaa1c0795f32c958539bd581e8f9`
- Safety state: shadow-only; external mutation disabled

## Required practice
Before each risky change, record the current commit here. After tests/verification demonstrate a stable state, add a new `R#` anchor. Never delete historical anchors.
