# ORION Engineering Contract

- `laboratory/orion-v0.1` is the canonical laboratory branch. Work in the active ORION repository and worktree, confirm the current branch and base before editing, and preserve unrelated work.
- ChatGPT is the architecture, adjudication, and orchestration authority. Codex implements only explicitly scoped GitHub issues on feature branches; the issue body is the implementation contract.
- Do not widen scope, invent adjacent work, merge, or promote changes. Human maintainers retain merge and promotion authority.
- Do not access customer or live ERP systems or networks unless a separate ledger explicitly authorizes that access. Never persist or print credentials or customer identifiers.
- Deterministic tests are the verification authority. Before reporting completion, run `py_compile`, Ruff, the full pytest suite, the demo, `git diff --check`, and source/capability scans.
- The demo must retain `execution_allowed=false` unless a future explicitly authorized milestone changes that doctrine.
- Preserve every existing stash; never reset, pop, or drop it.
- Prefer the smallest semantic owner, composition, and one authoritative implementation of each semantic rule. Do not duplicate semantics.
- Another ERP adds an adapter, not another ORION.
- Observation is not prediction. Learning is not execution. Capability is not permission.
