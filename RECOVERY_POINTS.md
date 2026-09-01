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

## 2026-09-01 — Python quality baseline verified

- Branch: `laboratory/orion-v0.1`
- Commit: `0443898` — `chore: enforce Python quality baseline`
- Verification:
  - `ruff check .` — passed
  - `pytest -q` — 23 passed
  - `git diff --check` — passed
  - `python -m orion.demo` — completed successfully
  - shadow safety preserved: `execution_allowed=false`
- Scope:
  - Python/Ruff modernization
  - import/type cleanup
  - local Python artifacts added to `.gitignore`
  - no intended architectural change
  - malformed ERPNext `data` type now raises `TypeError`

## 2026-09-01 — Tenant and assurance boundaries verified

- Branch: `laboratory/orion-v0.1`
- Commit: `7ce6589` — `fix: preserve assurance across knowledge promotion`
- Includes:
  - `553b77e` — tenant isolation enforced during discovery
  - `7ce6589` — validation assurance preserved across knowledge promotion
- Verification:
  - `ruff check .` — passed
  - `pytest -q` — 30 passed
  - `python -m orion.demo` — completed successfully
  - `git diff --check` — passed
  - shadow safety preserved: `execution_allowed=false`
- Safety invariants:
  - mixed/unscoped tenant discovery batches produce zero evidence writes
  - validation decisions are cryptographically/id-bound to their hypothesis identity
  - promoted knowledge retains explicit assurance
  - promotion requires provenance
  - validated knowledge does not grant execution authority

## 2026-09-01 — Common knowledge isolation verified

- Branch: `laboratory/orion-v0.1`
- Commit: `8e66459` — `fix: block implicit common knowledge promotion`
- Verification:
  - `ruff check .` — passed
  - `pytest -q` — 33 passed
  - `python -m orion.demo` — completed successfully
  - `git diff --check` — passed
  - shadow safety preserved: `execution_allowed=false`
- Safety invariants:
  - customer knowledge remains tenant-scoped
  - direct customer-to-common promotion is forbidden
  - common knowledge retrieval remains disabled until explicit generalization exists
  - cross-company reuse requires a future governed generalization workflow

## 2026-09-01 — Structural shadow execution safety verified

- Branch: `laboratory/orion-v0.1`
- Commit: `d29922e` — `fix: enforce structural shadow-only execution safety`
- Verification:
  - `ruff check .` — passed
  - `pytest -q` — 38 passed
  - `python -m orion.demo` — completed successfully
  - `git diff --check` — passed
  - shadow safety preserved: `execution_allowed=false`
- Safety invariants:
  - shadow decisions cannot be constructed with execution authority
  - common or unvalidated knowledge cannot drive shadow proposals
  - prototype runs require an explicit tenant
  - mixed-tenant batches are rejected before evidence or graph mutation

## 2026-09-01 — ERPNext read-only discovery boundary hardened

- Branch: `laboratory/orion-v0.1`
- Commit: `05e8dec` — `fix: harden ERPNext read-only discovery boundary`
- Verification:
  - `ruff check .` — passed
  - `pytest -q` — 55 passed
  - `python -m orion.demo` — completed successfully
  - `git diff --check` — passed
  - shadow safety preserved: `execution_allowed=false`
- ERPNext safety invariants:
  - HTTPS-only ERP origin
  - GET-only discovery requests
  - redirects rejected
  - tenant, API key, and API secret must be explicitly configured
  - resource names are constrained
  - response size is bounded
  - pagination is bounded and incomplete discovery fails closed
  - malformed API responses fail closed
  - network failures fail closed
  - no ERP write capability is exposed by the discovery adapter

## 2026-09-01 — First live ERPNext read-only probe verified

- Live ERPNext connectivity verified using dedicated API credentials.
- Probe scope: one low-volume metadata/master resource.
- No customer record contents were written to logs or repository.
- Verification:
  - live HTTPS request succeeded
  - observations returned successfully
  - every observation remained `READ_ONLY`
  - every evidence object remained tenant-bound
  - no execution path was invoked
  - no ERP mutation was attempted
- Live credentials and customer endpoint remain outside Git.

## 2026-09-01 — Permission-aware live ERPNext metadata discovery verified

- Branch: `laboratory/orion-v0.1`
- Commit: `0ed7977` — `feat: add permission-aware ERPNext metadata discovery`
- Verification:
  - `python -m py_compile` — passed
  - `ruff check .` — passed
  - `pytest -q` — 67 passed
  - `python -m orion.demo` — completed successfully
  - `execution_allowed=false`
  - `git diff --check` — passed
- Live ERPNext verification:
  - dedicated metadata adapter successfully queried an explicitly authorized `Company` DocType
  - metadata request used HTTP GET only
  - metadata was classified as `EvidenceKind.METADATA`
  - every observation remained `READ_ONLY`
  - every evidence object remained tenant-bound
  - source identification was preserved
  - requested DocType binding was preserved
  - metadata contents were not printed during verification
  - no execution path was invoked
- Permission-boundary verification:
  - direct `DocType` enumeration returned HTTP 403 and remains unavailable
  - ORION did not receive broader ERP administrative permission
  - structural discovery uses the permission-aware Frappe metadata method for explicitly configured DocTypes
- Security:
  - no ERP write capability was added
  - redirects remain rejected
  - response size remains bounded
  - credentials and customer endpoint remain outside Git
