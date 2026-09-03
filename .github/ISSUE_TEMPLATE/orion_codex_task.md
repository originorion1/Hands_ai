---
name: ORION Codex implementation task
about: Define a bounded ORION implementation contract for Codex
title: "Codex: "
labels: ""
assignees: ""
---

## Objective

<!-- State one bounded outcome. -->

## Canonical base SHA

<!-- Full SHA. -->

## Branch

`codex/`

## Semantic invariants

- Observation is not prediction.
- Learning is not execution.
- Capability is not permission.

## Required implementation

<!-- List only work required for this issue. -->

## Tests

<!-- Specify new or changed deterministic tests. -->

## Guardrails

- No customer or live-system access.
- No ERP or other network access.
- No credentials or customer identifiers.
- No merge or promotion by Codex.
- Do not widen scope or disturb existing stashes.

## Verification

- [ ] `py_compile`
- [ ] Ruff
- [ ] Full pytest suite
- [ ] Demo (`execution_allowed=false`)
- [ ] `git diff --check`
- [ ] Source/capability scans

## Report format

- Full head SHA:
- Exact base SHA:
- Changed files:
- Total tests and result:
- Ruff:
- `py_compile`:
- Demo:
- Diff check:
- Source/capability scans:
- Customer/network/write access: no
- Persistence/schema changes:
- Merge performed: no
